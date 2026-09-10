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

from aionmeter.aggregate import (DAMAGE, UNATTRIBUTED, UNKNOWN_HEALER, Meter)

NO_OWNER_NAMES = (UNATTRIBUTED, UNKNOWN_HEALER)
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
# Строка наложения эффекта нужна для атрибуции дотов, но уроном не является
# и крит-префикс не должен залипать в имя цели
_st = P("Critical Hit!Training Dummy is in the spinning state because Nemme used Body Slice I.")
check("крит-префикс на не-уроне: это не урон",
      (_st.kind, _st.amount), ("applied", 0))
check("крит-префикс не залипает в имя цели",
      (_st.target, _st.actor, _st.skill),
      ("Training Dummy", "Nemme", "Body Slice I"))
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
check("своя строка подписана «You», ник хранится отдельно",
      (row16["name"], row16["display"], row16["is_self"], m16.self_name),
      ("You", "You", True, "Steepeek"))
m16.cfg["show_own_nick"] = True
check("настройкой можно вернуть ник",
      m16.snapshot(DAMAGE)["rows"][0]["display"], "Steepeek")


print("парсер: отражение щитом")

# Шаблоны клиента STR_SKILL_SUCC_Reflector_PROTECT_A_to_B / _A_to_ME.
# Раньше RE_HIT знал только клаузу "by using", и хвост "by reflecting the
# attack" целиком уезжал в имя цели — цель дробилась, а урон засчитывался.
e = P("Steepeek inflicted 70 damage on Elite Krotan Officer by reflecting the attack.")
check("цель отражения разобрана без хвоста",
      (e.kind, e.actor, e.target, e.amount, e.extra),
      ("damage", "Steepeek", "Elite Krotan Officer", 70, "reflect"))

e = P("Zero inflicted 168 damage on Seasoned Ulsaruk by reflecting Fang Strike V.")
check("отражение именованного скилла тоже помечено",
      (e.target, e.amount, e.extra), ("Seasoned Ulsaruk", 168, "reflect"))

e = P("Weisti inflicted 4" + NBSP + "231 damage on Mob by using Freezing Wind IV.")
check("обычный удар отражением не помечается", e.extra, "")


print("агрегатор: отражение и эхо своего урона")

# На аое Модор клиент печатает всем участникам одно и то же фиктивное число
# 6 553 601 — больше, чем весь их реальный урон за бой. Замер на живом логе:
# 20 таких строк переворачивали таблицу целиком.
m17 = Meter(dict(DEFAULTS))
m17.cfg["scope"] = "all"
m17.feed(parse("2026.09.05 00:51:13", "Ally inflicted 1000 damage on Modor."))
m17.feed(parse("2026.09.05 00:51:14",
               "Ally inflicted 6" + NBSP + "553" + NBSP + "601 damage on Modor "
               "by reflecting the attack."))
check("отражение в урон по умолчанию не идёт",
      [(r["name"], r["total"]) for r in m17.snapshot(DAMAGE)["rows"]],
      [("Ally", 1000)])

m18 = Meter(dict(DEFAULTS))
m18.cfg["scope"] = "all"
m18.cfg["count_reflect"] = True
m18.feed(parse("2026.09.05 00:51:14",
               "Ally inflicted 70 damage on Modor by reflecting the attack."))
check("настройкой отражение возвращается",
      m18.snapshot(DAMAGE)["rows"][0]["total"], 70)

# Своё попадание клиент пишет дважды: от первого лица и по нику. На живом
# логе 359 из 397 строк с ником (90 %) имеют такого близнеца.
m19 = Meter(dict(DEFAULTS))
m19.cfg["scope"] = "all"
m19.feed(parse("2026.09.04 15:06:59",
               "The Glory Points to be deducted for Steepeek are 28."))
m19.feed(parse("2026.09.04 15:06:59",
               "You inflicted 2" + NBSP + "469 damage on Magus by using Rupture Arrow IV."))
m19.feed(parse("2026.09.04 15:06:59",
               "Steepeek inflicted 2" + NBSP + "469 damage on Magus by using Rupture Arrow IV."))
rows19 = m19.snapshot(DAMAGE)["rows"]
check("эхо своего урона не удваивает и не двоит строку",
      [(r["name"], r["total"], r["hits"]) for r in rows19],
      [("You", 2469, 1)])

# А вот два настоящих одинаковых попадания в одну секунду — это два удара.
m20 = Meter(dict(DEFAULTS))
m20.cfg["scope"] = "all"
m20.feed(parse("2026.09.04 15:06:59",
               "The Glory Points to be deducted for Steepeek are 28."))
for _ in range(2):
    m20.feed(parse("2026.09.04 15:06:59", "You inflicted 500 damage on Magus."))
check("повтор той же формы считается как два удара",
      [(r["total"], r["hits"]) for r in m20.snapshot(DAMAGE)["rows"]], [(1000, 2)])

# Ник, пришедший без первого лица, всё равно должен лечь в свою строку.
m21 = Meter(dict(DEFAULTS))
m21.cfg["scope"] = "all"
m21.feed(parse("2026.09.04 15:06:59",
               "The Glory Points to be deducted for Steepeek are 28."))
m21.feed(parse("2026.09.04 15:07:00", "Steepeek inflicted 300 damage on Magus."))
check("одиночная запись по нику склеена со своей строкой",
      [(r["name"], r["total"]) for r in m21.snapshot(DAMAGE)["rows"]], [("You", 300)])


print("бафы, расходники, вторая форма наложения дота")

e = P("You have used Greater Divine Life Serum.")
check("предмет использован", (e.kind, e.actor, e.skill),
      ("used_item", "You", "Greater Divine Life Serum"))

e = P("Lowrider is in the boost Attack state because Lowrider used Rage VI.")
check("наложение эффекта отдаёт и состояние",
      (e.kind, e.actor, e.target, e.skill, e.extra),
      ("applied", "Lowrider", "Lowrider", "Rage VI", "boost Attack"))

# Вторая форма наложения дота: раньше парсер её не знал вовсе, а в выборке
# из 20 МБ таких строк 4131 — все с именем автора.
e = P("Alexstrasza used Erosion VI to inflict the continuous damage effect on Zeralukis.")
check("вторая форма наложения дота разбирается",
      (e.kind, e.actor, e.target, e.skill),
      ("applied", "Alexstrasza", "Zeralukis", "Erosion VI"))

m50 = Meter(dict(DEFAULTS))
m50.cfg["scope"] = "all"
m50.feed(parse("2026.09.07 12:00:00", "Ally inflicted 100 damage on Mob."))
m50.feed(parse("2026.09.07 12:00:01",
               "Ally is in the boost Attack state because Ally used Rage VI."))
m50.feed(parse("2026.09.07 12:00:02",
               "Ally is in the boost Attack state because Ally used Rage VI."))
m50.feed(parse("2026.09.07 12:00:03", "You have used Fine Anti-Shock Scroll."))
rows50 = {r["name"]: dict(r.get("buffs") or []) for r in m50.snapshot(DAMAGE)["rows"]}
check("применения эффекта считаются по автору",
      rows50.get("Ally", {}).get("Rage VI"), 2)
check("свой расходник попадает в свою строку",
      rows50.get("You", {}).get("Fine Anti-Shock Scroll"), 1)

# Дот, наложенный второй формой, должен приписаться автору, а не остаться
# в строке без владельца.
m51 = Meter(dict(DEFAULTS))
m51.cfg["scope"] = "all"
m51.feed(parse("2026.09.07 12:00:00",
               "Alexstrasza used Erosion VI to inflict the continuous damage effect on Mob."))
m51.feed(parse("2026.09.07 12:00:01",
               "Mob received 500 damage due to the effect of Erosion VI."))
check("тик дота ушёл автору из второй формы",
      {r["name"]: r["total"] for r in m51.snapshot(DAMAGE)["rows"]},
      {"Alexstrasza": 500})


print("хил: кому он на самом деле принадлежит")

from aionmeter.aggregate import HEAL, UNKNOWN_HEALER

def _heal_meter(self_class="RA", skills=None):
    # Класс задаём ДО создания: Meter засевает им голоса в __init__, иначе
    # правило разбора хила считает свой класс неизвестным.
    cfg = dict(DEFAULTS)
    cfg["scope"] = "all"
    cfg["self_class"] = self_class
    m = Meter(cfg)
    m.skill_class = dict(skills or {})
    return m

def _heal_rows(m):
    return {r["name"]: r["total"] for r in m.snapshot(HEAL)["rows"]}

# «You restored N of X's HP by using S» — этой одной фразе в клиенте отвечают
# и мой хил, и чужой хот, и чужой вампиризм. Разбираем по классу скилла.
m40 = _heal_meter(skills={"Word of Revival V": "CH"})
m40.feed(parse("2026.09.07 12:00:00",
               "You restored 500 of Kimiko's HP by using Word of Revival V."))
check("хилка чужого класса не приписывается игроку",
      _heal_rows(m40), {UNKNOWN_HEALER: 500})

# Свой класс — свой хил.
m41 = _heal_meter(self_class="PR", skills={"Light of Rejuvenation V": "PR"})
m41.feed(parse("2026.09.07 12:00:00",
               "You restored 500 of Kimiko's HP by using Light of Rejuvenation V."))
check("хилка своего класса остаётся за игроком", _heal_rows(m41), {"You": 500})

# Вампиризм: названный сам бьёт этим же скиллом — лечит себя.
m42 = _heal_meter(skills={"Exhausting Wave I": "FI"})
m42.feed(parse("2026.09.07 12:00:00",
               "Lamenace inflicted 794 damage on Mob by using Exhausting Wave I."))
m42.feed(parse("2026.09.07 12:00:00",
               "You restored 300 of Lamenace's HP by using Exhausting Wave I."))
check("вампиризм записан тому, кто бьёт", _heal_rows(m42).get("Lamenace"), 300)

# Зелье класса не имеет: пьёт его тот, кто назван.
m43 = _heal_meter()
m43.feed(parse("2026.09.07 12:00:00",
               "You restored 200 of Miixd's HP by using Major Recovery Potion."))
check("зелье засчитано тому, кто его выпил", _heal_rows(m43), {"Miixd": 200})

# «You recovered N HP by using S» — это шаблон HEAL_TO_ME: вылечили МЕНЯ.
m44 = _heal_meter(skills={"Word of Revival V": "CH"})
m44.feed(parse("2026.09.07 12:00:00", "You recovered 400 HP by using Word of Revival V."))
check("«меня вылечили» не считается моим хилом",
      _heal_rows(m44), {UNKNOWN_HEALER: 400})

# Свой самохил остаётся своим.
m45 = _heal_meter(skills={"Seizure Arrow II": "RA"})
m45.feed(parse("2026.09.07 12:00:00", "You recovered 400 HP by using Seizure Arrow II."))
check("свой самохил остаётся за игроком", _heal_rows(m45), {"You": 400})

# Форма с явным автором сомнений не вызывает и правилом не трогается.
m46 = _heal_meter(skills={"Healing Wind IV": "CH"})
m46.feed(parse("2026.09.07 12:00:00",
               "Lisa recovered 2" + NBSP + "883 HP because Athen used Healing Wind IV."))
check("явный автор берётся как есть", _heal_rows(m46), {"Athen": 2883})

# Класс ещё не известен — отбирать хил у игрока нельзя.
m47 = _heal_meter(self_class="", skills={"Word of Revival V": "CH"})
m47.feed(parse("2026.09.07 12:00:00",
               "You restored 500 of Kimiko's HP by using Word of Revival V."))
check("при неизвестном своём классе хил остаётся за игроком",
      _heal_rows(m47), {"You": 500})


print("добыча: только выпавшее в бою")

# Клиент пишет одну строку и на дроп, и на взятое со склада: STR_MSG_GET_ITEM.
# Отличаем по обстановке — был ли рядом бой.
m30 = Meter(dict(DEFAULTS))
m30.cfg["scope"] = "all"
m30.feed(parse("2026.09.05 12:00:00", "You inflicted 500 damage on Mob."))
m30.feed(parse("2026.09.05 12:00:05", "You have acquired [item:111;ver6;;;;]."))
rows23 = m30.snapshot("loot")["rows"]
check("дроп во время боя засчитан", [r["total"] for r in rows23], [1])

# Спустя долгую тишину — это склад или почта.
m31 = Meter(dict(DEFAULTS))
m31.cfg["scope"] = "all"
m31.feed(parse("2026.09.05 12:00:00", "You inflicted 500 damage on Mob."))
m31.feed(parse("2026.09.05 12:30:00", "You have acquired [item:222;ver6;;;;]."))
check("взятое вне боя не засчитано", m31.snapshot("loot")["rows"], [])

# Галочка возвращает прежнее поведение целиком.
m32 = Meter(dict(DEFAULTS))
m32.cfg["scope"] = "all"
m32.cfg["loot_in_combat"] = False
m32.feed(parse("2026.09.05 12:30:00", "You have acquired [item:222;ver6;;;;]."))
check("настройкой считается всё подряд",
      [r["total"] for r in m32.snapshot("loot")["rows"]], [1])

# Труп обыскивают не мгновенно — окно после боя должно быть щедрым.
m33 = Meter(dict(DEFAULTS))
m33.cfg["scope"] = "all"
m33.feed(parse("2026.09.05 12:00:00", "You inflicted 500 damage on Mob."))
m33.feed(parse("2026.09.05 12:00:20", "You have acquired [item:333;ver6;;;;]."))
check("подбор через 20 с после боя ещё считается дропом",
      [r["total"] for r in m33.snapshot("loot")["rows"]], [1])


print("добыча: номера предметов")

m22 = Meter(dict(DEFAULTS))
m22.cfg["scope"] = "all"
m22.feed(parse("2026.09.05 11:59:58", "You inflicted 100 damage on Mob."))
m22.feed(parse("2026.09.05 12:00:00",
               "You have acquired [item:167000522;ver6;;;;]."))
# Короткая форма без точек с запятой: раньше номер уезжал со скобкой,
# и название не находилось никогда.
m22.feed(parse("2026.09.05 12:00:01", "You have acquired [item:186000938]."))
loot22 = m22.snapshot("loot")["rows"]
ids22 = sorted((loot22[0].get("ids") or {}).values()) if loot22 else []
check("номер вынут из обеих форм записи", ids22, ["167000522", "186000938"])
check("в скобках и точках с запятой номер не остаётся",
      [i for i in ids22 if not i.isdigit()], [])


print("версии")

from aionmeter import version as vermod

check("разбор тега", vermod.as_tuple("v1.2.3"), (1, 2, 3))
check("хвост после дефиса отбрасывается", vermod.as_tuple("0.4.0-beta2"), (0, 4, 0))
check("нечисловая часть не роняет разбор", vermod.as_tuple("1.x.3"), (1, 0, 3))
# Строковое сравнение здесь дало бы неверный ответ: "0.10.0" < "0.9.0".
check("0.10.0 новее 0.9.0", vermod.is_newer("0.10.0", "0.9.0"), True)
check("0.9.0 не новее 0.10.0", vermod.is_newer("0.9.0", "0.10.0"), False)
check("та же версия не новее", vermod.is_newer("1.0.0", "1.0.0"), False)
check("разная длина сравнивается по нулям", vermod.is_newer("1.0.1", "1.0"), True)
check("префикс v не мешает", vermod.is_newer("v2.0.0", "1.9.9"), True)


print("ассет-пак")

from aionmeter import assets as assetsmod

# Пака в тестовом окружении может не быть — это законно, и всё обязано
# продолжать работать: иконок просто не будет.
check("отсутствующее имя не находится", assetsmod.skill_icon_path(""), None)
check("пустой код класса не находится", assetsmod.class_icon_path(""), None)
check("таблица скиллов всегда словарь", isinstance(assetsmod.skill_map(), dict), True)
check("таблица предметов всегда словарь", isinstance(assetsmod.items(), dict), True)

# elementalist — имя файла эмблемы в самом клиенте. Без него у спиритмастера
# не находилась иконка класса.
check("у EL есть кандидат elementalist",
      "elementalist" in skilldb.icon_candidates("EL"), True)
check("кандидаты начинаются с кода в нижнем регистре",
      skilldb.icon_candidates("RA")[0], "ra")

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
check("класс по скиллам", by_name["Ann"]["cls_name"], "Ranger")
check("другой класс у другого игрока", by_name["Bob"]["cls_name"], "Sorcerer")
check("без скиллов класса нет", by_name["Cid"]["cls_name"], "")
check("код класса тоже в снимке", by_name["Ann"]["cls"], "RA")
check("у каждого класса есть цвет",
      sorted(skilldb.CLASSES) == sorted(skilldb.COLOURS), True)
check("у каждого класса есть имена файлов иконок",
      sorted(skilldb.CLASSES) == sorted(skilldb.ICON_ALIASES), True)
check("код класса всегда первый кандидат имени файла",
      skilldb.icon_candidates("RA")[0], "ra")
check("псевдонимы имён файлов — кортеж, а не строка",
      all(isinstance(v, tuple) for v in skilldb.ICON_ALIASES.values()), True)
# имена набора, который чаще всего оказывается у людей на диске
_arm = {"glad", "templar", "sin", "ranger", "sorc", "sm",
        "cleric", "chanter", "bard", "gunner", "aethertech"}
check("типовой набор имён покрывает все классы",
      all(_arm & set(skilldb.icon_candidates(c)) for c in skilldb.CLASSES), True)

m18 = Meter(dict(DEFAULTS))
m18.cfg["scope"] = "all"
check("без базы класс не выдумывается", m18.actor_class("Ann"), "")

print("утилита иконок: варианты рангов")

import importlib.util as _ilu
_spec = _ilu.spec_from_file_location(
    "fetch_icons", Path(__file__).parent / "tools" / "fetch_skill_icons.py")
_fi = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_fi)

check("откат идёт от своего ранга вниз",
      _fi.rank_variants("Gale Arrow IV"),
      ["Gale Arrow IV", "Gale Arrow III", "Gale Arrow II", "Gale Arrow I", "Gale Arrow"])
check("скилл без ранга не разбирается",
      _fi.rank_variants("Accurate Hit"), ["Accurate Hit"])
check("первый ранг откатывается только к имени без ранга",
      _fi.rank_variants("Ambush I"), ["Ambush I", "Ambush"])

print("настройки: чтение конфига")

import codecs, json as _json, os as _os, tempfile as _tf
_tmpcfg = Path(_tf.mkdtemp(prefix="aionmeter-cfg-"))
_old_appdata = _os.environ.get("APPDATA")
_os.environ["APPDATA"] = str(_tmpcfg)
try:
    from aionmeter import config as _c
    _path = _c.config_path()
    _path.parent.mkdir(parents=True, exist_ok=True)
    _body = _json.dumps({"dps_window": 42, "metric": "heal"}, ensure_ascii=False)
    # Блокнот и PowerShell пишут UTF-8 с BOM — конфиг должен читаться и так
    _path.write_bytes(codecs.BOM_UTF8 + _body.encode("utf-8"))
    _loaded = _c.load()
    check("конфиг с BOM читается, а не теряется", _loaded["dps_window"], 42)
    check("остальные ключи берутся из умолчаний",
          _loaded["active_gap"], DEFAULTS["active_gap"])
    _path.write_text("{ это не json", "utf-8")
    check("битый конфиг не роняет запуск", _c.load()["dps_window"],
          DEFAULTS["dps_window"])
finally:
    if _old_appdata is not None:
        _os.environ["APPDATA"] = _old_appdata

print("настройки: пути к иконкам подставляются сами")

from aionmeter import config as _cfgmod
_cfg = dict(DEFAULTS)
check("явный путь имеет приоритет",
      _cfgmod.icons_dir({"icons_dir": r"D:\my\icons"}), r"D:\my\icons")
check("несуществующая папка по умолчанию даёт пусто, а не битый путь",
      _cfgmod._resolve_dir("", "заведомо-нет-такой-папки"), "")
check("иконки скиллов берут свой каталог, а не каталог классов",
      _cfgmod.skill_icons_dir({"skill_icons_dir": "X"}), "X")

print("агрегатор: мобы, петы и PvP")

from aionmeter.aggregate import is_player_name
check("ник игрока — одно слово", is_player_name("Steepeek"), True)
check("имя с пробелом игроком не считается", is_player_name("Ulgorn Raider"), False)

m19 = Meter(dict(DEFAULTS))
m19.cfg["scope"] = "all"
m19.feed(parse("2026.08.29 19:00:00", "Ulgorn Raider inflicted 900 damage on Somebody."))
m19.feed(parse("2026.08.29 19:00:01", "Steepeek inflicted 100 damage on Mob."))
check("моб в таблицу урона не попадает",
      [r["display"] for r in m19.snapshot(DAMAGE)["rows"]], ["Steepeek"])
m19.cfg["hide_mobs"] = False
check("с выключенным фильтром моб виден",
      len(m19.snapshot(DAMAGE)["rows"]), 2)

# В PvP опыт даётся и за убитого игрока — мобом его считать нельзя
m20 = Meter(dict(DEFAULTS))
m20.cfg["scope"] = "all"
m20.feed(parse("2026.08.29 19:00:00", "You have gained 500 XP from Enemyguy."))
m20.feed(parse("2026.08.29 19:00:01", "Enemyguy inflicted 300 damage on Ally."))
check("убитый в PvP игрок остаётся в таблице",
      [r["display"] for r in m20.snapshot(DAMAGE)["rows"]], ["Enemyguy"])
check("но опыт за него засчитан", m20.snapshot(DAMAGE)["loot"]["exp"], 500)

m21 = Meter(dict(DEFAULTS))
m21.cfg["scope"] = "all"
m21.feed(parse("2026.08.29 19:00:00", "You have gained 500 XP from Ulgorn Raider."))
check("моб из строки опыта запоминается как моб", "Ulgorn Raider" in m21.mobs, True)

print("агрегатор: доты, лут, сигналы")

# Тик дота автора не содержит. Владельца помним с момента наложения —
# это не догадка, а механика: новый дот перебивает старый.
m22 = Meter(dict(DEFAULTS)); m22.cfg["scope"] = "all"
m22.feed(parse("2026.08.31 19:00:00", "Ann inflicted 500 damage on Mob by using Erosion VI."))
m22.feed(parse("2026.08.31 19:00:02", "Mob received 300 damage due to the effect of Erosion VI."))
by = {r["display"]: r["total"] for r in m22.snapshot(DAMAGE)["rows"]}
check("тик дота приписан тому, кто его наложил", by.get("Ann"), 800)
check("служебной строки без владельца при этом нет", UNATTRIBUTED in by, False)

# Два сорка одним скиллом: урон уходит последнему наложившему
m23 = Meter(dict(DEFAULTS)); m23.cfg["scope"] = "all"
m23.feed(parse("2026.08.31 19:00:00", "Ann inflicted 10 damage on Mob by using Erosion VI."))
m23.feed(parse("2026.08.31 19:00:01", "Mob received 100 damage due to the effect of Erosion VI."))
m23.feed(parse("2026.08.31 19:00:02", "Bob inflicted 10 damage on Mob by using Erosion VI."))
m23.feed(parse("2026.08.31 19:00:03", "Mob received 100 damage due to the effect of Erosion VI."))
by = {r["display"]: r["total"] for r in m23.snapshot(DAMAGE)["rows"]}
check("перебитый дот тикает новому владельцу", (by.get("Ann"), by.get("Bob")), (110, 110))

# Строка наложения эффекта тоже называет автора
m24 = Meter(dict(DEFAULTS)); m24.cfg["scope"] = "all"
m24.feed(parse("2026.08.31 19:00:00",
               "Mob is in the burning state because Cid used Flame Cage V."))
m24.feed(parse("2026.08.31 19:00:01", "Mob received 200 damage due to the effect of Flame Cage V."))
check("автор берётся и из строки наложения",
      {r["display"]: r["total"] for r in m24.snapshot(DAMAGE)["rows"]}.get("Cid"), 200)

# Дот босса по ИГРОКУ — это входящий урон, а не наш
m25 = Meter(dict(DEFAULTS)); m25.cfg["scope"] = "all"
m25.feed(parse("2026.08.31 19:00:00", "Weisti has joined your group."))
m25.feed(parse("2026.08.31 19:00:01", "Weisti received 400 damage due to the effect of Lava Tsunami I."))
check("дот по игроку не попадает в наш урон", m25.snapshot(DAMAGE)["rows"], [])
check("дот по игроку идёт в «получено»",
      m25.snapshot("taken")["rows"][0]["total"], 400)

# Прок с годстоуна владельца не имеет вовсе
m26 = Meter(dict(DEFAULTS)); m26.cfg["scope"] = "all"
m26.feed(parse("2026.08.31 19:00:00",
               "Mob received 50 damage due to the effect of Magical Water Damage Effect."))
check("безымянный прок остаётся отдельной строкой",
      m26.snapshot(DAMAGE)["rows"][0]["display"], UNATTRIBUTED)

# Лут
m27 = Meter(dict(DEFAULTS)); m27.cfg["scope"] = "all"
# Бой нужен: добыча вне боя теперь не засчитывается — см. блок выше.
m27.feed(parse("2026.08.31 18:59:58", "You inflicted 100 damage on Mob."))
m27.feed(parse("2026.08.31 19:00:00", "You have acquired [item:167000522;ver6;;;;]."))
m27.feed(parse("2026.08.31 19:00:01", "You have acquired 5 [item:186000010;ver6;;;;]s."))
m27.feed(parse("2026.08.31 19:00:02", "Weisti has acquired [item:188052667;ver6;;;;]."))
m27.feed(parse("2026.08.31 19:00:03", "Ivar rolled the dice and got a 87."))
items = m27.snapshot(DAMAGE)["items"]
check("свой лут посчитан", sum(items["You"].values()), 6)
check("лут согруппника посчитан отдельно", sum(items["Weisti"].values()), 1)
check("бросок кубика записан", m27.snapshot(DAMAGE)["rolls"], [("Ivar", 87)])

print("строка для игрового чата")

class _FakeEngine:
    paused = False
    def __init__(self, snap): self._s = snap
    def snapshot(self): return self._s
    def reset(self): pass
    def set_paused(self, v): pass


def _copy_text(rows, metric="damage", duration=134):
    from PySide6.QtWidgets import QApplication
    from aionmeter.overlay import Overlay
    app = QApplication.instance() or QApplication([])
    snap = {"rows": rows, "metric": metric, "duration": duration, "loot": {},
            "total": sum(r["total"] for r in rows), "stats": {}}
    cfg = dict(DEFAULTS); cfg["metric"] = metric
    ov = Overlay(_FakeEngine(snap), cfg)
    ov.snapshot = snap
    return ov.copy_text()


def _row(name, total, avg, **kw):
    base = {"name": name, "display": name, "total": total, "avg": avg, "dps": avg,
            "pct": 50.0, "hits": 10, "crit": None, "cls": "", "cls_name": "",
            "is_self": False, "is_party": False, "section": "party", "skills": []}
    base.update(kw)
    return base


_txt = _copy_text([_row("Steepeek", 8_400_000, 1825), _row("Weisti", 5_580_000, 1800)])
check("вид строки для чата", _txt,
      "Damage 2:14 | Steepeek 8.40M (1 825 dps) | Weisti 5.58M (1 800 dps)")
check("на вкладке хила подпись hps, а не dps",
      "hps" in _copy_text([_row("Ann", 1000, 50)], metric="heal"), True)
check("на вкладке добычи пишем штуки и без времени",
      _copy_text([_row("Ann", 42, 0)], metric="loot"), "Loot | Ann 42 pcs")
check("строка без владельца в чат не идёт",
      UNATTRIBUTED in _copy_text([_row("Ann", 100, 10), _row(UNATTRIBUTED, 999, 99)]),
      False)
check("пустая таблица даёт пустую строку", _copy_text([]), "")

_many = _copy_text([_row(f"Player{i:02d}", 1_000_000 + i, 1000) for i in range(10)])
check("длинный список режется по лимиту чата",
      max(len(x) for x in _many.split(chr(10))) <= 240, True)
check("запись не рвётся пополам",
      all(x.count("(") == x.count(")") for x in _many.split(chr(10))), True)

print("база предметов")

import importlib.util as _iu
_sp = _iu.spec_from_file_location(
    "fetch_items", Path(__file__).parent / "tools" / "fetch_item_names.py")
_fi = _iu.module_from_spec(_sp)
_sp.loader.exec_module(_fi)

_xml = ('<item_templates>'
        '<item_template id="186000130" name="Crucible Insignia" level="1" '
        'quality="RARE" item_group="MATERIAL"/>'
        '<item_template id="100000001" name="Circulus\' Sword" level="1" '
        'mask="1" item_group="SWORD" quality="UNIQUE"/>'
        '<item_template id="152000911" name="Magical Aether"/>'
        '</item_templates>')
_items = _fi.parse(_xml)
check("разобрано предметов", len(_items), 3)
check("название с апострофом не ломает разбор",
      _items["100000001"][0], "Circulus' Sword")
check("качество и тип берутся из любого места строки",
      _items["186000130"][1:], ["RARE", "MATERIAL"])
check("предмет без качества не теряется", _items["152000911"], ["Magical Aether", "", ""])

from aionmeter import itemdb
check("у каждого качества есть цвет и название",
      sorted(itemdb.QUALITY_COLOURS) == sorted(itemdb.QUALITY_NAMES), True)
check("неизвестный предмет даёт пустое название", itemdb.lookup("нет такого")[0], "")

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

print("урон по целям и фильтр «только босс»")

def _boss_meter():
    cfg = dict(DEFAULTS)
    cfg["self_name"] = "Steepeek"
    m = Meter(cfg)
    lines = [
        "You inflicted 1000 damage on Modor by using Fang Strike V.",
        "You inflicted 500 damage on Modor Add by using Fang Strike V.",
        "Weisti inflicted 2000 damage on Modor by using Blaze V.",
        "Weisti inflicted 100 damage on Modor Add by using Blaze V.",
    ]
    for i, body in enumerate(lines):
        ev = P(body, ts="2026.09.10 21:14:%02d" % i)
        assert ev is not None, body
        m.feed(ev)
    return cfg, m


cfg_b, m_b = _boss_meter()
snap_b = m_b.snapshot(DAMAGE)
rows_b = {r["name"]: r for r in snap_b["rows"]}
check("главная цель определена", snap_b["boss"], "Modor")
check("разрез по целям ведётся",
      m_b.session.actors[DAMAGE]["You"].top_targets(),
      [("Modor", 1000), ("Modor Add", 500)])
check("урон и удары по одной цели",
      m_b.session.actors[DAMAGE]["You"].on_target("Modor Add"), (500, 1))
check("по цели, которую не били, нули",
      m_b.session.actors[DAMAGE]["You"].on_target("Nobody"), (0, 0))
check("итог без фильтра — по всем целям", rows_b["You"]["total"], 1500)
check("урон по главной цели виден и без фильтра", rows_b["You"]["boss_total"], 1000)

cfg_b["boss_only"] = True
snap_bo = m_b.snapshot(DAMAGE)
rows_bo = {r["name"]: r for r in snap_bo["rows"]}
check("фильтр включён", snap_bo["boss_only"], True)
check("в итоге только урон по боссу", rows_bo["You"]["total"], 1000)
check("удары тоже по боссу", rows_bo["You"]["hits"], 1)
check("доля пересчитана от урона по боссу",
      round(rows_bo["You"]["pct"]), 33)
check("итог таблицы без аддов", snap_bo["total"], 3000)

# Кто по боссу не бил — в таблице «только босс» его быть не должно.
ev_add = P("Ivar inflicted 700 damage on Modor Add by using Blaze V.",
           ts="2026.09.10 21:14:10")
m_b.feed(ev_add)
cfg_b["boss_only"] = False
check("бивший только аддов виден без фильтра",
      "Ivar" in {r["name"] for r in m_b.snapshot(DAMAGE)["rows"]}, True)
cfg_b["boss_only"] = True
check("бивший только аддов скрыт фильтром",
      "Ivar" in {r["name"] for r in m_b.snapshot(DAMAGE)["rows"]}, False)

# Фильтр — только для урона: у хила главной цели нет по смыслу.
cfg_b["metric"] = "heal"
check("на хиле фильтр не действует", m_b.snapshot("heal")["boss_only"], False)

print("сессии: выгрузка и файлы")

from aionmeter import sessions as sessmod

cfg_s, m_s = _boss_meter()
data = m_s.export()
check("выгрузка непустая", bool(data), True)
check("в выгрузке главная цель", data["boss"], "Modor")
check("в выгрузке все участники", len(data["damage"]), 2)
check("свой урон отдельно", data["you"]["total"], 1500)
check("свой урон по боссу отдельно", data["you"]["boss"], 1000)
check("итог сессии", data["total"], 3600)

check("пустая сессия не выгружается", Meter(dict(DEFAULTS)).export(), None)
m_s.reset()
check("после очистки выгружать нечего", m_s.export(), None)

# Файлы кладём во временный APPDATA, чтобы не трогать настоящие настройки.
sess_home = Path(tempfile.mkdtemp(prefix="aionmeter-sessions-"))
old_appdata = os.environ.get("APPDATA")
os.environ["APPDATA"] = str(sess_home)
try:
    path = sessmod.save(data, cfg_s)
    check("файл сессии записан", bool(path and Path(path).is_file()), True)
    listed = sessmod.listing()
    check("сессия читается обратно", len(listed), 1)
    check("состав сохранился", listed[0]["boss"], "Modor")
    check("версия формата проставлена", listed[0]["v"], sessmod.VERSION)

    # Второй файл с тем же временем начала не должен затирать первый.
    sessmod.save(data, cfg_s)
    check("одинаковое время начала не затирает файл", len(sessmod.listing()), 2)

    # Выключенное сохранение не пишет ничего.
    off = dict(cfg_s)
    off["save_sessions"] = False
    check("выключенное сохранение не пишет", sessmod.save(data, off), None)

    # Потолок хранения вытесняет старые файлы.
    keep = dict(cfg_s)
    keep["keep_sessions"] = 2
    for i in range(3):
        shifted = dict(data)
        shifted["start"] = data["start"] + 60 * (i + 1)
        sessmod.save(shifted, keep)
    check("сверх потолка файлы удаляются", len(sessmod.listing()), 2)
finally:
    if old_appdata is None:
        os.environ.pop("APPDATA", None)
    else:
        os.environ["APPDATA"] = old_appdata
    for f in sess_home.rglob("*"):
        try:
            f.unlink()
        except OSError:
            pass
    try:
        (sess_home / "sessions").rmdir()
        sess_home.rmdir()
    except OSError:
        pass

print("исправленные шаблоны и живучесть разбора")

from aionmeter.config import detect_encoding, system_ansi
from aionmeter.parser import MAX_DIGITS, to_int

# Длинные имена мобов: при лимите в 32 символа целые инстансы выпадали.
e = P("You received 1" + NBSP + "164 damage from Pashid Destruction Unit Rearguard.")
check("длинное имя моба разбирается", (e.kind, e.amount, e.incoming),
      ("damage", 1164, True))
check("длинное имя моба целиком", e.actor, "Pashid Destruction Unit Rearguard")

# Тики ловушек рейнджера: автор назван прямо, значит это не сирота.
e = P("Steel Rose Sharpshooter received 653 poisoning damage after you used "
      "Poisoning Trap V Effect.")
check("свой тик ловушки с автором", (e.kind, e.actor, e.amount),
      ("dot", "You", 653))
e = P("Dummy received 200 bleeding damage after Weisti used Wind Cut Down VI.")
check("чужой тик кровотечения с автором", (e.kind, e.actor), ("dot", "Weisti"))

# Урон со снятием бафов: имя цели стоит ПЕРЕД числом.
e = P("Wuaffel used Aegis Breaker I to deal Terath Vanquisher 3" + NBSP
      + "451 damage and dispel some magical buffs.")
check("урон со снятием бафов", (e.actor, e.target, e.amount, e.skill),
      ("Wuaffel", "Terath Vanquisher", 3451, "Aegis Breaker I"))
e = P("Kant used Ignite Aether VII to deal you 753 damage and dispel "
      "some of your magical buffs.")
check("снятие бафов по себе — входящий", (e.target, e.incoming, e.amount),
      ("You", True, 753))
e = P("Loluu used Magic Implosion I to deal Armory Maintenance Surkana  1 "
      "damage and dispel some magical buffs.")
check("двойной пробел перед числом", (e.target, e.amount),
      ("Armory Maintenance Surkana", 1))

# Саммоны в третьем лице.
e = P("Granhildr has summoned Holy Servant to attack Modor by using "
      "Summon Holy Servant V.")
check("чужой саммон с целью", (e.kind, e.actor, e.target),
      ("summon", "Granhildr", "Holy Servant"))
e = P("Sarah summoned Healing Servant by using Summon Healing Servant I.")
check("чужой саммон без цели", (e.actor, e.target), ("Sarah", "Healing Servant"))
e = P("You summoned Water Spirit by using Summon Water Spirit V.")
check("свой саммон помечен как свой", e.actor, "You")

# Наложение эффекта: ключ — имя скилла, под ним придёт тик.
e = P("Steel Rose Veteran received the Delayed Blast effect because "
      "Weisti used Delayed Blast IV.")
check("наложение эффекта с автором",
      (e.kind, e.actor, e.target, e.skill),
      ("applied", "Weisti", "Steel Rose Veteran", "Delayed Blast IV"))

check("вход в игру распознан", P("You changed the connection status to Online.").kind,
      "login")

# Устойчивость: битые данные не должны ронять разбор порции.
check("слишком длинное число не ломает разбор", to_int("9" * (MAX_DIGITS + 1)), 0)
check("нормальное число разбирается", to_int("1" + NBSP + "234"), 1234)
check("несуществующая дата не бросает исключение",
      P("You inflicted 100 damage on X by using Y.", ts="2026.13.45 25:61:61").kind,
      "damage")

# Кодировка: чистый ASCII больше не объявляется utf-8.
check("ASCII-хвост даёт системную страницу",
      detect_encoding(b"You changed the connection status to Online." * 20),
      system_ansi())
check("настоящий utf-8 определяется", detect_encoding("Вы".encode("utf-8")), "utf-8")
check("cp1251 определяется", detect_encoding("Вы".encode("cp1251")), system_ansi())
nbsp_line = (("You inflicted 1" + NBSP + "220 damage on Training Dummy "
              "by using Fang Strike V.").encode("cp1251"))
check("удар с неразрывным пробелом не теряется",
      P(nbsp_line.decode(detect_encoding(nbsp_line), "replace")).amount, 1220)

print("смена персонажа и петы")

cfg_l = dict(DEFAULTS)
m_l = Meter(cfg_l)
for i, body in enumerate([
        "The Glory Points to be deducted for Steepeek are 280.",
        "You changed the connection status to Online.",
        "The Glory Points to be deducted for Weisti are 120."]):
    m_l.feed(P(body, ts="2026.09.10 21:00:%02d" % i))
check("перелогин переопределяет ник", m_l.self_name, "Weisti")

cfg_m = dict(DEFAULTS)
cfg_m["self_name"] = "Steepeek"
m_m = Meter(cfg_m)
m_m.feed(P("You changed the connection status to Online.", ts="2026.09.10 21:00:00"))
check("ник из настроек перелогин не стирает", m_m.self_name, "Steepeek")

cfg_p = dict(DEFAULTS)
cfg_p["self_name"] = "Steepeek"
cfg_p["hide_mobs"] = False
m_p = Meter(cfg_p)
for i, body in enumerate([
        "You summoned Water Spirit by using Summon Water Spirit V.",
        "Water Spirit inflicted 300 damage on Dummy by using Aqua Blast.",
        "Granhildr has summoned Holy Servant to attack Dummy by using Summon Holy Servant V.",
        "Holy Servant inflicted 900 damage on Dummy by using Holy Strike."]):
    m_p.feed(P(body, ts="2026.09.10 21:10:%02d" % i))
pet_rows = {r["name"]: r["total"] for r in m_p.snapshot(DAMAGE)["rows"]}
check("свой пет идёт себе", pet_rows.get("You"), 300)
check("чужой пет идёт своему хозяину", pet_rows.get("Granhildr"), 900)

# Тик после наложения находит автора по имени скилла.
cfg_d = dict(DEFAULTS)
m_d = Meter(cfg_d)
for i, body in enumerate([
        "Steel Rose Veteran received the Delayed Blast effect because Weisti used Delayed Blast IV.",
        "Steel Rose Veteran received 3" + NBSP + "233 damage due to the effect of Delayed Blast IV."]):
    m_d.feed(P(body, ts="2026.09.10 21:20:%02d" % i))
dot_rows = {r["name"]: r["total"] for r in m_d.snapshot(DAMAGE)["rows"]}
check("тик приписан автору наложения", dot_rows.get("Weisti"), 3233)
check("строки «(периодический)» не появилось", UNATTRIBUTED in dot_rows, False)

print("приватность файла нераспознанного")

from aionmeter.engine import Engine

eng_chk = Engine(dict(DEFAULTS))
for body, want in (
        ("[3.LFG] [charname:Bigyahu;1.0 0.6 0.6]: продам меч", True),
        ("Steepeek: го фарм", True),
        ("You Whisper to [charname:Meleze;1.0]: hi", True),
        ("Legion Message: сбор в 20:00", True),
        ("Determination of Absorption II Effect has been activated.", False),
        ("Invalid target.", False),
        ("High Priest Esras received 745 damage due to the effect of Flame Cage V.", False)):
    check(f"чат отсеивается: {body[:34]}", eng_chk._is_chat(body), want)

print("находки аудита: атрибуция и потолки")

# Потолок целей не должен терять именно босса.
cfg_cap = dict(DEFAULTS)
cfg_cap["hide_mobs"] = False
m_cap = Meter(cfg_cap)
for i in range(600):
    m_cap.feed(P(f"You inflicted 100 damage on Mob{i} by using Fang Strike V.",
                 ts="2026.09.10 10:%02d:%02d" % (i // 60, i % 60)))
for i in range(20):
    m_cap.feed(P("You inflicted 100000 damage on Ulsaruk by using Fang Strike V.",
                 ts="2026.09.10 21:%02d:%02d" % (i // 60, i % 60)))
a_cap = m_cap.session.actors[DAMAGE]["You"]
check("цели в разрезе не растут без предела",
      len(a_cap.by_target) <= a_cap.TARGET_CAP, True)
check("босс уцелел в разрезе после переполнения",
      a_cap.on_target("Ulsaruk"), (2000000, 20))
cfg_cap["boss_only"] = True
check("строка не исчезает из таблицы «только босс»",
      len(m_cap.snapshot(DAMAGE)["rows"]), 1)

# Вход в игру не стирает ник: иначе своё попадание считается дважды.
m_log = Meter(dict(DEFAULTS, hide_mobs=False))
m_log.feed(P("The Glory Points to be deducted for Steepeek are 280.",
             ts="2026.09.10 21:00:00"))
m_log.feed(P("You changed the connection status to Online.", ts="2026.09.10 21:00:01"))
m_log.feed(P("You inflicted 500 damage on Dummy by using Fang Strike V.",
             ts="2026.09.10 21:00:05"))
m_log.feed(P("Steepeek inflicted 500 damage on Dummy by using Fang Strike V.",
             ts="2026.09.10 21:00:05"))
check("вход в игру не стирает ник", m_log.self_name, "Steepeek")
check("эхо своего удара не удваивается",
      {r["name"]: r["total"] for r in m_log.snapshot(DAMAGE)["rows"]}, {"You": 500})

# Чужой саммон не отбирает пета с тем же именем.
m_pet = Meter(dict(DEFAULTS, self_name="Steepeek", hide_mobs=False))
for i, body in enumerate([
        "You summoned Holy Servant by using Summon Holy Servant V.",
        "Granhildr has summoned Holy Servant to attack Dummy by using Summon Holy Servant V.",
        "Holy Servant inflicted 900 damage on Dummy by using Holy Strike."]):
    m_pet.feed(P(body, ts="2026.09.10 21:10:%02d" % i))
check("свой пет не уезжает чужому игроку",
      {r["name"]: r["total"] for r in m_pet.snapshot(DAMAGE)["rows"]}, {"You": 900})

# Тик с названным автором идёт автору, а не в «(периодический)».
m_dot = Meter(dict(DEFAULTS, hide_mobs=False))
m_dot.feed(P("Dummy received 653 poisoning damage after Weisti used Poisoning Trap V Effect.",
             ts="2026.09.10 21:20:00"))
dot_rows = {r["name"]: r["total"] for r in m_dot.snapshot(DAMAGE)["rows"]}
check("тик с явным автором идёт ему", dot_rows.get("Weisti"), 653)
check("строки «(периодический)» при явном авторе нет", UNATTRIBUTED in dot_rows, False)

# Скользящее окно считается по кольцу, а не по всей сессии.
m_ring = Meter(dict(DEFAULTS, hide_mobs=False))
for i in range(400):
    m_ring.feed(P("You inflicted 10 damage on Dummy by using Fang Strike V.",
                  ts="2026.09.10 12:%02d:%02d" % (i // 60, i % 60)))
a_ring = m_ring.session.actors[DAMAGE]["You"]
check("кольцо короче полной раскладки", len(a_ring.recent) < len(a_ring.per_sec), True)
check("кольцо не длиннее своего окна",
      len(a_ring.recent) <= a_ring.RECENT_SPAN + 2, True)
check("итог от кольца не пострадал", a_ring.total, 4000)

# «Бой идёт» — про свежесть данных, а не про существование объекта.
check("на старом логе бой не считается идущим",
      m_ring.snapshot(DAMAGE)["active"], False)

# Двухпроходный снимок: итог и доли считаются по ВСЕМ строкам.
cfg_big = dict(DEFAULTS)
cfg_big["hide_mobs"] = False
cfg_big["max_rows"] = 3
m_big = Meter(cfg_big)
for i in range(20):
    m_big.feed(P(f"Player{i:02d} inflicted {100 * (i + 1)} damage on Dummy by using Blaze V.",
                 ts="2026.09.10 13:00:%02d" % i))
snap_big = m_big.snapshot(DAMAGE)
check("итог считается по всем строкам, а не по видимым",
      snap_big["total"], sum(100 * (i + 1) for i in range(20)))
check("скрытые строки посчитаны", snap_big["hidden"], 17)
check("видимых строк ровно столько, сколько просили",
      len([r for r in snap_big["rows"] if r["name"] not in NO_OWNER_NAMES]), 3)
check("доля лидера считается от общей суммы",
      round(snap_big["rows"][0]["pct"], 1), round(100.0 * 2000 / 21000, 1))

print("скорость убийства босса")

_cfgk = dict(DEFAULTS)
_cfgk["self_name"] = "Steepeek"
_cfgk["hide_mobs"] = False
_mk = Meter(_cfgk)


def _at(body, sec):
    _mk.feed(P(body, ts="2026.09.10 20:%02d:%02d" % (sec // 60, sec % 60)))


# Сорок секунд чистим аддов, потом полторы минуты бьём босса и убиваем.
for _i in range(20):
    _at("You inflicted 1000 damage on Small Add by using Fang Strike V.", _i * 2)
for _i in range(30):
    _at("You inflicted 50000 damage on Modor by using Fang Strike V.", 40 + _i * 3)
    _at("Weisti inflicted 40000 damage on Modor by using Blaze V.", 40 + _i * 3)
_at("You have gained 12345 XP from Modor.", 130)

_snapk = _mk.snapshot(DAMAGE)
check("главная цель определена", _snapk["boss"], "Modor")
check("время убийства считается от первого удара ПО НЕЙ",
      _snapk["boss_seconds"], 91)
check("убийство записано вместе со временем",
      _mk.session.kills[-1][0], "Modor")

_fights = _mk.export_fights()
check("бой попал в выгрузку", len(_fights), 1)
_f = _fights[0]
check("в бою указан босс", _f["boss"], "Modor")
check("бой засчитан как убийство", _f["killed"], True)
check("длительность боя — до смерти цели", _f["seconds"], 91)
check("урон в бою — только по боссу", _f["total"], 2700000)
check("свой урон по боссу отделён от урона за бой",
      (_f["players"][0]["total"], _f["players"][0]["all_targets"]),
      (1500000, 1520000))

_datak = _mk.export()
check("бои попадают в файл сессии", len(_datak.get("fights", [])), 1)
check("убийства в файле — с временем", len(_datak["kills"][0]), 2)

# Живой босс: скорости ещё нет, и выдумывать её нельзя.
_mk2 = Meter(dict(DEFAULTS, hide_mobs=False))
_mk2.feed(P("You inflicted 500 damage on Modor by using Fang Strike V.",
            ts="2026.09.10 21:00:00"))
check("у недобитой цели скорости нет", _mk2.snapshot(DAMAGE)["boss_seconds"], None)
check("недобитый бой в выгрузке помечен", _mk2.export_fights()[0]["killed"], False)

print("режим стримера")

try:
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QPixmap
    from PySide6.QtWidgets import QApplication
    from aionmeter.overlay import Overlay

    _app = QApplication.instance() or QApplication([])
    _srow = {"name": "You", "display": "Steepeek", "cls": "RA", "cls_name": "Ranger",
             "section": "party", "total": 8400000, "dps": 12000.0, "avg": 12000.0,
             "hits": 200, "crit": 30.0, "max": 90000, "is_self": True,
             "is_party": True, "skills": [], "buffs": [], "pct": 100.0, "bar": 1.0,
             "boss_total": 4000000, "boss_hits": 100}
    _ssnap = {"rows": [_srow], "metric": "damage", "duration": 60, "total": 8400000,
              "loot": {}, "stats": {}, "active": True, "split": False, "hidden": 0,
              "sections": [{"key": "all", "total": 8400000, "count": 1}]}
    _scfg = dict(DEFAULTS)
    _scfg["click_through"] = True
    _scfg["transparent"] = True
    _sov = Overlay(_FakeEngine(_ssnap), _scfg)
    _sov.snapshot = _ssnap
    _sov.resize(460, 300)

    check("по умолчанию режим выключен", _scfg["streamer"], False)
    _sov.action_toggle_streamer()
    check("режим включается", _scfg["streamer"], True)
    check("клик-сквозь снят: иначе из режима не выйти",
          _scfg["click_through"], False)
    check("прозрачность снята: хромакею нужен сплошной фон",
          _scfg["transparent"], False)

    _pm = QPixmap(_sov.size())
    _sov.render(_pm)
    _zones = [n for n, _r in _sov._hit]
    check("в режиме остаётся только кнопка выхода", _zones, ["btn:stream_off"])
    _exit = next(r for n, r in _sov._hit if n == "btn:stream_off")
    check("кнопка выхода в правом верхнем углу",
          _exit.right() > _sov.width() - 30 and _exit.top() < 30, True)
    check("клик по ней попадает в кнопку",
          _sov._hit_at(QPointF(_exit.center().x(), _exit.center().y())),
          "btn:stream_off")
    check("фон окна — цвет хромакея",
          _pm.toImage().pixelColor(_sov.width() // 2,
                                   _sov.height() - 20).name().lower(),
          _scfg["chroma_color"].lower())

    _sov._activate("btn:stream_off", None)
    check("кнопка выключает режим", _scfg["streamer"], False)
    check("клик-сквозь вернулся как был", _scfg["click_through"], True)
    check("прозрачность вернулась как была", _scfg["transparent"], True)
    check("для режима есть сочетание клавиш",
          bool(DEFAULTS["hotkeys"].get("streamer")), True)
except ImportError:
    print("  (GUI-часть пропущена: нет PySide6)")

print("подсказки у кнопок и вкладок")

from aionmeter.overlay import ACTIONS, HELP, METRIC_TABS, TOOLBAR_RIGHT

_missing = [f"btn:{n}" for n, _t in ACTIONS + TOOLBAR_RIGHT
            if f"btn:{n}" not in HELP]
check("у каждой кнопки есть подсказка", _missing, [])
_missing_tabs = [f"metric:{k}" for k, _l in METRIC_TABS
                 if f"metric:{k}" not in HELP]
check("у каждой вкладки есть подсказка", _missing_tabs, [])
check("подсказка возврата к живым данным есть", "btn:live" in HELP, True)
_no_body = [k for k, (_t, b) in HELP.items() if not b and not k.startswith("col:")]
check("у подсказок есть пояснение, а не одно название", _no_body, [])
_long = [k for k, (_t, b) in HELP.items() if len(b) > 70]
check("пояснения короткие", _long, [])

try:
    from PySide6.QtWidgets import QApplication
    from aionmeter.overlay import Overlay
    _app = QApplication.instance() or QApplication([])
    _cfg_h = dict(DEFAULTS)
    _ovh = Overlay(_FakeEngine({"rows": [], "metric": "damage", "duration": 0,
                                "loot": {}, "total": 0, "stats": {}}), _cfg_h)
    _ovh._hot = "btn:clear"
    _title, _body = _ovh._tip_for("btn:clear")
    check("в заголовке подсказки есть сочетание клавиш",
          _cfg_h["hotkeys"]["reset"] in _title, True)
    check("пояснение на месте", _body.startswith("Clears the table"), True)
    check("для зоны перетаскивания подсказки нет",
          _ovh._tip_for("drag:title"), None)
except ImportError:
    print("  (GUI-часть пропущена: нет PySide6)")

print("открытие сохранённой сессии в обычных вкладках")

_sv = Path(tempfile.mkdtemp(prefix="aionmeter-view-"))
_old_appdata = os.environ.get("APPDATA")
os.environ["APPDATA"] = str(_sv)
try:
    from aionmeter import sessions as _sess
    _data = {
        "start": 1757530000, "end": 1757533600, "duration": 3600, "boss": "Modor",
        "kill_count": 14, "total": 17000000, "self_name": "Steepeek",
        "self_class": "RA", "you": {"total": 9000000, "avg": 2500.0, "boss": 6000000},
        "party": ["Weisti"], "loot": {"exp": 500000},
        "damage": [
            {"name": "You", "cls": "RA", "total": 9000000, "hits": 900, "crits": 300,
             "max": 90000, "active": 3600, "avg": 2500.0,
             "skills": [["Fang Strike V", 3000000]],
             "targets": [["Modor", 6000000], ["Add", 3000000]]},
            {"name": "Weisti", "cls": "WI", "total": 8000000, "hits": 700, "crits": 70,
             "max": 120000, "active": 3600, "avg": 2222.0,
             "skills": [["Blaze V", 4000000]], "targets": [["Modor", 5000000]]}],
        "heal": [{"name": "Weisti", "cls": "WI", "total": 300000, "hits": 40,
                  "crits": 0, "max": 8000, "active": 3600, "avg": 83.0,
                  "skills": [], "targets": []}],
        "taken": [{"name": "You", "cls": "RA", "total": 1200000, "hits": 300,
                   "crits": 0, "max": 30000, "active": 3600, "avg": 333.0,
                   "skills": [], "targets": []}],
    }
    _path = _sess.save(_data, dict(DEFAULTS))
    check("сессия сохранена", bool(_path), True)

    from PySide6.QtWidgets import QApplication
    from aionmeter.overlay import Overlay
    _app = QApplication.instance() or QApplication([])
    _cfg = dict(DEFAULTS)
    _ov2 = Overlay(_FakeEngine({"rows": [], "metric": "damage", "duration": 0,
                                "loot": {}, "total": 0, "stats": {}}), _cfg)
    _ov2.set_metric("sessions")
    _ov2._sessions_at = 0.0
    _ov2._refresh()
    _rows = _ov2.snapshot["rows"]
    check("сессия видна в списке", len(_rows), 1)

    _ov2.open_session(_rows[0]["name"])
    check("клик уводит на вкладку урона", _cfg["metric"], "damage")
    check("урон загружен из файла", _ov2.snapshot["total"], 17000000)
    check("строки игроков на месте", len(_ov2.snapshot["rows"]), 2)
    check("свой разбор по скиллам подгрузился",
          _ov2.snapshot["rows"][0]["skills"], [("Fang Strike V", 3000000)])
    check("крит посчитан из сохранённого",
          round(_ov2.snapshot["rows"][0]["crit"]), 33)
    check("подпись открытой сессии есть",
          bool(_ov2.snapshot.get("saved_label")), True)
    check("бой не считается идущим", _ov2.snapshot["active"], False)

    _ov2.set_metric("heal")
    _ov2._refresh()
    check("хил берётся из той же сессии", _ov2.snapshot["total"], 300000)
    _ov2.set_metric("taken")
    _ov2._refresh()
    check("полученный урон тоже", _ov2.snapshot["total"], 1200000)

    _ov2.close_session_view()
    check("возврат к живым данным", _ov2.snapshot.get("saved_label"), None)
finally:
    if _old_appdata is None:
        os.environ.pop("APPDATA", None)
    else:
        os.environ["APPDATA"] = _old_appdata
    for _f in sorted(_sv.rglob("*"), reverse=True):
        try:
            _f.unlink() if _f.is_file() else _f.rmdir()
        except OSError:
            pass
    try:
        _sv.rmdir()
    except OSError:
        pass

print("привязка к клиенту Origin")

from aionmeter.config import ORIGIN_MAGIC, is_origin_client

_org = Path(tempfile.mkdtemp(prefix="aionmeter-origin-"))
(_org / "Data" / "Items").mkdir(parents=True, exist_ok=True)
(_org / "Data" / "Items" / "items.pak").write_bytes(ORIGIN_MAGIC + b"x" * 64)
check("клиент с паками Origin опознан", is_origin_client(str(_org)), True)

_alien = Path(tempfile.mkdtemp(prefix="aionmeter-alien-"))
(_alien / "Data" / "Items").mkdir(parents=True, exist_ok=True)
(_alien / "Data" / "Items" / "items.pak").write_bytes(b"PK" + b"x" * 64)
check("обычный ZIP-пак Origin-ом не считается", is_origin_client(str(_alien)), False)
check("пустой путь не опознан", is_origin_client(""), False)

for _tmp in (_org, _alien):
    for _f in sorted(_tmp.rglob("*"), reverse=True):
        try:
            _f.unlink() if _f.is_file() else _f.rmdir()
        except OSError:
            pass
    try:
        _tmp.rmdir()
    except OSError:
        pass

print("язык интерфейса")

from aionmeter import skilldb as _sk
check("названия классов английские", _sk.CLASSES["RA"], "Ranger")
check("все классы без кириллицы",
      any(any("Ѐ" <= ch <= "ӿ" for ch in v) for v in _sk.CLASSES.values()),
      False)

print("шапка с названием и версия в подвале")

from aionmeter.version import STAGE, display as version_display

check("версия показывается со стадией", version_display(), f"0.1.0 {STAGE}")
check("стадия — бета", STAGE, "beta")

try:
    from PySide6.QtCore import QPointF
    from PySide6.QtWidgets import QApplication
    from aionmeter.overlay import APP_NAME, Overlay

    _app = QApplication.instance() or QApplication([])
    _ov = Overlay(_FakeEngine({"rows": [], "metric": "damage", "duration": 0,
                               "loot": {}, "total": 0, "stats": {}}), dict(DEFAULTS))
    _ov.resize(460, 560)
    _ov.snapshot = {"rows": [], "metric": "damage", "duration": 0, "loot": {},
                    "total": 0, "stats": {}}
    check("название программы", APP_NAME, "Wingbeat")
    check("заголовок окна назван программой", _ov.windowTitle(), "Wingbeat")
    check("полоса заголовка занимает место", _ov.TITLE_H > 0, True)
    check("обвязка учитывает полосу заголовка",
          _ov.chrome_h > _ov.HEAD_H + _ov.ACT_H + _ov.STATS_H, True)

    # Клики должны попадать в кнопки, а не промахиваться на высоту полосы.
    from PySide6.QtGui import QPixmap
    _pm = QPixmap(_ov.size())
    _ov.render(_pm)
    _inset = _ov.frame_inset()
    _tabs = [(n, r) for n, r in _ov._hit if n.startswith("metric:")]
    check("вкладки зарегистрированы", len(_tabs) >= 4, True)
    _name, _rect = _tabs[1]
    _point = QPointF(_rect.center().x() + _inset,
                     _rect.center().y() + _inset + _ov.TITLE_H)
    check("клик по вкладке попадает в неё", _ov._hit_at(_point), _name)
    _btn = [(n, r) for n, r in _ov._hit if n == "btn:clear"]
    if _btn:
        _point = QPointF(_btn[0][1].center().x() + _inset,
                         _btn[0][1].center().y() + _inset + _ov.TITLE_H)
        check("клик по кнопке попадает в неё", _ov._hit_at(_point), "btn:clear")
except ImportError:
    print("  (GUI-часть пропущена: нет PySide6)")

print()
print(f"пройдено {ok}, провалено {fail}")
sys.exit(1 if fail else 0)
