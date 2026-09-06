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


print("добыча: номера предметов")

m22 = Meter(dict(DEFAULTS))
m22.cfg["scope"] = "all"
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
check("класс по скиллам", by_name["Ann"]["cls_name"], "Рейнджер")
check("другой класс у другого игрока", by_name["Bob"]["cls_name"], "Волшебник")
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
check("строки «(периодический)» при этом нет", "(периодический)" in by, False)

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
      m26.snapshot(DAMAGE)["rows"][0]["display"], "(периодический)")

# Лут
m27 = Meter(dict(DEFAULTS)); m27.cfg["scope"] = "all"
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
      "Урон 2:14 | Steepeek 8.40M (1 825 dps) | Weisti 5.58M (1 800 dps)")
check("на вкладке хила подпись hps, а не dps",
      "hps" in _copy_text([_row("Ann", 1000, 50)], metric="heal"), True)
check("на вкладке добычи пишем штуки и без времени",
      _copy_text([_row("Ann", 42, 0)], metric="loot"), "Добыча | Ann 42 шт")
check("периодический урон в чат не идёт",
      "период" in _copy_text([_row("Ann", 100, 10), _row("(периодический)", 999, 99)]),
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

print()
print(f"пройдено {ok}, провалено {fail}")
sys.exit(1 if fail else 0)
