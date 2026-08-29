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

from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .parser import SELF

DAMAGE, HEAL, TAKEN = "damage", "heal", "taken"

#: Периодический урон: владельца эффекта в строке лога физически нет.
UNATTRIBUTED = "(периодический)"

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

    def add(self, ts: int, amount: int, crit: bool, skill: str, active_gap: int) -> None:
        self.total += amount
        self.hits += 1
        if amount > self.max_hit:
            self.max_hit = amount
        if crit:
            self.crits += 1
        if skill:
            self.skill_hits += 1
            self.skills[skill] += amount
        self.per_sec[ts] = self.per_sec.get(ts, 0) + amount
        if self.runs and ts - self.runs[-1][1] <= active_gap:
            if ts > self.runs[-1][1]:
                self.runs[-1][1] = ts
        else:
            self.runs.append([ts, ts])

    @property
    def active_seconds(self) -> int:
        # +1, иначе бой внутри одной секунды даёт деление на ноль
        return sum(b - a + 1 for a, b in self.runs) or 1

    @property
    def avg_dps(self) -> float:
        return self.total / self.active_seconds

    def dps_now(self, now: int, window: int) -> float:
        # Верхняя граница now-1: последняя секунда файла ещё пишется
        lo, hi = now - window, now - 1
        if hi < lo:
            return 0.0
        s = sum(v for t, v in self.per_sec.items() if lo <= t <= hi)
        return s / window

    @property
    def crit_pct(self) -> float | None:
        return 100.0 * self.crits / self.hits if self.hits else None


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
        self.kills: list[str] = []

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
        self.mobs: set[str] = set()
        self.hostiles: set[str] = set()
        self.encounter: Encounter | None = None
        self.session = Encounter(0, cfg)     # накапливает всё, не закрывается
        self.history: list[Encounter] = []
        self.pending_close = 0
        self.last_ts = 0
        self.stats = Counter()

    # -- служебное --

    def reset(self) -> None:
        if self.encounter is not None:
            self.history.append(self.encounter)
        self.encounter = None
        self.session = Encounter(0, self.cfg)
        self.pending_close = 0

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
             crit: bool, skill: str) -> None:
        """Пишем и в текущий бой, и в счётчик за всю сессию."""
        gap = self.cfg["active_gap"]
        enc = self._ensure(ts)
        enc.actor(metric, name).add(ts, amount, crit, skill, gap)
        if not self.session.start:
            self.session.start = ts
        self.session.last = max(self.session.last, ts)
        self.session.actor(metric, name).add(ts, amount, crit, skill, gap)

    def _owner(self, name: str) -> str:
        """Питомца схлопываем во владельца, если так настроено."""
        if self.cfg.get("merge_pets", True) and name in self.pets:
            return SELF
        return name

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

    def feed(self, ev) -> None:
        self.stats["events"] += 1
        self.last_ts = max(self.last_ts, ev.ts)
        kind = ev.kind

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
                self.pets.add(ev.target)
            else:
                self.pets.discard(ev.target)
            return

        if kind == "chat":
            if ev.target == SELF:
                if not self.cfg.get("self_name") and not self.self_name and ev.actor:
                    # Своя реплика идёт без обёртки [charname:] — этого хватает.
                    self.self_name = ev.actor
            elif ev.extra in PARTY_CHANNELS and ev.actor:
                self.party_seen.add(ev.actor)
            return

        if kind == "xp":
            self.mobs.add(ev.target)
            self.session.kills.append(ev.target)
            enc = self.encounter
            if enc is not None:
                enc.kills.append(ev.target)
                self.pending_close = ev.ts + self.cfg["kill_grace"]
            return

        if kind == "death":
            self.mobs.discard(ev.target)
            return

        if kind == "heal":
            if not ev.actor or ev.amount <= 0 or ev.extra == "MP":
                return
            self._add(HEAL, self._owner(ev.actor), ev.ts, ev.amount, False, ev.skill)
            return

        if kind == "dot":
            if ev.amount <= 0:
                return
            self._ensure(ev.ts).targets[ev.target] += ev.amount
            self.session.targets[ev.target] += ev.amount
            # Владельца эффекта в строке физически нет — отдельной строкой,
            # а не размазываем молча по игрокам.
            self._add(DAMAGE, UNATTRIBUTED, ev.ts, ev.amount, False, "")
            return

        if kind != "damage" or ev.amount <= 0:
            return

        if ev.incoming:
            # Кто-то получил урон. Атакующего запоминаем как враждебного.
            if ev.actor:
                self.hostiles.add(ev.actor)
            if ev.target != SELF and ev.target not in self.pets:
                self.party_seen.add(ev.target)
            self._add(TAKEN, self._owner(ev.target), ev.ts, ev.amount, ev.crit, ev.skill)
            return

        # Урон по кому-то из наших — значит бьёт враг.
        if ev.target == SELF or ev.target in self.party:
            self.hostiles.add(ev.actor)
            self._add(TAKEN, self._owner(ev.target), ev.ts, ev.amount, ev.crit, ev.skill)
            return

        enc = self._ensure(ev.ts)
        enc.targets[ev.target] += ev.amount
        self.session.targets[ev.target] += ev.amount
        self._maybe_split_on_target(enc, ev)
        self._add(DAMAGE, self._owner(ev.actor), ev.ts, ev.amount, ev.crit, ev.skill)

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
        if name == UNATTRIBUTED:
            return "other"
        if self.cfg.get("hide_mobs", True) and (name in self.mobs or name in self.hostiles):
            return None
        return "other"

    def snapshot(self, metric: str | None = None, whole: bool | None = None) -> dict:
        cfg = self.cfg
        metric = metric or cfg.get("metric", DAMAGE)
        if whole is None:
            whole = cfg.get("mode", "session") == "session"
        enc = self.session if whole else self.encounter
        window = cfg["dps_window"]
        now = self.last_ts

        rows = []
        if enc is not None:
            for name, a in enc.actors[metric].items():
                section = self.section_of(name)
                if section is None:
                    continue
                rows.append({
                    "name": name,
                    "section": section,
                    "total": a.total,
                    "dps": a.dps_now(now, window),
                    "avg": a.avg_dps,
                    "hits": a.hits,
                    "crit": a.crit_pct,
                    "max": a.max_hit,
                    "is_self": name == SELF or (bool(self.self_name) and name == self.self_name),
                    "is_party": name in self.party,
                    "top_skill": a.skills.most_common(1)[0][0] if a.skills else "",
                })

        # Сортировка: сначала своя группа, внутри — по урону.
        # Три ключа, иначе строки прыгают местами при равенстве.
        order = {"party": 0, "other": 1}
        rows.sort(key=lambda r: (order[r["section"]], -r["total"], -r["hits"], r["name"]))

        # Доля и длина полосы считаются ВНУТРИ секции: сравнивать себя
        # осмысленно с согруппниками, а не с посторонним фармером рядом.
        sections = []
        for key in ("party", "other"):
            part = [r for r in rows if r["section"] == key]
            if not part:
                continue
            sec_total = sum(r["total"] for r in part) or 1
            top = part[0]["total"] or 1
            for r in part:
                r["pct"] = 100.0 * r["total"] / sec_total
                r["bar"] = r["total"] / top
            sections.append({"key": key, "total": sec_total, "count": len(part)})

        limit = cfg.get("max_rows", 12)
        shown = rows[:limit]
        return {
            "rows": shown,
            "sections": sections,
            "split": cfg.get("scope", "split") == "split",
            "hidden": max(0, len(rows) - limit),
            "total": sum(r["total"] for r in rows),
            "duration": enc.duration if enc and enc.start else 0,
            "target": enc.dominant if enc else "",
            "kills": len(enc.kills) if enc else 0,
            "window": window,
            "metric": metric,
            "mode": "session" if whole else "encounter",
            "self_name": self.self_name,
            "party": sorted((self.party | self.party_seen | self.party_manual)
                            - self.party_excluded),
            "active": enc is not None,
            "stats": dict(self.stats),
        }
