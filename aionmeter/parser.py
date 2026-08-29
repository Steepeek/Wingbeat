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
NAME = r"[^\[\]:;]{1,32}?"

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
RE_HIT = re.compile(
    r"^" + _CRIT +
    r"(?P<actor>" + NAME + r") (?:has )?inflicted "
    r"(?P<amount>" + NUM + r") (?:critical )?damage"
    r"(?: and the [^.]{1,80}? effect)?"
    r" on (?P<target>.+?)"
    r"(?: by\s+using (?P<skill>.+?))?\.$"
)

# "You received 367 damage from Steel Rose Veteran."          (входящий)
# "Weisti received 367 damage from Steel Rose Veteran."        (входящий по группе)
# "Training Dummy received 1 234 damage due to the effect of X."  (DoT, без владельца)
RE_RECV = re.compile(
    r"^" + _CRIT +
    r"(?P<target>" + NAME + r") received "
    r"(?P<amount>" + NUM + r") (?:critical |bleeding |poisoning )?damage "
    r"(?:from (?P<actor>" + NAME + r")"
    r"|due to (?:the effect of )?(?P<effect>.+?))\.$"
)

# "You restored 102 of Weisti's HP by using Light of Rejuvenation V."
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

SELF = "You"

_TRANS_THOUSANDS = str.maketrans("", "", THOUSANDS)


def to_int(s: str | None) -> int:
    """Число урона -> int. Снимает любые разделители тысяч."""
    if not s:
        return 0
    return int(s.translate(_TRANS_THOUSANDS))


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
        base = int(datetime(int(day[0:4]), int(day[5:7]), int(day[8:10])).timestamp())
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
            return Event(
                "heal", ts_to_epoch(ts),
                actor=healer or "", target=target,
                amount=to_int(g.get("amount")), skill=g.get("skill") or "",
                extra=g.get("res") or "HP",
            )

    m = RE_XP.match(body)
    if m:
        return Event("xp", ts_to_epoch(ts), target=m["mob"], amount=to_int(m["amount"]))

    if body[0] == "Y":                       # дешёвый отсев: все ниже начинаются с "You"
        m = RE_AP.match(body)
        if m:
            return Event("loot", ts_to_epoch(ts), amount=to_int(m["amount"]), extra="ap")
        m = RE_KINAH_IN.match(body)
        if m:
            return Event("loot", ts_to_epoch(ts), amount=to_int(m["amount"]), extra="kinah_in")
        m = RE_KINAH_OUT.match(body)
        if m:
            return Event("loot", ts_to_epoch(ts), amount=to_int(m["amount"]), extra="kinah_out")

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
        return Event("summon", ts_to_epoch(ts), target=m["pet"], extra="add")
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
