r"""Разбор боевых сообщений Aion Chat.log.

Грамматика выведена не на глаз, а из таблицы шаблонов самого клиента:
    L10N/<lang>/data/data.pak  ->  strings/client_strings_msg.xml
Там ~4800 шаблонов вида STR_MSG_COMBAT_* и STR_SKILL_SUCC_*.

Ловушки, которые здесь закрыты (каждая ломает наивный парсер):

1. Разделитель тысяч — NBSP (U+00A0), а не пробел. В Python \s и split()
   без аргумента матчат NBSP и рвут "4 231" пополам.
2. Крит-префикс имеет две формы: "Critical Hit! You inflicted N critical
   damage" (автоатаки) и "Critical Hit!Zero inflicted N damage" — без
   пробела (скиллы). Разные семейства шаблонов в клиенте.
3. Вставная клауза: "inflicted 508 damage and the rune carve effect on X".
4. Одна логическая запись может занимать несколько физических строк
   (внутри Legion Message встречается \r\r\n).
5. Строка чата может содержать текст, похожий на событие урона, — поэтому
   имя актора не должно содержать [ ] : ; и запись якорится по ^.
6. В шаблоне хила клиента есть опечатка с двойным пробелом: "by  using".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

# --- примитивы грамматики -------------------------------------------------

#: Разделители тысяч, которые может подставить клиент.
#: NBSP — то, что реально пишет клиент на EU/RU. Точка и запятая встречаются
#: в других локалях. Обычный пробел сознательно НЕ включён: он размывает
#: границы токенов, а в наблюдаемых логах не встречается.
THOUSANDS = "   .,"

NUM = r"[0-9][0-9" + re.escape(THOUSANDS) + r"]*"

#: Имя актора: без скобок и двоеточий. Это и есть анти-спуфинг — строка чата
#: всегда содержит "[" или ":" перед текстом игрока.
NAME = r"[^\[\]:;]{1,64}?"

#: Метка времени записи: "YYYY.MM.DD HH:MM:SS" + " : " на позициях [19:22].
TS_RE = re.compile(r"^\d{4}\.\d{2}\.\d{2} \d{2}:\d{2}:\d{2}$")
SEP = " : "
TS_LEN = 19

_CRIT = r"(?:(?P<crit>Critical Hit!)[ ]?)?"

# --- боевые шаблоны -------------------------------------------------------

# "You inflicted 1 220 damage on Training Dummy."
# "Critical Hit!Zero inflicted 1 412 damage on X by using Fang Strike V."
# "Zero inflicted 508 damage and the rune carve effect on X by using ..."
# "<X> has inflicted 900 damage on you by using <S>."   (входящий, 'has' + 'you')
# "Zero inflicted 70 damage on X by reflecting the attack."   (щит-отражатель)
#
# Клауза отражения — не украшение. Без неё "by reflecting the attack" целиком
# уезжало в имя цели: RE_HIT знал только "by using", а target жадно добирал
# хвост. Шаблоны клиента (STR_SKILL_SUCC_Reflector_PROTECT_*):
#     [%SkillTarget] inflicted %num0 damage on [%SkillCaster] by reflecting the attack.
#     [%SkillCaster] inflicted %num0 damage on [%SkillTarget] by reflecting [%SkillName].
RE_HIT = re.compile(
    r"^" + _CRIT +
    r"(?P<actor>" + NAME + r") (?:has )?inflicted "
    r"(?P<amount>" + NUM + r") (?:critical )?damage"
    r"(?: and the [^.]{1,80}? effect)?"
    r" on (?P<target>.+?)"
    r"(?:(?: by\s+using (?P<skill>.+?))"
    r"|(?: by reflecting (?P<reflect>.+?)))?\.$"
)

# "You received 367 damage from Steel Rose Veteran."          (входящий)
# "Weisti received 367 damage from Steel Rose Veteran."        (входящий по группе)
# "Training Dummy received 1 234 damage due to the effect of X."  (DoT, без владельца)
RE_RECV = re.compile(
    r"^" + _CRIT +
    r"(?P<target>" + NAME + r") received "
    r"(?P<amount>" + NUM + r") (?:critical |bleeding |poisoning )?damage "
    r"(?:from (?P<actor>" + NAME + r")"
    r"|after (?P<actor2>" + NAME + r") used (?P<skill2>.+?)"
    r"|due to (?:the effect of )?(?P<effect>.+?))\.$"
)

# «Loluu used Aegis Breaker I to deal Terath Vanquisher 3 451 damage and
# dispel some magical buffs.» Порядок здесь обратный обычному: имя цели
# стоит ПЕРЕД числом, поэтому отдельным шаблоном. У части мобов клиент
# ставит двойной пробел перед числом — отсюда \s+.
RE_DISPEL_HIT = re.compile(
    r"^" + _CRIT +
    r"(?P<actor>" + NAME + r") used (?P<skill>.+?) to deal "
    r"(?:(?P<selftarget>you)|(?P<target>.+?))\s+"
    r"(?P<amount>" + NUM + r") damage and dispel"
)

# "You restored 102 of Weisti's HP by using Light of Rejuvenation V."
#
# ЛОВУШКА. Этой одной фразе в клиенте соответствуют ДВА разных семейства
# шаблонов, и текст у них совпадает до символа:
#     STR_SKILL_SUCC_Heal_Instant_HEAL_ME_TO_B   — лечу я
#     STR_SKILL_SUCC_Heal_INTERVAL_HEAL_TO_B     — тик чужого хота
#     STR_SKILL_SUCC_SkillATKDrain_..._HEAL_TO_B — чужой вампиризм
# Во втором и третьем случае «You» — не игрок: слота для автора в шаблоне
# нет вовсе. Доказано на живом логе:
#     You restored ... of Lamenace's HP by using Exhausting Wave I.
#     Lamenace inflicted 794 damage on Stallari by using Exhausting Wave I.
# то есть лечил себя Lamenace, а строка написана от «You». Поэтому такие
# события помечаются unsure, а автора разбирает агрегатор по классу скилла.
RE_HEAL_RESTORE = re.compile(
    r"^(?P<actor>" + NAME + r") restored (?P<amount>" + NUM + r") "
    r"of (?P<target>.+?)'s (?P<res>HP|MP)"
    r"(?: by\s+using (?P<skill>.+?))?\.$"
)

# "Weisti recovered 1 234 HP because Steepeek used Benevolence I."
# "Weisti recovered 240 MP because you used Recovery Spell I."
RE_HEAL_BECAUSE = re.compile(
    r"^(?P<target>" + NAME + r") recovered (?P<amount>" + NUM + r") (?P<res>HP|MP) "
    r"because (?P<actor>" + NAME + r") used (?P<skill>.+?)\.$"
)

# "Elspe recovered 1 234 HP by using Flash of Recovery VII."   (себе)
# "Sarah recovered 500 HP after using Saving Grace I."
RE_HEAL_SELF = re.compile(
    r"^(?P<actor>" + NAME + r") recovered (?P<amount>" + NUM + r") (?P<res>HP|MP) "
    r"(?:by\s+using|after using) (?P<skill>.+?)\.$"
)

# "Elentiya recovered 22 MP due to the effect of Invincibility Mantra I Effect."
RE_HEAL_REGEN = re.compile(
    r"^(?P<actor>" + NAME + r") recovered (?P<amount>" + NUM + r") (?P<res>HP|MP) "
    r"due to (?:the effect of )?(?P<effect>.+?)\.$"
)

# "You recovered 12 MP." / "Pinkstar restored 148 MP." — регенерация без источника.
RE_HEAL_BARE = re.compile(
    r"^(?P<actor>" + NAME + r") (?:recovered|restored) (?P<amount>" + NUM + r") (?P<res>HP|MP)\.$"
)

# Смерть моба в лог не пишется — но пишется строка опыта с его именем.
# Она одновременно маркер конца боя и признак "это моб, а не игрок".
RE_XP = re.compile(
    r"^You have gained (?P<amount>" + NUM + r") XP from (?P<mob>.+?)"
    r"(?: \(.*\))?\.$"
)

# Добыча за сессию. Точные формулировки из STR_MSG_* клиента.
RE_AP = re.compile(r"^You have gained (?P<amount>" + NUM + r") Abyss Points\.$")
RE_GLORY_GAIN = re.compile(
    r"^You have gained (?P<amount>" + NUM + r") Glory Points\.$")
RE_KINAH_IN = re.compile(
    r"^You (?:have earned|received(?: a refund of)?) (?P<amount>" + NUM + r") Kinah")
RE_KINAH_OUT = re.compile(r"^You spent (?P<amount>" + NUM + r") Kinah\.$")

# PvP: "Kaj has defeated Zxsadntlgw." / "You have defeated X."
RE_PVP = re.compile(
    r"^(?:You have defeated (?P<victim1>.+?)"
    r"|(?P<killer>" + NAME + r") has defeated (?P<victim2>.+?))\.$")

# "You were killed by <X>'s attack." / "<A> was killed by <B>'s attack."
RE_DEATH = re.compile(
    r"^(?P<victim>" + NAME + r") (?:was|were) killed by (?P<killer>.+?)'s attack\.$"
)

# "<Цель> is in the <состояние> state because <Кастер> used <Скилл>."
# Единственная строка, которая называет автора накладываемого эффекта.
# Нужна, чтобы приписать тики дота: сам тик автора не содержит.
RE_STATE = re.compile(
    r"^" + _CRIT +
    r"(?P<target>" + NAME + r") is in the (?P<state>.{1,70}?) state because "
    r"(?P<actor>" + NAME + r") used (?P<skill>.+?)\.$")

# "<Кастер> used <Скилл> to inflict the continuous damage effect on <Цель>."
# Вторая форма наложения дота, которую парсер раньше не знал вовсе: в
# выборке из 20 МБ таких строк 4131. Она тоже называет автора, поэтому
# годится для атрибуции тиков наравне с RE_STATE.
RE_APPLIED_DOT = re.compile(
    r"^" + _CRIT +
    r"(?P<actor>" + NAME + r") used (?P<skill>.+?) to inflict "
    r"the continuous damage effect on (?P<target>.+?)\.$")

# "You have used <предмет>." — STR_USE_ITEM. Банки, свитки, еда, сыворотки.
# Только от первого лица: шаблона для чужих предметов в клиенте нет,
# поэтому расход у согруппников не виден в принципе.
RE_ITEM_USED = re.compile(r"^You have used (?P<item>.+?)\.$")

# Лут. "%0 has acquired %1." — строка группового лута (STR_PARTY_ITEM_WIN).
RE_LOOT_SELF = re.compile(
    r"^You have acquired (?:(?P<count>" + NUM + r") )?(?P<item>.+?)\(?s?\)?"
    r"(?: and stored them in your special cube)?\.$")
RE_LOOT_OTHER = re.compile(
    r"^(?P<who>" + NAME + r") has acquired (?P<item>.+?)\.$")
RE_ROLL = re.compile(
    r"^(?P<who>" + NAME + r") rolled the dice and got a (?P<value>" + NUM + r")"
    r"(?: \(max\. " + NUM + r"\))?\.$")

# Состав группы. Точные формулировки из STR_PARTY_* клиента.
RE_PARTY_JOIN = re.compile(r"^(?P<who>" + NAME + r") has joined your group\.$")
RE_PARTY_LEAVE = re.compile(
    r"^(?P<who>" + NAME + r") has (?:left your group|been kicked out of your group)\.$"
)
RE_PARTY_INVITE = re.compile(r"^You have invited (?P<who>" + NAME + r") to join your group\.$")
RE_PARTY_SELF_LEAVE = re.compile(r"^(?:You left the group|The group has been disbanded)\.$")

# Питомцы и суммоны: их урон пишется под их собственным именем.
RE_SUMMON = re.compile(
    r"^You summoned (?P<pet>.+?)(?: by using (?P<skill>.+?))?\.$"
)
RE_UNSUMMON = re.compile(r"^(?:You unsummon (?P<pet>.+?)|(?P<pet2>.+?) has been dismissed)\.$")

# Саммон в третьем лице, две формы клиента:
#   «Granhildr has summoned Holy Servant to attack X by using Summon ... V.»
#   «Sarah summoned Healing Servant by using Summon Healing Servant I.»
RE_SUMMON_OTHER = re.compile(
    r"^(?P<owner>" + NAME + r") (?:has )?summoned (?P<pet>.+?)"
    r"(?: to attack (?:.+?))?"
    r"(?: by using (?P<skill>.+?))?\.$"
)

# «Terath Vanquisher received the Delayed Blast effect because Loluu used
# Lava Tsunami I.» Единственная строка, из которой можно узнать автора у
# скиллов, тикающих без прямого удара: на живом логе это 5221 запись и
# около 17M урона, до сих пор уезжавшего в «(periodic)».
RE_EFFECT_BECAUSE = re.compile(
    r"^(?P<target>" + NAME + r") received the (?P<effect>.+?) effect "
    r"because (?P<actor>" + NAME + r") used (?P<skill>.+?)\.$"
)

# Собственная реплика в чате идёт БЕЗ обёртки [charname:] — в отличие от чужих.
# Это самый надёжный способ узнать свой ник:
#   чужие:  [3.LFG] [charname:Zuzia;1.0 0.69 0.69]: текст
#   своя:   [3.LFG] Steepeek: текст
RE_OWN_CHAT = re.compile(r"^\[\d+\.(?P<chan>[^\]]+)\] (?P<me>[^\[\]:;]{1,24}): ")
RE_OTHER_CHAT = re.compile(r"^\[\d+\.(?P<chan>[^\]]+)\] \[charname:(?P<who>[^;\]]{1,24});")

# Запасной способ: строка при входе в игру.
RE_GLORY = re.compile(
    r"^The Glory Points to be deducted for (?P<me>" + NAME + r") are "
)

# Вход в игру. Сам по себе ника не несёт, но говорит: с этого момента за
# «You» может стоять уже ДРУГОЙ персонаж. Без него метр, один раз узнав
# ник, держался за него до перезапуска — и на твинке приписывал его удары
# отдельной строкой рядом с «You».
RE_LOGIN = re.compile(r"^You changed the connection status to Online\.$")

SELF = "You"

_TRANS_THOUSANDS = str.maketrans("", "", THOUSANDS)


#: Потолок длины числа. Самый большой удар в игре — семь знаков, у
#: фиктивных чисел щита-отражателя тоже семь. Пятнадцать берём с запасом.
#: Без потолка строка из тысяч цифр (битый лог, чужая подделка) бросала
#: ValueError и обрывала разбор всей прочитанной порции.
MAX_DIGITS = 15


def to_int(s: str | None) -> int:
    """Число урона -> int. Снимает любые разделители тысяч."""
    if not s:
        return 0
    digits = s.translate(_TRANS_THOUSANDS)
    if len(digits) > MAX_DIGITS:
        return 0
    return int(digits)


# --- события --------------------------------------------------------------

@dataclass(slots=True)
class Event:
    kind: str          # damage | dot | heal | xp | death | party | summon | chat
    ts: int            # секунды (монотонно в пределах сессии)
    actor: str = ""    # кто нанёс/вылечил; "" если владелец неизвестен (DoT)
    target: str = ""
    amount: int = 0
    skill: str = ""    # "" => автоатака (для kind == "damage")
    crit: bool = False
    incoming: bool = False   # урон получен, а не нанесён
    #: Автор события в строке НЕ указан однозначно — см. RE_HEAL_RESTORE.
    unsure: bool = False
    extra: str = ""          # эффект / ресурс / подтип


_day_cache: dict[str, int] = {}


def ts_to_epoch(ts: str) -> int:
    """'2026.08.29 19:48:11' -> секунды.

    День кэшируется, поэтому разбор большого лога не упирается в strptime.
    Для DPS важны только разности, поэтому смещение зоны роли не играет.
    """
    day = ts[:10]
    base = _day_cache.get(day)
    if base is None:
        try:
            base = int(datetime(int(day[0:4]), int(day[5:7]),
                                int(day[8:10])).timestamp())
        except ValueError:
            # Число вида «2026.13.45» формой проходит, а датой не является.
            # Такое встречается в битом логе; ронять из-за него разбор
            # нельзя, поэтому запоминаем ноль и идём дальше.
            base = 0
        _day_cache[day] = base
    return base + int(ts[11:13]) * 3600 + int(ts[14:16]) * 60 + int(ts[17:19])


def iter_records(lines):
    """Физические строки -> логические записи (ts, body).

    Строка начинает новую запись, только если на позициях [0:19] стоит метка
    времени, а на [19:22] — ' : '. Всё остальное приклеивается к предыдущей
    записи: клиент переносит длинные сообщения без повтора метки.
    """
    ts = None
    buf: list[str] = []
    for line in lines:
        line = line.rstrip(" \r\n")
        if len(line) > TS_LEN + 2 and line[TS_LEN:TS_LEN + 3] == SEP and TS_RE.match(line[:TS_LEN]):
            if ts is not None:
                yield ts, "\n".join(buf)
            ts = line[:TS_LEN]
            buf = [line[TS_LEN + 3:]]
        elif ts is not None:
            buf.append(line)
    if ts is not None:
        yield ts, "\n".join(buf)


def parse(ts: str, body: str) -> Event | None:
    """Одна запись -> Event или None, если строка не боевая."""
    if not body:
        return None

    # Строки чата отсекаем сразу: они самые частые из небоевых и в них
    # игрок может написать что угодно, включая подделку под событие урона.
    if body[0] == "[":
        m = RE_OWN_CHAT.match(body)
        if m:
            return Event("chat", ts_to_epoch(ts), actor=m["me"],
                         extra=m["chan"], target=SELF)
        m = RE_OTHER_CHAT.match(body)
        if m:
            return Event("chat", ts_to_epoch(ts), actor=m["who"], extra=m["chan"])
        return None

    m = RE_HIT.match(body)
    if m:
        target = m["target"]
        # Входящий урон: "<X> has inflicted N damage on you by using <S>."
        incoming = target == "you"
        return Event(
            "damage", ts_to_epoch(ts),
            actor=m["actor"], target=SELF if incoming else target,
            amount=to_int(m["amount"]), skill=m["skill"] or "",
            crit=bool(m["crit"]), incoming=incoming,
            extra="reflect" if m["reflect"] else "",
        )

    m = RE_DISPEL_HIT.match(body)
    if m:
        target = SELF if m["selftarget"] else m["target"]
        return Event(
            "damage", ts_to_epoch(ts), actor=m["actor"], target=target,
            amount=to_int(m["amount"]), crit=bool(m["crit"]),
            skill=m["skill"], incoming=bool(m["selftarget"]),
        )

    m = RE_RECV.match(body)
    if m:
        t = ts_to_epoch(ts)
        amount = to_int(m["amount"])
        if m["actor"]:
            # Входящий урон по кому-то: "<T> received N damage from <A>."
            return Event(
                "damage", t, actor=m["actor"], target=m["target"],
                amount=amount, crit=bool(m["crit"]), incoming=True,
            )
        if m["actor2"]:
            # «<T> received N poisoning damage after you used <S>.»
            # Автор назван, значит это обычный урон по цели, а не сирота:
            # у рейнджера так тикают ловушки и кровотечения.
            actor = m["actor2"]
            return Event(
                "dot", t, actor=SELF if actor.lower() == "you" else actor,
                target=m["target"], amount=amount, extra=m["skill2"] or "",
            )
        # Периодический урон. Владельца в шаблоне физически нет.
        return Event(
            "dot", t, actor="", target=m["target"],
            amount=amount, extra=m["effect"] or "",
        )

    if " HP" in body or " MP" in body:
        for rx, healer_group in (
            (RE_HEAL_RESTORE, "actor"),
            (RE_HEAL_BECAUSE, "actor"),
            (RE_HEAL_SELF, "actor"),
            (RE_HEAL_REGEN, None),
            (RE_HEAL_BARE, None),
        ):
            m = rx.match(body)
            if not m:
                continue
            g = m.groupdict()
            healer = g.get(healer_group) if healer_group else ""
            if healer and healer.lower() == "you":
                healer = SELF
            target = g.get("target") or g.get("actor") or ""
            # Две формы не называют автора, и обе от лица «You»:
            #   «You restored N of X's HP by using S» — Heal_*_HEAL_TO_B,
            #      мой хил и чужой хот/вампиризм пишутся одинаково;
            #   «You recovered N HP by using S» — Heal_*_HEAL_TO_ME, где
            #      TO_ME значит «цель — я». То есть меня ВЫЛЕЧИЛИ, а «by
            #      using» относится к скиллу лекаря, а не к моему действию.
            # Остальные формы автора называют прямо и сомнений не вызывают.
            unsure = healer == SELF and (
                (rx is RE_HEAL_RESTORE and target != SELF)
                or rx is RE_HEAL_SELF)
            return Event(
                "heal", ts_to_epoch(ts),
                actor=healer or "", target=target,
                amount=to_int(g.get("amount")), skill=g.get("skill") or "",
                extra=g.get("res") or "HP", unsure=unsure,
            )

    m = RE_XP.match(body)
    if m:
        return Event("xp", ts_to_epoch(ts), target=m["mob"], amount=to_int(m["amount"]))

    if body[0] == "Y":                       # дешёвый отсев: все ниже начинаются с "You"
        m = RE_AP.match(body)
        if m:
            return Event("loot", ts_to_epoch(ts), amount=to_int(m["amount"]), extra="ap")
        m = RE_GLORY_GAIN.match(body)
        if m:
            return Event("loot", ts_to_epoch(ts),
                         amount=to_int(m["amount"]), extra="glory")
        m = RE_KINAH_IN.match(body)
        if m:
            return Event("loot", ts_to_epoch(ts), amount=to_int(m["amount"]), extra="kinah_in")
        m = RE_KINAH_OUT.match(body)
        if m:
            return Event("loot", ts_to_epoch(ts), amount=to_int(m["amount"]), extra="kinah_out")

    if body[:4] == "You " or " has acquired " in body:
        m = RE_LOOT_SELF.match(body)
        if m:
            return Event("loot_item", ts_to_epoch(ts), actor=SELF, target=m["item"],
                         amount=to_int(m["count"]) or 1)
        m = RE_LOOT_OTHER.match(body)
        if m:
            return Event("loot_item", ts_to_epoch(ts), actor=m["who"],
                         target=m["item"], amount=1)

    m = RE_STATE.match(body)
    if m:
        return Event("applied", ts_to_epoch(ts), actor=m["actor"],
                     target=m["target"], skill=m["skill"], extra=m["state"])

    m = RE_APPLIED_DOT.match(body)
    if m:
        return Event("applied", ts_to_epoch(ts), actor=m["actor"],
                     target=m["target"], skill=m["skill"], extra="continuous damage")

    m = RE_EFFECT_BECAUSE.match(body)
    if m:
        # Ключ атрибуции — имя СКИЛЛА, а не эффекта. Проверено на живом
        # логе: строка наложения говорит «received the Delayed Blast effect
        # because Weisti used Delayed Blast IV», а тик приходит как «due to
        # the effect of Delayed Blast IV» — то есть под именем скилла с
        # рангом. По названию эффекта («Delayed Blast») тик не нашёлся бы.
        actor = m["actor"]
        return Event("applied", ts_to_epoch(ts),
                     actor=SELF if actor.lower() == "you" else actor,
                     target=m["target"], skill=m["skill"], extra=m["effect"])

    m = RE_ITEM_USED.match(body)
    if m:
        return Event("used_item", ts_to_epoch(ts), actor=SELF, skill=m["item"])

    m = RE_ROLL.match(body)
    if m:
        return Event("roll", ts_to_epoch(ts), actor=m["who"], amount=to_int(m["value"]))

    m = RE_PVP.match(body)
    if m:
        killer = m["killer"] or SELF
        return Event("pvp", ts_to_epoch(ts), actor=killer,
                     target=m["victim1"] or m["victim2"])

    m = RE_DEATH.match(body)
    if m:
        victim = m["victim"]
        return Event(
            "death", ts_to_epoch(ts),
            actor=m["killer"], target=SELF if victim == "You" else victim,
        )

    m = RE_SUMMON.match(body)
    if m:
        return Event("summon", ts_to_epoch(ts), actor=SELF, target=m["pet"],
                     extra="add")
    m = RE_SUMMON_OTHER.match(body)
    if m:
        owner = m["owner"]
        return Event("summon", ts_to_epoch(ts),
                     actor=SELF if owner.lower() == "you" else owner,
                     target=m["pet"], extra="add")
    m = RE_UNSUMMON.match(body)
    if m:
        return Event("summon", ts_to_epoch(ts), target=m["pet"] or m["pet2"], extra="del")

    for rx, sub in (
        (RE_PARTY_JOIN, "join"), (RE_PARTY_LEAVE, "leave"), (RE_PARTY_INVITE, "invite"),
    ):
        m = rx.match(body)
        if m:
            return Event("party", ts_to_epoch(ts), target=m["who"], extra=sub)
    if RE_PARTY_SELF_LEAVE.match(body):
        return Event("party", ts_to_epoch(ts), extra="disband")

    if RE_LOGIN.match(body):
        return Event("login", ts_to_epoch(ts))

    m = RE_GLORY.match(body)
    if m:
        return Event("chat", ts_to_epoch(ts), actor=m["me"], extra="glory")

    return None


def parse_lines(lines):
    """Удобная обёртка: физические строки -> поток Event."""
    for ts, body in iter_records(lines):
        ev = parse(ts, body)
        if ev is not None:
            yield ev
