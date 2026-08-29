"""Самопроверка ядра: py tests.py

Без внешних зависимостей. Проверяет ровно те места, на которых ломаются
наивные реализации: NBSP в числах, две формы крита, вставная клауза,
подделка события через чат, обрыв строки на середине, ротация лога.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from aionmeter.aggregate import DAMAGE, Meter
from aionmeter.config import DEFAULTS
from aionmeter.parser import iter_records, parse, to_int
from aionmeter.tailer import Tailer

NBSP = " "
ok = 0
fail = 0


def check(name, got, want):
    global ok, fail
    if got == want:
        ok += 1
    else:
        fail += 1
        print(f"  FAIL  {name}\n        получено: {got!r}\n        ожидалось: {want!r}")


def P(body, ts="2026.08.29 19:48:11"):
    return parse(ts, body)


# --------------------------------------------------------------- парсер

print("парсер: боевые строки")

e = P("Weisti inflicted 4" + NBSP + "231 damage on Steel Rose Veteran by using Freezing Wind IV.")
check("обычный скилловый удар", (e.kind, e.actor, e.amount, e.skill, e.crit),
      ("damage", "Weisti", 4231, "Freezing Wind IV", False))

e = P("Critical Hit! You inflicted 1" + NBSP + "220 critical damage on Training Dummy.")
check("крит автоатаки (пробел + слово critical)", (e.actor, e.amount, e.crit, e.skill),
      ("You", 1220, True, ""))

e = P("Critical Hit!Zero inflicted 1" + NBSP + "412 damage on Training Dummy by using Fang Strike V.")
check("крит скилла (без пробела)", (e.actor, e.amount, e.crit, e.skill),
      ("Zero", 1412, True, "Fang Strike V"))

e = P("Zero inflicted 508 damage and the rune carve effect on Training Dummy by using Rune Carve V.")
check("вставная клауза", (e.actor, e.amount, e.target, e.skill),
      ("Zero", 508, "Training Dummy", "Rune Carve V"))

e = P("Tamiiko inflicted 335 damage on Training Dummy.")
check("автоатака = skill пустой", (e.actor, e.amount, e.skill), ("Tamiiko", 335, ""))

e = P("Training Dummy received 1" + NBSP + "469 damage due to the effect of Magical Water Damage Effect.")
check("DoT без владельца", (e.kind, e.actor, e.amount), ("dot", "", 1469))

e = P("Weisti received 367 damage from Steel Rose Veteran.")
check("входящий урон", (e.kind, e.target, e.actor, e.incoming, e.amount),
      ("damage", "Weisti", "Steel Rose Veteran", True, 367))

e = P("Steel Rose Veteran has inflicted 900 damage on you by using Crush I.")
check("входящий по себе ('has' + строчное you)", (e.incoming, e.target, e.actor, e.amount),
      (True, "You", "Steel Rose Veteran", 900))

print("парсер: хил, опыт, группа, петы")

e = P("You restored 102 of Weisti's HP by using Light of Rejuvenation V.")
check("хил на цель", (e.kind, e.actor, e.target, e.amount), ("heal", "You", "Weisti", 102))

e = P("Weisti recovered 1" + NBSP + "234 HP because Steepeek used Benevolence I.")
check("хил через 'because'", (e.actor, e.target, e.amount), ("Steepeek", "Weisti", 1234))

e = P("Elspe recovered 1" + NBSP + "234 HP by  using Flash of Recovery VII.")
check("двойной пробел 'by  using' в шаблоне клиента", (e.actor, e.amount), ("Elspe", 1234))

e = P("You have gained 500" + NBSP + "420 XP from Steel Rose Veteran (Energy of Repose 87" + NBSP + "576).")
check("опыт = маркер убийства", (e.kind, e.target, e.amount), ("xp", "Steel Rose Veteran", 500420))

e = P("Weisti has joined your group.")
check("вход в группу", (e.kind, e.target, e.extra), ("party", "Weisti", "join"))

e = P("Weisti has left your group.")
check("выход из группы", (e.kind, e.target, e.extra), ("party", "Weisti", "leave"))

e = P("You summoned Holy Servant by using Summon Holy Servant I.")
check("призыв пета", (e.kind, e.target, e.extra), ("summon", "Holy Servant", "add"))

e = P("[3.LFG] Steepeek: SR DECK CLER")
check("своя реплика в чате => свой ник", (e.kind, e.actor), ("chat", "Steepeek"))

print("парсер: защита от подделки и мусора")

check("чужая реплика с текстом под событие урона",
      P("[3.LFG] [charname:Spoofer;1.0 0.6 0.6]: Tamiiko inflicted 999999 damage on X."), None)
check("реплика в say-канале",
      P("Spoofer: Tamiiko inflicted 999999 damage on X."), None)
check("крит-префикс на не-уроне",
      P("Critical Hit!Training Dummy is in the spinning state because Nemme used Body Slice I."), None)
check("системная строка", P("You are too far from the target to use that skill."), None)
check("пустая строка", P(""), None)

check("число с NBSP", to_int("4" + NBSP + "231"), 4231)
check("число с точкой (немецкая локаль)", to_int("3.584"), 3584)
check("число без разделителя", to_int("335"), 335)

print("парсер: склейка многострочных записей")

lines = [
    "2026.08.29 18:21:15 : Legion Message: первая строка",
    "продолжение без метки времени",
    "2026.08.29 18:21:17 : Tamiiko inflicted 335 damage on Training Dummy.",
]
recs = list(iter_records(lines))
check("две записи из трёх строк", len(recs), 2)
check("продолжение приклеено к первой", recs[0][1].count("\n"), 1)
check("вторая запись цела", P(recs[1][1]).amount, 335)

# --------------------------------------------------------------- агрегатор

print("агрегатор: DPS, активное время, сегментация")

cfg = dict(DEFAULTS)
cfg["scope"] = "all"
m = Meter(cfg)
base = "2026.08.29 19:00:"
for i in range(10):                     # 10 ударов по 100, одна секунда = один удар
    m.feed(parse(f"{base}{i:02d}", "Aaa inflicted 100 damage on Dummy."))
snap = m.snapshot(DAMAGE)
row = snap["rows"][0]
check("сумма урона", row["total"], 1000)
check("средний DPS = сумма / активное время", round(row["avg"], 2), 100.0)
check("доля одного актора = 100%", round(row["pct"]), 100)

m2 = Meter(dict(cfg))
m2.feed(parse("2026.08.29 19:00:00", "Aaa inflicted 100 damage on Dummy."))
m2.feed(parse("2026.08.29 19:05:00", "Aaa inflicted 100 damage on Dummy."))
check("пауза 5 минут разрывает бой", m2.snapshot(DAMAGE)["rows"][0]["total"], 100)

m3 = Meter(dict(cfg))
m3.feed(parse("2026.08.29 19:00:00", "Aaa inflicted 100 damage on Dummy."))
m3.feed(parse("2026.08.29 19:00:01", "You have gained 500 XP from Dummy."))
m3.feed(parse("2026.08.29 19:00:09", "Aaa inflicted 700 damage on Other."))
check("смерть цели закрывает бой (с добором DoT)", m3.snapshot(DAMAGE)["rows"][0]["total"], 700)

m4 = Meter(dict(cfg))
m4.feed(parse("2026.08.29 19:00:00", "Weisti has joined your group."))
m4.feed(parse("2026.08.29 19:00:01", "Weisti inflicted 100 damage on Dummy."))
m4.feed(parse("2026.08.29 19:00:01", "Rando inflicted 999 damage on Dummy."))
m4.cfg["scope"] = "party"
names = [r["name"] for r in m4.snapshot(DAMAGE)["rows"]]
check("scope=party скрывает посторонних", names, ["Weisti"])

# --------------------------------------------------------------- tail

print("tail: дочитывание, обрыв строки, ротация")

tmp = Path(tempfile.mkdtemp(prefix="aionmeter-"))
log = tmp / "Chat.log"
log.write_bytes(b"")

t = Tailer(str(log), backfill_bytes=1024)
check("пустой файл — нет строк", t.read(), [])

with open(log, "ab") as f:
    f.write(b"line one\r\nline two\r\n")
check("две полные строки", t.read(), [b"line one", b"line two"])

with open(log, "ab") as f:
    f.write(b"partial without terminator")
check("незавершённая строка наверх не отдаётся", t.read(), [])

with open(log, "ab") as f:
    f.write(b"...tail\r\n")
check("строка отдаётся, когда дописана", t.read(), [b"partial without terminator...tail"])

with open(log, "wb") as f:                       # обрезка (аналог ClearLog)
    f.write(b"after truncate\r\n")
t.read()                                          # первый вызов замечает ротацию
check("после обрезки читаем сначала", t.read(), [b"after truncate"])

t.close()
os.remove(log)                                    # пересоздание файла
with open(log, "wb") as f:
    f.write(b"brand new\r\n")
t2 = Tailer(str(log), backfill_bytes=1024, from_start=True)
check("пересозданный файл читается", t2.read(), [b"brand new"])
t2.close()

# файл должен открываться, даже пока кто-то другой в него пишет и может удалить
holder = open(log, "ab")
t3 = Tailer(str(log), backfill_bytes=1024)
holder.write(b"while open\r\n")
holder.flush()
check("чтение параллельно с записью", b"while open" in t3.read(), True)
t3.close()
holder.close()
try:
    os.remove(log)
    removed = True
except OSError:
    removed = False
check("метр не мешает удалить лог (FILE_SHARE_DELETE)", removed, True)
try:
    tmp.rmdir()
except OSError:
    pass

print()
print(f"пройдено {ok}, провалено {fail}")
sys.exit(1 if fail else 0)
