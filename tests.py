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
from aionmeter import skilldb

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

print("парсер: добыча и PvP")

e = P("You have gained 435 Abyss Points.")
check("очки бездны", (e.kind, e.extra, e.amount), ("loot", "ap", 435))
e = P("You have earned 143" + NBSP + "097 Kinah.")
check("кинах получен", (e.kind, e.extra, e.amount), ("loot", "kinah_in", 143097))
e = P("You spent 12" + NBSP + "000 Kinah.")
check("кинах потрачен", (e.kind, e.extra, e.amount), ("loot", "kinah_out", 12000))
e = P("Kaj has defeated Zxsadntlgw.")
check("чужое PvP-убийство", (e.kind, e.actor, e.target), ("pvp", "Kaj", "Zxsadntlgw"))
e = P("You have defeated Sunayaka.")
check("своё PvP-убийство", (e.kind, e.actor, e.target), ("pvp", "You", "Sunayaka"))
check("строка про нехватку AP не считается добычей",
      P("You do not have enough Abyss Points."), None)

print("парсер: защита от подделки и мусора")

# Чужая реплика распознаётся как чат (нужно для канала группы), но НЕ как урон
spoof = P("[3.LFG] [charname:Spoofer;1.0 0.6 0.6]: Tamiiko inflicted 999999 damage on X.")
check("подделка урона через чат не становится уроном",
      (spoof.kind, spoof.actor, spoof.extra, spoof.amount), ("chat", "Spoofer", "LFG", 0))
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
check("пауза 5 минут разрывает бой", m2.snapshot(DAMAGE, whole=False)["rows"][0]["total"], 100)
check("в режиме сессии обе части складываются", m2.snapshot(DAMAGE, whole=True)["rows"][0]["total"], 200)

m3 = Meter(dict(cfg))
m3.feed(parse("2026.08.29 19:00:00", "Aaa inflicted 100 damage on Dummy."))
m3.feed(parse("2026.08.29 19:00:01", "You have gained 500 XP from Dummy."))
m3.feed(parse("2026.08.29 19:00:09", "Aaa inflicted 700 damage on Other."))
check("смерть цели закрывает бой (с добором DoT)",
      m3.snapshot(DAMAGE, whole=False)["rows"][0]["total"], 700)

m4 = Meter(dict(cfg))
m4.feed(parse("2026.08.29 19:00:00", "Weisti has joined your group."))
m4.feed(parse("2026.08.29 19:00:01", "Weisti inflicted 100 damage on Dummy."))
m4.feed(parse("2026.08.29 19:00:01", "Rando inflicted 999 damage on Dummy."))
m4.cfg["scope"] = "party"
names = [r["name"] for r in m4.snapshot(DAMAGE)["rows"]]
check("scope=party скрывает посторонних", names, ["Weisti"])

print("агрегатор: секции группа/остальные")

m5 = Meter(dict(DEFAULTS))
m5.cfg["scope"] = "split"
m5.feed(parse("2026.08.29 19:00:00", "Weisti has joined your group."))
m5.feed(parse("2026.08.29 19:00:01", "You inflicted 300 damage on Dummy."))
m5.feed(parse("2026.08.29 19:00:01", "Weisti inflicted 100 damage on Dummy."))
m5.feed(parse("2026.08.29 19:00:01", "Rando inflicted 900 damage on Dummy."))
m5.feed(parse("2026.08.29 19:00:01", "Farmer inflicted 100 damage on Dummy."))
snap5 = m5.snapshot(DAMAGE)
check("посторонние не пропадают, а идут отдельной секцией",
      [(r["name"], r["section"]) for r in snap5["rows"]],
      [("You", "party"), ("Weisti", "party"), ("Rando", "other"), ("Farmer", "other")])
check("доля считается ВНУТРИ секции, а не от общего итога",
      [round(r["pct"]) for r in snap5["rows"]], [75, 25, 90, 10])
check("полоса нормируется на лидера своей секции",
      [round(r["bar"], 2) for r in snap5["rows"]], [1.0, 0.33, 1.0, 0.11])
check("итог в шапке — по всем видимым", snap5["total"], 1400)
check("секции описаны в снимке",
      [(s_["key"], s_["total"]) for s_ in snap5["sections"]],
      [("party", 400), ("other", 1000)])

m5.cfg["scope"] = "all"
check("scope=all кладёт всех в одну секцию",
      {r["section"] for r in m5.snapshot(DAMAGE)["rows"]}, {"other", "party"})

m6 = Meter(dict(DEFAULTS))
m6.cfg["scope"] = "split"
m6.feed(parse("2026.08.29 19:00:00", "Mob Guard inflicted 500 damage on You."))
m6.feed(parse("2026.08.29 19:00:01", "Mob Guard inflicted 500 damage on Dummy."))
check("тот, кто бьёт нас, в таблицу урона не попадает",
      [r["name"] for r in m6.snapshot(DAMAGE)["rows"]], [])
check("но попадает в метрику полученного урона",
      [r["name"] for r in m6.snapshot("taken")["rows"]], ["You"])

print("агрегатор: достройка ростера без событий входа")

# Метр часто запускают, когда группа УЖЕ собрана: событий "has joined" не было.
m8 = Meter(dict(DEFAULTS))
m8.cfg["scope"] = "split"
m8.feed(parse("2026.08.29 19:00:00", "Ally inflicted 500 damage on Mob."))
check("до подсказки согруппник числится посторонним",
      m8.snapshot(DAMAGE)["rows"][0]["section"], "other")
# Шаблон "X received N damage from Y" в клиенте существует ТОЛЬКО для группы
m8.feed(parse("2026.08.29 19:00:02", "Ally received 200 damage from Mob."))
check("строка получения урона выдаёт согруппника",
      m8.snapshot(DAMAGE)["rows"][0]["section"], "party")

m9 = Meter(dict(DEFAULTS))
m9.cfg["scope"] = "split"
m9.feed(parse("2026.08.29 19:00:00", "Buddy inflicted 500 damage on Mob."))
m9.feed(parse("2026.08.29 19:00:01", "[1.Group] [charname:Buddy;1.0 1.0 1.0]: го дальше"))
check("реплика в групповом канале выдаёт согруппника",
      m9.snapshot(DAMAGE)["rows"][0]["section"], "party")
m9.feed(parse("2026.08.29 19:00:02", "Buddy has left your group."))
check("выход из группы убирает и достроенного",
      m9.snapshot(DAMAGE)["rows"][0]["section"], "other")

m10 = Meter(dict(DEFAULTS))
m10.feed(parse("2026.08.29 19:00:00", "[3.LFG] [charname:Rando;1.0 1.0 1.0]: wts"))
check("обычный канал согруппником не делает", sorted(m10.party_seen), [])

m11 = Meter(dict(DEFAULTS))
m11.cfg["scope"] = "split"
m11.feed(parse("2026.08.29 19:00:00", "Sniper inflicted 500 damage on Mob."))
m11.set_party("Sniper", True)
check("ручное отнесение к группе", m11.snapshot(DAMAGE)["rows"][0]["section"], "party")
m11.feed(parse("2026.08.29 19:00:01", "Sniper received 100 damage from Mob."))
m11.set_party("Sniper", False)
check("ручное исключение сильнее автоматики",
      m11.snapshot(DAMAGE)["rows"][0]["section"], "other")

print("агрегатор: добыча и разбор по скиллам")

m12 = Meter(dict(DEFAULTS))
m12.cfg["scope"] = "all"
for line in ("You have gained 500 XP from Mob.",
             "You have gained 435 Abyss Points.",
             "You have earned 1" + NBSP + "000 Kinah.",
             "You have defeated Enemy.",
             "You were killed by Enemy's attack."):
    m12.feed(parse("2026.08.29 19:00:00", line))
loot = m12.snapshot(DAMAGE)["loot"]
check("счётчики добычи",
      (loot.get("exp"), loot.get("ap"), loot.get("kinah_in"),
       loot.get("kills"), loot.get("pvp_kills"), loot.get("deaths")),
      (500, 435, 1000, 1, 1, 1))

m13 = Meter(dict(DEFAULTS))
m13.cfg["scope"] = "all"
m13.feed(parse("2026.08.29 19:00:00", "You inflicted 300 damage on Mob by using Gale Arrow VII."))
m13.feed(parse("2026.08.29 19:00:01", "You inflicted 200 damage on Mob by using Gale Arrow VII."))
m13.feed(parse("2026.08.29 19:00:02", "You inflicted 100 damage on Mob by using Swift Shot V."))
m13.feed(parse("2026.08.29 19:00:03", "You inflicted 50 damage on Mob."))
row13 = m13.snapshot(DAMAGE)["rows"][0]
check("разбор по скиллам в снимке", row13["skills"],
      [("Gale Arrow VII", 500), ("Swift Shot V", 100)])
check("автоатаки в разбор скиллов не попадают (имени скилла в логе нет)",
      row13["total"] - sum(v for _k, v in row13["skills"]), 50)

m13.reset()
check("очистка обнуляет и добычу", m13.snapshot(DAMAGE)["loot"], {})

print("агрегатор: определение своего ника")

# Реплика без обёртки [charname:] — признак СЛАБЫЙ: у чужих такие строки
# в логе тоже встречаются (проверено на живом логе: 11 своих против 11 чужих
# от разных людей по одной штуке).
m14 = Meter(dict(DEFAULTS))
m14.feed(parse("2026.08.29 19:00:00", "[3.LFG] Rando: wts stuff"))
check("одна чужая реплика ник не задаёт", m14.self_name, "")
for i in range(3):
    m14.feed(parse(f"2026.08.29 19:00:1{i}", "[3.LFG] Steepeek: SR DECK"))
check("преобладающее имя принимается", m14.self_name, "Steepeek")
check("но помечается как неподтверждённое", m14.self_confirmed, False)

m15 = Meter(dict(DEFAULTS))
m15.feed(parse("2026.08.29 19:00:00", "[3.LFG] Rando: wts stuff"))
m15.feed(parse("2026.08.29 19:00:01",
               "The Glory Points to be deducted for Steepeek are 28."))
check("строка Glory Points задаёт ник однозначно",
      (m15.self_name, m15.self_confirmed), ("Steepeek", True))
for i in range(5):
    m15.feed(parse(f"2026.08.29 19:00:2{i}", "[3.LFG] Impostor: hi"))
check("подтверждённый ник эвристикой не перебивается", m15.self_name, "Steepeek")

m16 = Meter(dict(DEFAULTS))
m16.cfg["scope"] = "all"
m16.feed(parse("2026.08.29 19:00:00",
               "The Glory Points to be deducted for Steepeek are 28."))
m16.feed(parse("2026.08.29 19:00:01", "You inflicted 100 damage on Mob."))
row16 = m16.snapshot(DAMAGE)["rows"][0]
check("в таблице показывается ник, а не «You»",
      (row16["name"], row16["display"], row16["is_self"]), ("You", "Steepeek", True))

print("агрегатор: класс по скиллам")

m17 = Meter(dict(DEFAULTS))
m17.cfg["scope"] = "all"
m17.skill_class = {"Gale Arrow VII": "RA", "Deadshot V": "RA",
                   "Freezing Wind IV": "WI", "Body Smash IV": "FI"}
m17.feed(parse("2026.08.29 19:00:00", "Ann inflicted 100 damage on Mob by using Gale Arrow VII."))
m17.feed(parse("2026.08.29 19:00:01", "Ann inflicted 100 damage on Mob by using Deadshot V."))
m17.feed(parse("2026.08.29 19:00:01", "Bob inflicted 200 damage on Mob by using Freezing Wind IV."))
m17.feed(parse("2026.08.29 19:00:02", "Cid inflicted 300 damage on Mob."))
by_name = {r["display"]: r for r in m17.snapshot(DAMAGE)["rows"]}
check("класс по скиллам", by_name["Ann"]["cls_name"], "Рейнджер")
check("другой класс у другого игрока", by_name["Bob"]["cls_name"], "Волшебник")
check("без скиллов класса нет", by_name["Cid"]["cls_name"], "")
check("код класса тоже в снимке", by_name["Ann"]["cls"], "RA")
check("у каждого класса есть цвет",
      sorted(skilldb.CLASSES) == sorted(skilldb.COLOURS), True)

m18 = Meter(dict(DEFAULTS))
m18.cfg["scope"] = "all"
check("без базы класс не выдумывается", m18.actor_class("Ann"), "")

print("агрегатор: очистка")

m7 = Meter(dict(DEFAULTS))
m7.cfg["scope"] = "all"
m7.feed(parse("2026.08.29 19:00:00", "Aaa inflicted 100 damage on Dummy."))
check("до очистки данные есть", len(m7.snapshot(DAMAGE)["rows"]), 1)
m7.reset()
check("после очистки таблица пуста", m7.snapshot(DAMAGE)["rows"], [])
m7.feed(parse("2026.08.29 19:00:20", "Aaa inflicted 700 damage on Dummy."))
check("после очистки счёт идёт заново", m7.snapshot(DAMAGE)["rows"][0]["total"], 700)

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
