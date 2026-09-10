"""Накопление статистики боя и сегментация боёв.

Ключевые решения, которые определяют, какие цифры увидит человек:

* DPS считается двумя способами сразу. avg — сумма урона, делённая на
  АКТИВНОЕ время игрока (отрезки, между которыми не больше active_gap
  секунд). now — скользящее окно dps_window. Разница между ними на одних и
  тех же данных доходит до 20 раз, поэтому показываем обе цифры.
* Гранулярность лога — одна секунда. Последняя секунда файла всегда неполная,
  поэтому в скользящее окно она не входит: иначе "текущий DPS" дёргается.
* Конец боя определяется тремя сигналами, а не одним таймаутом: тишина,
  смерть цели (строка опыта) и смена доминирующей цели. Одного таймаута мало —
  фарм на манекене идёт часами без единого перерыва.
"""

from __future__ import annotations

import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from . import skilldb
from .parser import SELF

DAMAGE, HEAL, TAKEN, LOOT = "damage", "heal", "taken", "loot"

#: Периодический урон: владельца эффекта в строке лога физически нет.
UNATTRIBUTED = "(periodic)"

#: Хил, у которого клиент не назвал автора. Отдельной строкой, а не на
#: игроке: приписывать чужое себе хуже, чем честно сказать «неизвестно».
UNKNOWN_HEALER = "(unknown healer)"

def is_player_name(name: str) -> bool:
    """Имя персонажа игрока в Aion — всегда одно слово, без пробелов.

    Этого хватает, чтобы отделить игроков от мобов, петов и NPC: проверено
    на живом логе — из 526 акторов урона 111 содержат пробел, и среди них
    нет ни одного игрока (мобы, суммоны, NPC), а все 14 согруппников —
    без пробела.
    """
    return " " not in name


#: Названия группового канала чата в разных локализациях клиента.
PARTY_CHANNELS = {"Group", "Party", "Группа", "Gruppe", "Groupe", "Grupo"}


@dataclass
class Actor:
    name: str
    total: int = 0
    hits: int = 0
    crits: int = 0
    skill_hits: int = 0
    max_hit: int = 0
    per_sec: dict[int, int] = field(default_factory=dict)
    runs: list[list[int]] = field(default_factory=list)   # активные отрезки [начало, конец]
    skills: Counter = field(default_factory=Counter)
    #: Что игрок ПРИМЕНЯЛ помимо урона: бафы, дебафы, контроль, а у
    #: себя ещё банки и свитки. Ключ -> сколько раз.
    buffs: Counter = field(default_factory=Counter)
    #: Урон по каждой цели отдельно — из этого считается «только босс».
    #: Значение — изменяемая пара [урон, удары]: обычный dict со списком
    #: вместо двух Counter'ов выбран по замеру. На разборе всего лога
    #: (2,7 млн событий) пара Counter'ов стоила 80 секунд из 186, список с
    #: одним поиском по ключу — около 4. Цель у строки урона есть всегда, а
    #: за сутки фарма их набегают тысячи, поэтому число ключей ограничено:
    #: сверх потолка новые цели не заводятся, но общий итог не меняется.
    by_target: dict[str, list[int]] = field(default_factory=dict)
    #: Последние секунды: (секунда, урон). Только для скользящего окна.
    recent: list[list[int]] = field(default_factory=list)

    #: Потолок числа целей в разрезе и сколько оставлять при чистке.
    #: Прежняя версия при переполнении просто отказывалась заводить новые
    #: цели — и теряла ровно ту, ради которой разрез и нужен. Замер на
    #: живом логе: за сутки фарма потолок выбирается мусорными мобами, и
    #: пришедший потом босс в разрез уже не попадает: 267 244 урона по
    #: нему показывались как ноль. Теперь при переполнении выбрасываем
    #: САМЫЕ СЛАБЫЕ цели: босс по определению среди сильных, и вылететь
    #: он не может. Чистка идёт пачкой, поэтому стоит O(1) на удар.
    TARGET_CAP = 512
    TARGET_KEEP = 256

    def add(self, ts: int, amount: int, crit: bool, skill: str, active_gap: int,
            target: str = "") -> None:
        self.total += amount
        self.hits += 1
        if target:
            slot = self.by_target.get(target)
            if slot is not None:
                slot[0] += amount
                slot[1] += 1
            else:
                if len(self.by_target) >= self.TARGET_CAP:
                    self._trim_targets()
                self.by_target[target] = [amount, 1]
        if amount > self.max_hit:
            self.max_hit = amount
        if crit:
            self.crits += 1
        if skill:
            self.skill_hits += 1
            self.skills[skill] += amount
        self.per_sec[ts] = self.per_sec.get(ts, 0) + amount
        if self.recent and self.recent[-1][0] == ts:
            self.recent[-1][1] += amount
        else:
            self.recent.append([ts, amount])
            # Подрезаем хвост, только когда он реально отрос: сравнение
            # дешевле, чем срез списка на каждом ударе.
            if ts - self.recent[0][0] > self.RECENT_SPAN:
                cut = ts - self.RECENT_SPAN
                first = 0
                while first < len(self.recent) and self.recent[first][0] < cut:
                    first += 1
                del self.recent[:first]
        if self.runs and ts - self.runs[-1][1] <= active_gap:
            if ts > self.runs[-1][1]:
                self.runs[-1][1] = ts
        else:
            self.runs.append([ts, ts])

    def _trim_targets(self) -> None:
        """Оставить только самые крупные цели.

        Выбрасываем мелочь, а не последнюю пришедшую: единственная цель,
        которая обязана уцелеть, — это та, по которой набито больше всего,
        то есть босс. Чистим сразу до половины потолка, поэтому на каждый
        удар приходится в среднем ничтожная доля этой работы.
        """
        top = sorted(self.by_target.items(), key=lambda kv: -kv[1][0])
        self.by_target = dict(top[:self.TARGET_KEEP])

    @property
    def active_seconds(self) -> int:
        # +1, иначе бой внутри одной секунды даёт деление на ноль
        return sum(b - a + 1 for a, b in self.runs) or 1

    @property
    def avg_dps(self) -> float:
        return self.total / self.active_seconds

    #: Сколько секунд держать в кольце. Больше максимального окна DPS
    #: (60 с по настройкам) с запасом на паузу между тиками.
    RECENT_SPAN = 75

    def dps_now(self, now: int, window: int) -> float:
        # Верхняя граница now-1: последняя секунда файла ещё пишется.
        # Считаем по кольцу последних секунд, а не по всей раскладке
        # сессии: замер показал 263 тысячи записей в per_sec на миллионе
        # событий, и полный перебор ради десятисекундного окна делался
        # четыре раза в секунду для каждого актора.
        lo, hi = now - window, now - 1
        if hi < lo:
            return 0.0
        s = 0
        for t, v in self.recent:
            if lo <= t <= hi:
                s += v
        return s / window

    @property
    def crit_pct(self) -> float | None:
        return 100.0 * self.crits / self.hits if self.hits else None

    def on_target(self, target: str) -> tuple[int, int]:
        """Урон и удары по одной цели — (0, 0), если по ней не били."""
        slot = self.by_target.get(target)
        return (slot[0], slot[1]) if slot else (0, 0)

    def top_targets(self, limit: int = 8) -> list[tuple[str, int]]:
        """Цели по убыванию урона. Считается только по запросу: в снимке,

        который строится четыре раза в секунду по всем акторам сразу, такая
        сортировка стоила бы дороже, чем всё остальное вместе взятое.
        """
        rows = sorted(self.by_target.items(), key=lambda kv: -kv[1][0])
        return [(name, slot[0]) for name, slot in rows[:limit]]


class Encounter:
    """Один бой."""

    def __init__(self, start: int, cfg: dict):
        self.cfg = cfg
        self.start = start
        self.last = start
        self.actors: dict[str, dict[str, Actor]] = {
            DAMAGE: {}, HEAL: {}, TAKEN: {},
        }
        self.targets: Counter = Counter()
        #: Цель -> [первый удар, последний удар]. Нужно для скорости
        #: убийства: длительность БОЯ считается от первого события вообще,
        #: а честный парс по боссу — от первого удара именно по нему.
        self.target_seen: dict[str, list[int]] = {}
        #: Убитые: (имя, когда). Раньше лежали одни имена, и сказать,
        #: за сколько положили босса, было нельзя.
        self.kills: list[tuple[str, int]] = []

    def actor(self, metric: str, name: str) -> Actor:
        table = self.actors[metric]
        a = table.get(name)
        if a is None:
            a = table[name] = Actor(name)
        return a

    @property
    def dominant(self) -> str:
        return self.targets.most_common(1)[0][0] if self.targets else ""

    @property
    def duration(self) -> int:
        return self.last - self.start + 1

    def note_target(self, name: str, ts: int) -> None:
        """Отметить, что по цели ударили в этот момент."""
        seen = self.target_seen.get(name)
        if seen is None:
            # Потолок тот же, что у разреза по целям: за осаду их набегают
            # тысячи, а интересны те, по кому реально били.
            if len(self.target_seen) >= 512:
                return
            self.target_seen[name] = [ts, ts]
        else:
            seen[1] = ts

    def kill_time(self, name: str) -> int | None:
        """Когда цель убили. None, если её не убивали."""
        for who, ts in reversed(self.kills):
            if who == name:
                return ts
        return None

    def time_to_kill(self, name: str) -> int | None:
        """Сколько секунд шёл бой с этой целью до её смерти.

        Считается от ПЕРВОГО удара по ней до строки опыта за неё, а не по
        длительности боя целиком: перед боссом почти всегда чистят аддов,
        и календарное время боя завышало бы скорость убийства.
        """
        killed = self.kill_time(name)
        seen = self.target_seen.get(name)
        if killed is None or seen is None:
            return None
        return max(1, killed - seen[0] + 1)


class Meter:
    """Состояние метра: ростер, петы, мобы, текущий бой."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.self_name = cfg.get("self_name") or ""
        self.party: set[str] = set()
        # Ростер, достроенный из боя. Строка "X received N damage from Y"
        # существует в клиенте ТОЛЬКО для согруппников и своих петов
        # (STR_MSG_COMBAT_PARTY_ENEMY_ATTACK), у посторонних такого шаблона
        # нет вовсе. Это позволяет узнать группу, даже если метр запустили,
        # когда группа уже собрана и событий входа он не видел.
        self.party_seen: set[str] = set()
        #: Ручная правка ростера из меню: последнее слово всегда за человеком.
        self.party_manual: set[str] = set()
        self.party_excluded: set[str] = set()
        self.pets: set[str] = set()
        #: Пет -> его хозяин. Раньше был просто набор имён, и правило
        #: «пет = мой» приписывало владельцу метра ЧУЖИХ спиритов: у двух
        #: спиритмастеров в группе питомцы зовутся одинаково. Теперь
        #: владельца берём из строки саммона, а при совпадении имён
        #: побеждает тот, кто призвал позже, — как и с дотами.
        self.pet_owner: dict[str, str] = {}
        self.mobs: set[str] = set()
        self.hostiles: set[str] = set()
        self.encounter: Encounter | None = None
        self.session = Encounter(0, cfg)     # накапливает всё, не закрывается
        self.history: list[Encounter] = []
        self.pending_close = 0
        self.last_ts = 0
        #: Когда в последний раз кто-то кого-то бил или лечил.
        #: По нему отличаем настоящий дроп от взятого со склада.
        self.last_combat_ts = 0
        self.stats = Counter()
        #: Добыча за сессию: опыт, AP, кинах, убийства, смерти.
        self.loot: Counter = Counter()
        #: Кто последним наложил эффект на цель: (скилл, цель) -> игрок.
        #: Тик дота автора не содержит вообще, поэтому владельца приходится
        #: помнить с момента наложения. «Последний наложивший» — это не
        #: догадка, а механика игры: новый дот перебивает старый, и тикает
        #: именно последний. Если два сорка бьют одним и тем же скиллом,
        #: урон уходит тому, кто наложил позже, — как в самой игре.
        self.effect_owner: dict[tuple[str, str], str] = {}
        #: Лут: игрок -> предмет -> количество. Плюс броски кубика.
        self.items: dict[str, Counter] = {}
        self.rolls: list[tuple[str, int]] = []
        #: Скилл -> код класса. Собирается из клиента, может быть пустой.
        self.skill_class: dict[str, str] = {}
        #: Голоса за класс по каждому актору: скиллы у классов не пересекаются.
        self.class_votes: dict[str, Counter] = {}
        #: Скилл -> кто им бил. Нужно, чтобы отличить вампиризм от хила:
        #: у вампирских скиллов лечится тот же, кто наносит удар.
        self.skill_users: dict[str, set] = {}
        #: Ник подтверждён однозначной строкой, а не эвристикой.
        self.self_confirmed = bool(cfg.get("self_name"))
        self._own_chat: Counter = Counter()
        #: Свои попадания, уже учтённые в текущей секунде: ключ -> форма записи.
        #: Нужно, чтобы не посчитать дважды одно попадание, записанное и от
        #: первого лица, и по нику. Живёт ровно одну секунду — см. _is_echo.
        self._echo: dict[tuple, str] = {}
        self._echo_ts = 0
        # Класс из прошлой сессии: снимает холодный старт разбора хила.
        seed = cfg.get("self_class")
        if seed:
            self.class_votes[SELF] = Counter({seed: 1})

    # -- служебное --

    def reset(self) -> None:
        if self.encounter is not None:
            self.history.append(self.encounter)
        self.encounter = None
        self.session = Encounter(0, self.cfg)
        self.loot.clear()
        self.effect_owner.clear()
        self.skill_users.clear()
        self.items.clear()
        self.rolls.clear()
        self.pending_close = 0

    def _dot_owner(self, effect: str, target: str) -> str:
        """Кому приписать тик периодического урона.

        Порядок: точное совпадение по последнему наложившему -> то же без
        суффикса « Effect» -> единственный игрок подходящего класса в бою.
        Последнее — потому что часть скиллов (Lava Tsunami, Lava Tempest)
        тикает вообще без строки прямого удара, и автора взять неоткуда,
        но класс скилла нам известен из базы клиента.
        """
        for key in (effect, effect.removesuffix(" Effect")):
            owner = self.effect_owner.get((key, target))
            if owner:
                return owner
        code = self.skill_class.get(effect) or             self.skill_class.get(effect.removesuffix(" Effect"))
        if not code:
            return ""
        of_class = [n for n in self.class_votes if self.actor_class(n) == code]
        return of_class[0] if len(of_class) == 1 else ""

    def actor_class(self, name: str) -> str:
        """Код класса по использованным скиллам или '' если не определён."""
        votes = self.class_votes.get(name)
        if not votes:
            return ""
        code = votes.most_common(1)[0][0]
        if name == SELF and code and self.cfg.get("self_class") != code:
            # Запоминаем свой класс в настройках. Без этого в начале каждой
            # сессии он неизвестен, и правило разбора хила (см. _heal_owner)
            # успевает приписать игроку чужие хилы: на живом логе класс
            # определялся только к 699-му событию.
            self.cfg["self_class"] = code
        return code

    def set_party(self, name: str, is_party: bool) -> None:
        """Ручное отнесение игрока к группе или к посторонним."""
        if is_party:
            self.party_manual.add(name)
            self.party_excluded.discard(name)
        else:
            self.party_excluded.add(name)
            self.party_manual.discard(name)
            self.party.discard(name)
            self.party_seen.discard(name)

    def _add(self, metric: str, name: str, ts: int, amount: int,
             crit: bool, skill: str, target: str = "") -> None:
        """Пишем и в текущий бой, и в счётчик за всю сессию."""
        gap = self.cfg["active_gap"]
        enc = self._ensure(ts)
        enc.actor(metric, name).add(ts, amount, crit, skill, gap, target)
        if not self.session.start:
            self.session.start = ts
        self.session.last = max(self.session.last, ts)
        self.session.actor(metric, name).add(ts, amount, crit, skill, gap, target)

    def _owner(self, name: str) -> str:
        """Питомца схлопываем во владельца, свой ник — в «You».

        Свой урон клиент пишет двумя разными шаблонами: от первого лица
        («You inflicted N damage on X») и от третьего, по нику
        («Steepeek inflicted N damage on X»). Без склейки игрок видел себя
        в таблице дважды — это и есть та самая лишняя строка.
        """
        if self.cfg.get("merge_pets", True) and name in self.pets:
            owner = self.pet_owner.get(name, SELF)
            if self.self_name and owner == self.self_name:
                return SELF
            return owner
        if self.self_name and name == self.self_name:
            return SELF
        return name

    def _is_echo(self, ev) -> bool:
        """Второй экземпляр собственного попадания, записанный другой формой.

        В группе одно попадание попадает в лог дважды — и «You inflicted», и
        «<ник> inflicted», с точностью до значения, цели и скилла. Замер на
        живом логе: 359 из 397 строк с ником (90 %) имеют такого близнеца.
        Просто склеить ники мало — урон удвоится.

        Отличаем эхо от настоящего двойного удара по ФОРМЕ записи: повтор
        той же формы в ту же секунду — это два реальных попадания, а вот
        та же цифра, пришедшая ДРУГОЙ формой, — эхо. Значение, цель и
        скилл совпадают у близнецов всегда, поэтому ключа из них хватает.
        """
        if not self.self_name:
            return False
        if ev.actor != SELF and ev.actor != self.self_name:
            return False
        if ev.ts != self._echo_ts:
            self._echo_ts = ev.ts
            self._echo.clear()
        key = (ev.amount, ev.target, ev.skill, ev.crit)
        seen = self._echo.get(key)
        if seen is None:
            self._echo[key] = ev.actor
            return False
        return seen != ev.actor

    def _add_buff(self, who: str, name: str) -> None:
        """Отметить применение эффекта или предмета в обеих таблицах.

        И в текущем бою, и в сессии: раскрытая строка показывает то же, что
        и остальные её цифры, а они берутся из выбранного среза.
        """
        for enc in (self._ensure(self.last_ts), self.session):
            enc.actor(DAMAGE, who).buffs[name] += 1

    def _heal_owner(self, ev) -> str:
        """Кому записать хил, когда в строке автор не назван.

        Форма «You restored N of X's HP by using S» в клиенте отвечает сразу
        трём семействам шаблонов, и текст у них совпадает до символа: мой
        собственный хил, тик ЧУЖОГО хота и ЧУЖОЙ вампиризм. Слота для автора
        во втором и третьем нет вовсе — клиент подставляет «You».

        Замер на живом логе рейнджера, который никого не лечил: таких строк
        4811, при этом строк «X recovered N HP because you used S», то есть
        заведомо своих, — ноль. Все 4811 приписывались игроку.

        Автора из соседних строк не достать: явная пара нашлась у 4 строк из
        4811. Поэтому разбираем по данным клиента, сверху вниз:
        """
        if not ev.unsure:
            return self._owner(ev.actor)

        # 1. Вампиризм: названный сам бьёт этим же скиллом, значит лечит себя.
        #    Проверено на логе — «Lamenace inflicted ... by using Exhausting
        #    Wave I» в ту же секунду, что и хил «of Lamenace's HP».
        if ev.target in self.skill_users.get(ev.skill, ()):
            return self._owner(ev.target)

        code = self.skill_class.get(ev.skill, "")
        # 2. Скилла нет в таблице классов — это зелье или предмет, а их пьют
        #    себе. Названный и есть тот, кто вылечился.
        if not code:
            return self._owner(ev.target)

        mine = self.actor_class(SELF)
        # 3. Свой класс ещё не определён (например, хилер вообще не бил) —
        #    отбирать у него хил нельзя, оставляем как было.
        if not mine:
            return self._owner(ev.actor)

        # 4. Скилл чужого класса — точно не мой, но кто именно, лог не говорит.
        if code != mine:
            return UNKNOWN_HEALER
        return self._owner(ev.actor)

    def _loot_out_of_combat(self, ts: int) -> bool:
        """Предмет взят вне боя — со склада, из почты, из ремесла?

        Клиент этого НЕ различает: и лут с моба, и взятое со склада, и
        вложение из письма приходят одной и той же строкой
        «You have acquired ...» (STR_MSG_GET_ITEM). Проверено по таблице
        шаблонов клиента и по живому логу: слова «warehouse» в нём нет
        вовсе, а «Mail has arrived» — только уведомление о письме, никак
        не связанное с моментом, когда вложение забирают.

        Покупки различать не нужно: у них своя строка «You have purchased»,
        и в добычу они не попадали никогда.

        Раз источник не написан, судим по обстановке: настоящий дроп падает
        во время боя или сразу после него, а склад и почта — в городе, где
        никто никого не бьёт. Окно намеренно щедрое: труп обыскивают не
        мгновенно.
        """
        if not self.cfg.get("loot_in_combat", True):
            return False
        # Проверять self.encounter нельзя: объект боя живёт до следующего
        # события и после получаса тишины всё ещё не None. Судим по времени
        # последнего удара — оно не врёт.
        window = max(0, int(self.cfg.get("loot_window", 25)))
        return ts - self.last_combat_ts > window

    def _close(self) -> None:
        if self.encounter is not None:
            self.history.append(self.encounter)
            if len(self.history) > 50:
                del self.history[:-50]
        self.encounter = None
        self.pending_close = 0

    def _ensure(self, ts: int) -> Encounter:
        cfg = self.cfg
        enc = self.encounter
        if enc is None:
            enc = self.encounter = Encounter(ts, cfg)
        elif ts - enc.last > cfg["encounter_timeout"]:
            self._close()
            enc = self.encounter = Encounter(ts, cfg)
        elif self.pending_close and ts > self.pending_close:
            self._close()
            enc = self.encounter = Encounter(ts, cfg)
        enc.last = max(enc.last, ts)
        return enc

    # -- приём событий --

    #: События, по которым видно, что бой идёт. Хил сюда входит: у клирика
    #: в бою может не быть ни одного удара, а добыча ему падает так же.
    COMBAT_KINDS = frozenset(("damage", "dot", "heal", "xp", "death", "pvp"))

    def feed(self, ev) -> None:
        self.stats["events"] += 1
        self.last_ts = max(self.last_ts, ev.ts)
        kind = ev.kind
        if kind in self.COMBAT_KINDS:
            self.last_combat_ts = max(self.last_combat_ts, ev.ts)

        if kind == "party":
            if ev.extra in ("join", "invite"):
                self.party.add(ev.target)
            elif ev.extra == "leave":
                self.party.discard(ev.target)
                self.party_seen.discard(ev.target)
            elif ev.extra == "disband":
                self.party.clear()
                self.party_seen.clear()
            return

        if kind == "summon":
            if ev.extra == "add":
                owner = ev.actor or SELF
                mine = self.pet_owner.get(ev.target) in (SELF, self.self_name)
                # Имена петов не уникальны: «Holy Servant» на живом логе
                # призывают 180 разных игроков. Поэтому чужой саммон НЕ
                # перехватывает пета, которого призвали мы, — иначе наш
                # спирит и наши ловушки уезжали в чужую строку до конца
                # боя. Своё имя всегда перебивает чужое.
                if owner in (SELF, self.self_name) or not mine:
                    self.pets.add(ev.target)
                    self.pet_owner[ev.target] = owner
            else:
                self.pets.discard(ev.target)
                self.pet_owner.pop(ev.target, None)
            return

        if kind == "login":
            # Вход в игру: за «You» МОЖЕТ стоять уже другой персонаж, но
            # доказательств этого пока нет. Ник не стираем — просто снимаем
            # с него отметку «подтверждён», чтобы следующая однозначная
            # строка могла его заменить. Стирать было нельзя: без ника
            # ломается склейка своих строк, и одно попадание, записанное и
            # от первого лица, и по нику, считается дважды. Клиент к тому
            # же пишет эту строку парами и при каждой смене статуса, а не
            # только при заходе новым персонажем.
            if not self.cfg.get("self_name"):
                self.self_confirmed = False
                self._own_chat.clear()
            return

        if kind == "chat":
            if self.cfg.get("self_name"):
                return
            if ev.extra == "glory" and ev.actor:
                # Единственная строка, где свой ник стоит однозначно.
                self.self_name = ev.actor
                self.self_confirmed = True
            elif ev.target == SELF and ev.actor and not self.self_confirmed:
                # Реплика без обёртки [charname:] — признак СЛАБЫЙ: у чужих
                # такие строки тоже встречаются. Берём только явно
                # преобладающее имя, иначе подхватим случайного человека.
                self._own_chat[ev.actor] += 1
                top = self._own_chat.most_common(2)
                if top[0][1] >= 3 and (len(top) == 1 or top[0][1] >= 2 * top[1][1]):
                    self.self_name = top[0][0]
            elif ev.extra in PARTY_CHANNELS and ev.actor:
                self.party_seen.add(ev.actor)
            return

        if kind == "loot":
            self.loot[ev.extra] += ev.amount
            return

        if kind == "pvp":
            if ev.actor == SELF or (self.self_name and ev.actor == self.self_name):
                self.loot["pvp_kills"] += 1
            elif ev.target == SELF or (self.self_name and ev.target == self.self_name):
                self.loot["pvp_deaths"] += 1
            return

        if kind == "xp":
            self.loot["exp"] += ev.amount
            self.loot["kills"] += 1
            # За убитого в PvP игрока опыт тоже даётся — по строке опыта
            # мобом его считать нельзя, иначе он пропадёт из таблицы.
            if not is_player_name(ev.target):
                self.mobs.add(ev.target)
            self.session.kills.append((ev.target, ev.ts))
            enc = self.encounter
            if enc is not None:
                enc.kills.append((ev.target, ev.ts))
                self.pending_close = ev.ts + self.cfg["kill_grace"]
            return

        if kind == "death":
            if ev.target == SELF or (self.self_name and ev.target == self.self_name):
                self.loot["deaths"] += 1
            self.mobs.discard(ev.target)
            return

        if kind == "heal":
            if not ev.actor or ev.amount <= 0 or ev.extra == "MP":
                return
            self._add(HEAL, self._heal_owner(ev), ev.ts, ev.amount, False, ev.skill)
            return

        if kind == "applied":
            if ev.skill and ev.actor:
                self.effect_owner[(ev.skill, ev.target)] = ev.actor
                self._add_buff(self._owner(ev.actor), ev.skill)
            return

        if kind == "used_item":
            # Расходники видны ТОЛЬКО свои: строки «X has used <предмет>»
            # в клиенте не существует, у согруппников их не увидеть никак.
            if ev.skill:
                self._add_buff(self._owner(ev.actor), ev.skill)
            return

        if kind == "loot_item":
            if self._loot_out_of_combat(ev.ts):
                return
            # Через _owner: свой ник должен схлопываться в «You», как в
            # уроне. Без этого игрок видел в добыче ДВЕ свои строки —
            # «You have acquired» и «<ник> has acquired» идут разными
            # шаблонами клиента, ровно как в боевых сообщениях.
            self.items.setdefault(self._owner(ev.actor), Counter())[ev.target] += ev.amount
            return

        if kind == "roll":
            self.rolls.append((ev.actor, ev.amount))
            if len(self.rolls) > 200:
                del self.rolls[:-200]
            return

        if kind == "dot":
            if ev.amount <= 0:
                return
            # Тик по СВОИМ — это входящий урон, а не наш: доты боссов по
            # группе раньше шли в общий урон и завышали его. Проверяем именно
            # принадлежность к своим, а не форму имени: односложные имена
            # мобов («Suga») по форме неотличимы от ников.
            if ev.target == SELF or ev.target in self.party                     or ev.target in self.party_seen or ev.target in self.party_manual:
                self._add(TAKEN, self._owner(ev.target), ev.ts, ev.amount, False, "")
                return
            enc_dot = self._ensure(ev.ts)
            enc_dot.targets[ev.target] += ev.amount
            enc_dot.note_target(ev.target, ev.ts)
            self.session.targets[ev.target] += ev.amount
            self.session.note_target(ev.target, ev.ts)
            # Строка «X received N poisoning damage after you used S»
            # называет автора прямо — угадывать по эффекту и классу тут
            # нечего. Заодно запоминаем владельца: следующие тики того же
            # эффекта придут уже безымянными.
            if ev.actor:
                owner = ev.actor
                if ev.extra:
                    self.effect_owner[(ev.extra, ev.target)] = owner
            else:
                owner = self._dot_owner(ev.extra, ev.target)
            self._add(DAMAGE, self._owner(owner) if owner else UNATTRIBUTED,
                      ev.ts, ev.amount, False, ev.extra if owner else "",
                      ev.target)
            return

        if kind != "damage" or ev.amount <= 0:
            return

        # Урон щита-отражателя не является уроном игрока, и хуже того — клиент
        # печатает в этих строках фиктивные числа. На аое Модор («Grendel's
        # Explosive Temper») ВСЕ участники разом получают ровно 6 553 601, что
        # больше всего их реального урона за бой. Одна такая строка ломала
        # таблицу целиком, поэтому по умолчанию отражение не считаем.
        if ev.extra == "reflect" and not self.cfg.get("count_reflect", False):
            return

        if self._is_echo(ev):
            return

        if ev.incoming:
            # Кто-то получил урон. Атакующего запоминаем как враждебного.
            if ev.actor:
                self.hostiles.add(ev.actor)
            if ev.target != SELF and ev.target not in self.pets:
                self.party_seen.add(ev.target)
            self._add(TAKEN, self._owner(ev.target), ev.ts, ev.amount, ev.crit,
                      ev.skill, ev.actor)
            return

        # Урон по кому-то из наших — значит бьёт враг.
        if ev.target == SELF or ev.target in self.party:
            self.hostiles.add(ev.actor)
            self._add(TAKEN, self._owner(ev.target), ev.ts, ev.amount, ev.crit,
                      ev.skill, ev.actor)
            return

        if ev.skill:
            # Запоминаем автора скилла на этой цели: тик дота автора не несёт
            self.effect_owner[(ev.skill, ev.target)] = ev.actor
            self.skill_users.setdefault(ev.skill, set()).add(ev.actor)
            if self.skill_class:
                code = self.skill_class.get(ev.skill)
                if code:
                    self.class_votes.setdefault(ev.actor, Counter())[code] += 1

        enc = self._ensure(ev.ts)
        enc.targets[ev.target] += ev.amount
        enc.note_target(ev.target, ev.ts)
        self.session.targets[ev.target] += ev.amount
        self.session.note_target(ev.target, ev.ts)
        self._maybe_split_on_target(enc, ev)
        self._add(DAMAGE, self._owner(ev.actor), ev.ts, ev.amount, ev.crit,
                  ev.skill, ev.target)

    def _maybe_split_on_target(self, enc: Encounter, ev) -> None:
        """Смена доминирующей цели — третий сигнал конца боя."""
        dom = enc.dominant
        if not dom or dom == ev.target:
            return
        if enc.targets[ev.target] > 2 * enc.targets[dom] and ev.ts - enc.start > 5:
            self._close()
            self.encounter = Encounter(ev.ts, self.cfg)
            self.encounter.last = ev.ts

    # -- вывод --

    def section_of(self, name: str) -> str | None:
        """В какую секцию попадает актор: 'party', 'other' или None (скрыть).

        Своих (себя, петов и согруппников) держим отдельно от посторонних:
        по строке урона они неотличимы, но ростер группы мы ведём сами, и
        мешать в одну таблицу свою группу и случайных людей рядом бесполезно.
        """
        scope = self.cfg.get("scope", "split")
        if name == SELF or name in self.pets or (self.self_name and name == self.self_name):
            return "party"
        if name in self.party_excluded:
            return None if scope == "party" else "other"
        if name in self.party or name in self.party_seen or name in self.party_manual:
            return "party"
        if scope == "party":
            return None
        # Служебные строки без владельца отсеиваться как мобы не должны:
        # в их именах есть пробелы, и правило is_player_name их скрывает.
        if name in (UNATTRIBUTED, UNKNOWN_HEALER):
            return "other"
        if self.cfg.get("hide_mobs", True):
            if not is_player_name(name) or name in self.mobs or name in self.hostiles:
                return None
        return "other"

    def _loot_rows(self) -> list[dict]:
        """Строки для вкладки «Добыча»: игрок -> сколько предметов подобрал.

        Раскрытие строки переиспользует тот же механизм, что и разбор по
        скиллам: список пар (подпись, количество). Названия резолвятся здесь,
        чтобы отрисовка не знала про базу предметов.
        """
        from . import itemdb
        rows = []
        for who, bag in self.items.items():
            if not bag:
                continue
            entries, quality, ids = [], {}, {}
            for raw, count in bag.most_common():
                item_id = ""
                if raw.startswith("[item:"):
                    # Клиент пишет две формы: "[item:167000522;ver6;;;;]" и
                    # короткую "[item:186000938]". Резать только по ";" мало —
                    # в короткой форме в номере оставалась скобка, и название
                    # не находилось. Замер: 78 % против 100 % после правки.
                    item_id = raw[6:].split(";", 1)[0].rstrip("]").strip()
                name, qual, _grp = itemdb.lookup(item_id) if item_id else ("", "", "")
                label = name or (f"item {item_id}" if item_id else raw)
                entries.append((label, count))
                quality[label] = qual
                # Иконка ищется по номеру, а не по названию: у предметов своя
                # таблица, и названия там не уникальны.
                ids[label] = item_id
            rows.append({
                "name": who, "display": who, "total": sum(bag.values()),
                "hits": len(bag), "dps": 0.0, "avg": 0.0, "crit": None, "max": 0,
                "cls": self.actor_class(who), "section": "party",
                "cls_name": skilldb.CLASSES.get(self.actor_class(who), ""),
                "is_self": who == SELF, "is_party": who in self.party,
                "skills": entries, "quality": quality, "ids": ids,
                "buffs": [],
            })
        return rows

    #: Сколько скиллов и целей кладём в файл сессии на одного игрока.
    #: Хвост длиннее этого в отчёте не нужен, а файл от него пухнет.
    EXPORT_SKILLS = 40
    EXPORT_TARGETS = 12

    def export(self) -> dict | None:
        """Сессия целиком — в простой словарь, готовый лечь в JSON.

        Возвращает None, если считать нечего: пустую сессию сохранять
        незачем, а нажатие «Очистить» на пустом метре — обычное дело.
        """
        enc = self.session
        if not enc.start or not any(enc.actors[m] for m in (DAMAGE, HEAL, TAKEN)):
            return None

        def dump(metric: str) -> list[dict]:
            out = []
            for name, a in enc.actors[metric].items():
                if not a.total:
                    continue
                out.append({
                    "name": name,
                    "cls": self.actor_class(name),
                    "total": a.total,
                    "hits": a.hits,
                    "crits": a.crits,
                    "max": a.max_hit,
                    "active": a.active_seconds,
                    "avg": round(a.avg_dps, 1),
                    "skills": a.skills.most_common(self.EXPORT_SKILLS),
                    "targets": a.top_targets(self.EXPORT_TARGETS),
                })
            out.sort(key=lambda r: -r["total"])
            return out

        boss = enc.dominant
        rows = dump(DAMAGE)
        fights = self.export_fights()
        mine = next((r for r in rows if r["name"] == SELF), None)
        return {
            "start": enc.start,
            "end": enc.last,
            "duration": enc.duration,
            "self_name": self.self_name,
            "self_class": self.actor_class(SELF),
            "boss": boss,
            "kills": [list(k) for k in enc.kills[-200:]],
            "kill_count": len(enc.kills),
            "fights": fights,
            "targets": enc.targets.most_common(20),
            "loot": dict(self.loot),
            "party": sorted((self.party | self.party_seen | self.party_manual)
                            - self.party_excluded),
            "total": sum(r["total"] for r in rows),
            "you": {
                "total": mine["total"] if mine else 0,
                "avg": mine["avg"] if mine else 0.0,
                "boss": dict(mine["targets"]).get(boss, 0) if mine else 0,
            },
            "damage": rows,
            "heal": dump(HEAL),
            "taken": dump(TAKEN),
        }

    #: Сколько боёв класть в файл сессии и сколько игроков в каждый.
    EXPORT_FIGHTS = 60
    EXPORT_FIGHT_PLAYERS = 24

    def export_fights(self) -> list[dict]:
        """Отдельные бои сессии — то, из чего потом строится рейтинг.

        Сессия целиком для рейтинга не годится: сравнивать надо один бой с
        одним боссом, а не вечер фарма. Поэтому здесь у каждого боя своя
        цель, свои участники и, главное, СКОРОСТЬ убийства — время от
        первого удара по цели до строки опыта за неё.
        """
        out = []
        fights = list(self.history)
        if self.encounter is not None:
            fights.append(self.encounter)
        for enc in fights[-self.EXPORT_FIGHTS:]:
            boss = enc.dominant
            if not boss or not enc.start:
                continue
            killed_at = enc.kill_time(boss)
            seen = enc.target_seen.get(boss) or [enc.start, enc.last]
            players = []
            for name, a in enc.actors[DAMAGE].items():
                on_boss, hits_boss = a.on_target(boss)
                if not on_boss:
                    continue
                players.append({
                    "name": name,
                    "cls": self.actor_class(name),
                    "total": on_boss,
                    "hits": hits_boss,
                    "all_targets": a.total,
                })
            if not players:
                continue
            players.sort(key=lambda r: -r["total"])
            span = enc.time_to_kill(boss) or max(1, seen[1] - seen[0] + 1)
            out.append({
                "boss": boss,
                "start": seen[0],
                "end": killed_at if killed_at is not None else seen[1],
                # Календарные секунды от первого удара по цели до её смерти.
                # Именно эту величину сравнивают между собой: «за сколько
                # положили» — и по ней же считается DPS для рейтинга.
                "seconds": span,
                "killed": killed_at is not None,
                "total": sum(r["total"] for r in players),
                "players": players[:self.EXPORT_FIGHT_PLAYERS],
            })
        return out

    def snapshot(self, metric: str | None = None, whole: bool | None = None) -> dict:
        cfg = self.cfg
        metric = metric or cfg.get("metric", DAMAGE)
        if whole is None:
            whole = cfg.get("mode", "session") == "session"
        enc = self.session if whole else self.encounter
        window = cfg["dps_window"]
        now = self.last_ts

        # «Только босс» — это фильтр по ГЛАВНОЙ цели среза, а не отдельная
        # метрика: у босса и у аддов урон пишется одними и теми же строками,
        # разделяет их только имя цели. Работает на уроне; на хиле и на
        # полученном уроне главной цели нет по смыслу.
        boss = enc.dominant if enc is not None else ""
        boss_only = bool(cfg.get("boss_only")) and metric == DAMAGE and bool(boss)

        # Итоги первого прохода живут между вызовами, поэтому сбрасываем
        # их здесь: иначе снимок вкладки добычи показал бы суммы с вкладки
        # урона, посчитанные предыдущим вызовом.
        self._sec_totals = None
        self._light_total = None
        self._light_count = None

        rows = self._loot_rows() if metric == LOOT else []
        # Вкладка сессий живёт на диске, а не в памяти метра: строки для неё
        # собирает окно. Здесь важно только не искать её среди метрик боя —
        # иначе выбранная вкладка роняла бы снимок при следующем запуске.
        if metric not in (DAMAGE, HEAL, TAKEN, LOOT):
            enc = None
        if enc is not None and metric != LOOT:
            # Снимок строится в ДВА прохода. Первый — дешёвый: имя, секция
            # и итог, чтобы отсортировать и посчитать доли. Второй — полный,
            # только для строк, которые действительно окажутся в окне.
            # Раньше проход был один: на живом логе снимок собирал 2650
            # строк со всеми скиллами и бафами, чтобы показать двенадцать,
            # и стоил 70-110 мс четыре раза в секунду.
            light = []
            for name, a in enc.actors[metric].items():
                section = self.section_of(name)
                if section is None:
                    continue
                on_boss, hits_boss = a.on_target(boss) if boss else (0, 0)
                if boss_only and not on_boss:
                    # Ни одного удара по главной цели — в таблице «только
                    # босс» такой строке делать нечего.
                    continue
                light.append((name, a, section, on_boss, hits_boss,
                              on_boss if boss_only else a.total,
                              hits_boss if boss_only else a.hits))

            split_now = cfg.get("scope") == "split"
            if split_now:
                order_key = {"party": 0, "other": 1}
                light.sort(key=lambda r: (order_key[r[2]], -r[5], -r[6], r[0]))
            else:
                light.sort(key=lambda r: (-r[5], -r[6], r[0]))

            # Доли считаются по ВСЕМ строкам, поэтому суммы берём здесь,
            # пока полные словари ещё не построены.
            self._sec_totals = {}
            for item in light:
                self._sec_totals[item[2]] = self._sec_totals.get(item[2], 0) + item[5]

            limit = cfg.get("max_rows", 12)
            # Строку без владельца окно рисует отдельно, поэтому она нужна
            # всегда, даже если не попала в первые max_rows.
            visible = list(light[:limit])
            seen_names = {item[0] for item in visible}
            for item in light[limit:]:
                if item[0] in (UNATTRIBUTED, UNKNOWN_HEALER) and item[0] not in seen_names:
                    visible.append(item)
            self._light_total = sum(item[5] for item in light)
            self._light_count = len(light)

            for name, a, section, on_boss, hits_boss, row_total, row_hits in visible:
                # Свой персонаж подписан «You». Ник определяется и хранится
                # (он нужен, чтобы узнавать себя в строках вида «because
                # <Ник> used <скилл>»), но в таблицу не подставляется:
                # определённый ник бывает не тем, если играешь с твинка.
                display = name
                if name == SELF and self.self_name and cfg.get("show_own_nick"):
                    display = self.self_name
                cls = self.actor_class(name)
                rows.append({
                    "name": name,
                    "display": display,
                    "cls": cls,
                    "cls_name": skilldb.CLASSES.get(cls, ""),
                    "section": section,
                    # В режиме «только босс» и итог, и обе скорости считаются
                    # по одной цели. Скользящее окно там взять неоткуда —
                    # посекундная раскладка ведётся на игрока целиком, а не
                    # на каждую его цель, — поэтому обе колонки показывают
                    # одно и то же: урон по боссу на активное время. Врать
                    # «текущим» DPS, посчитанным по всем целям сразу, хуже.
                    "total": row_total,
                    "dps": (on_boss / a.active_seconds if boss_only
                            else a.dps_now(now, window)),
                    "avg": on_boss / a.active_seconds if boss_only else a.avg_dps,
                    "hits": row_hits,
                    "crit": a.crit_pct,
                    "max": a.max_hit,
                    #: Урон по главной цели — есть всегда, даже когда фильтр
                    #: выключен: по нему рисуется подпись «по боссу» в подвале.
                    "boss_total": on_boss,
                    "boss_hits": hits_boss,
                    "is_self": name == SELF or (bool(self.self_name) and name == self.self_name),
                    "is_party": name in self.party,
                    # ВСЕ скиллы, а не первые восемь. Обрезка тут была вдвойне
                    # вредной: мало того что список кончался на восьмом, так
                    # ещё и весь урон сверх него уезжал в строку «автоатака» —
                    # она считается как «итог минус перечисленное». На живом
                    # логе это давало автоатаке 38 % вместо настоящих 8 %.
                    "skills": a.skills.most_common(),
                    "buffs": a.buffs.most_common(),
                })

        split = cfg.get("scope") == "split"
        # Три ключа сортировки, иначе строки прыгают местами при равенстве.
        # Разбиение на секции — только когда его действительно попросили:
        # иначе список один и порядок строго по урону.
        if split:
            order = {"party": 0, "other": 1}
            rows.sort(key=lambda r: (order[r["section"]], -r["total"], -r["hits"], r["name"]))
            groups = [("party", [r for r in rows if r["section"] == "party"]),
                      ("other", [r for r in rows if r["section"] == "other"])]
        else:
            rows.sort(key=lambda r: (-r["total"], -r["hits"], r["name"]))
            groups = [("all", rows)]

        # Доля — от суммы своей группы строк, длина полосы — от лидера.
        # Суммы взяты из первого прохода: в rows лежат только видимые
        # строки, и считать проценты по ним значило бы врать — у каждого
        # вышло бы больше, чем на самом деле.
        sec_totals = self._sec_totals
        sections = []
        for key, part in groups:
            if not part:
                continue
            if sec_totals is not None and metric != LOOT:
                sec_total = sec_totals.get(key if key != "all" else None, 0)
                if key == "all":
                    sec_total = sum(sec_totals.values())
            else:
                sec_total = sum(r["total"] for r in part)
            sec_total = sec_total or 1
            top = part[0]["total"] or 1
            for r in part:
                r["pct"] = 100.0 * r["total"] / sec_total
                r["bar"] = r["total"] / top
            sections.append({"key": key, "total": sec_total, "count": len(part)})

        shown = rows[:cfg.get("max_rows", 12) + 2]
        return {
            "rows": shown,
            "sections": sections,
            "split": split,
            "hidden": max(0, (self._light_count if self._light_count is not None
                              else len(rows)) - cfg.get("max_rows", 12)),
            "total": (self._light_total if self._light_total is not None
                      else sum(r["total"] for r in rows)),
            "duration": enc.duration if enc and enc.start else 0,
            "target": boss,
            "boss": boss,
            "boss_only": boss_only,
            "kills": len(enc.kills) if enc else 0,
            # Сколько шёл бой с текущей целью до её смерти. None — ещё жива.
            "boss_seconds": enc.time_to_kill(boss) if (enc and boss) else None,
            "started": enc.start if enc else 0,
            "loot": dict(self.loot),
            "items": {who: dict(c) for who, c in self.items.items()},
            "rolls": list(self.rolls[-20:]),
            "window": window,
            "metric": metric,
            "mode": "session" if whole else "encounter",
            "self_name": self.self_name,
            "party": sorted((self.party | self.party_seen | self.party_manual)
                            - self.party_excluded),
            # Раньше здесь стояло «enc is not None», а в режиме сессии
            # объект живёт с самого запуска — флаг не гас никогда, и окно
            # перерисовывалось 30 раз в секунду вечно. Теперь бой считается
            # идущим, пока в логе есть свежие события.
            "active": bool(enc is not None and enc.last
                           and time.time() - enc.last <= cfg["encounter_timeout"]),
            "stats": dict(self.stats),
        }
