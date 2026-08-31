"""Разовая сборка базы названий предметов.

    py tools/fetch_item_names.py

В Chat.log предмет записан только номером:
`You have acquired [item:167000522;ver6;;;;]`. Таблица «номер -> название»
лежит в `Data/Items/items.pak`, а он на приватных серверах зашифрован
(OADTENC1); в незашифрованной части клиента этих номеров нет вовсе.

Зато есть открытые эмуляторы Aion — у них те же самые номера предметов, и
файл `item_templates.xml` лежит открытым текстом:

    <item_template id="186000130" name="Crucible Insignia" quality="RARE" .../>

Берём его у beyond-aion (GPL-3.0, самый живой из эмуляторов), выжимаем
номер, название и качество и складываем в %APPDATA%\\AionMeter\\items.json.
Проверено на живом логе: 231 из 234 предметов опознаны, это 99 %.

Сам метр в сеть не ходит: эта утилита запускается руками и один раз.
База в репозиторий не кладётся — это игровые данные.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aionmeter import config as cfgmod

URL = ("https://raw.githubusercontent.com/beyond-aion/aion-server/master/"
       "game-server/data/static_data/items/item_templates.xml")

#: Из строки шаблона нужны только номер, название и качество. Разбираем
#: регуляркой, а не XML-парсером: файл 55 МБ, дерево целиком в память класть
#: незачем.
ROW = re.compile(
    r'<item_template id="(?P<id>\d+)" name="(?P<name>[^"]*)"'
    r'(?P<rest>[^>]*)')
QUALITY = re.compile(r'quality="([A-Z]+)"')
GROUP = re.compile(r'item_group="([A-Z_]+)"')

DB_NAME = "items.json"


def db_path() -> Path:
    return cfgmod.config_dir() / DB_NAME


def parse(text: str) -> dict:
    items = {}
    for m in ROW.finditer(text):
        rest = m.group("rest")
        q = QUALITY.search(rest)
        g = GROUP.search(rest)
        items[m.group("id")] = [m.group("name"), q.group(1) if q else "",
                                g.group(1) if g else ""]
    return items


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--file", help="локальный item_templates.xml вместо загрузки")
    ap.add_argument("--url", default=URL)
    args = ap.parse_args()

    if args.file:
        print(f"читаю {args.file}…")
        text = Path(args.file).read_text("utf-8", errors="replace")
    else:
        print(f"качаю {args.url}\n(около 55 МБ, один раз)")
        try:
            req = urllib.request.Request(
                args.url, headers={"User-Agent": "AionMeter-itemdb/1.0"})
            with urllib.request.urlopen(req, timeout=180) as resp:
                blob = resp.read()
        except (urllib.error.URLError, OSError) as e:
            print(f"не скачалось: {e}")
            return 1
        print(f"получено {len(blob) / 1024 / 1024:.0f} МБ")
        text = blob.decode("utf-8", "replace")

    items = parse(text)
    if not items:
        print("в файле не нашлось ни одного item_template — формат изменился?")
        return 1

    out = db_path()
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps({"source": args.file or args.url, "items": items},
                              ensure_ascii=False, separators=(",", ":")), "utf-8")
    tmp.replace(out)
    print(f"\nсобрано предметов: {len(items):,}".replace(",", " "))
    print(f"файл: {out}  ({out.stat().st_size / 1024 / 1024:.1f} МБ)")
    print("окно добычи подхватит названия при следующем открытии")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
