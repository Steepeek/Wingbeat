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
            elif ev.extra == "disband":
                self.party.clear()
            return

        if kind == "summon":
            if ev.extra == "add":
                self.pets.add(ev.target)
            else:
                self.pets.discard(ev.target)
            return

        if kind == "chat":
            if not self.cfg.get("self_name") and not self.self_name and ev.actor:
                # Собственная реплика идёт без обёртки [charname:] — этого хватает.
                self.self_name = ev.actor
            return

        if kind == "xp":
            self.mobs.add(ev.target)
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
            # Владельца эффекта в строке физически нет — отдельной строкой,
            # а не размазываем молча по игрокам.
            self._add(DAMAGE, "(периодический)", ev.ts, ev.amount, False, "")
            return

        if kind != "damage" or ev.amount <= 0:
            return

        if ev.incoming:
            # Кто-то получил урон. Атакующего запоминаем как враждебного.
            if ev.actor:
                self.hostiles.add(ev.actor)
            self._add(TAKEN, self._owner(ev.target), ev.ts, ev.amount, ev.crit, ev.skill)
            return

        # Урон по кому-то из наших — значит бьёт враг.
        if ev.target == SELF or ev.target in self.party:
            self.hostiles.add(ev.actor)
            self._add(TAKEN, self._owner(ev.target), ev.ts, ev.amount, ev.crit, ev.skill)
            return

        enc = self._ensure(ev.ts)
        enc.targets[ev.target] += ev.amount
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

    def visible(self, name: str) -> bool:
        if name == "(периодический)":
            return True
        if self.cfg.get("scope") == "party":
            return name == SELF or name in self.party or name in self.pets
        if self.cfg.get("hide_mobs", True):
            return name not in self.mobs and name not in self.hostiles
        return True

    def snapshot(self, metric: str | None = None, whole: bool = False) -> dict:
        cfg = self.cfg
        metric = metric or cfg.get("metric", DAMAGE)
        enc = self.session if whole else self.encounter
        window = cfg["dps_window"]
        now = self.last_ts

        rows = []
        if enc is not None:
            for name, a in enc.actors[metric].items():
                if not self.visible(name):
                    continue
                rows.append({
                    "name": name,
                    "total": a.total,
                    "dps": a.dps_now(now, window),
                    "avg": a.avg_dps,
                    "hits": a.hits,
                    "crit": a.crit_pct,
                    "max": a.max_hit,
                    "is_self": name == SELF or name == self.self_name,
                    "is_party": name in self.party,
                    "top_skill": a.skills.most_common(1)[0][0] if a.skills else "",
                })

        total = sum(r["total"] for r in rows) or 1
        # Три ключа сортировки: иначе строки прыгают при равенстве
        rows.sort(key=lambda r: (-r["total"], -r["hits"], r["name"]))
        top = rows[0]["total"] if rows else 1
        for r in rows:
            r["pct"] = 100.0 * r["total"] / total
            r["bar"] = r["total"] / (top or 1)   # полоса нормируется на лидера

        limit = cfg.get("max_rows", 12)
        return {
            "rows": rows[:limit],
            "hidden": max(0, len(rows) - limit),
            "total": total if rows else 0,
            "duration": enc.duration if enc else 0,
            "target": enc.dominant if enc else "",
            "kills": len(enc.kills) if enc else 0,
            "window": window,
            "metric": metric,
            "self_name": self.self_name,
            "party": sorted(self.party),
            "active": enc is not None,
            "stats": dict(self.stats),
        }
