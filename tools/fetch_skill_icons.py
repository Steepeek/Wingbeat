"""Разовая загрузка иконок скиллов из вики Aion.

    py tools/fetch_skill_icons.py

Сам метр в сеть не ходит вообще. Эта утилита запускается руками, один раз:
она берёт список скиллов, которые реально встретились в вашем Chat.log,
и складывает картинки в %APPDATA%\\AionMeter\\skillicons. Дальше метр просто
читает эту папку.

Источник — Aion Wiki на Fandom. Файлы там названы точным названием скилла
с рангом («Deadshot V.gif»), то есть ровно тем, что даёт наш парсер, —
поэтому сопоставлять ничего не нужно.

Покрытие неполное: вики остановилась на старом патче, высоких рангов там
нет. Поэтому при промахе пробуем ранги ниже — арт у рангов одинаковый.
На реальном логе это 318 точных попаданий из 543 скиллов и 416 (77%) с
откатом на младший ранг.

Картинки — art NCSoft. В репозиторий они не кладутся и вместе с программой
не раздаются: каждый скачивает себе сам.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aionmeter import config as cfgmod
from aionmeter.parser import iter_records, parse

API = "https://aion.fandom.com/api.php"
UA = "AionMeter-icon-fetcher/1.0 (personal use; github.com/AionMeter)"

ROMAN = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII"]
_RANK_RE = re.compile(r"^(.*) (" + "|".join(ROMAN) + r")$")

PAUSE = 0.35          # между запросами к API
BATCH = 50            # titles за один запрос — предел MediaWiki


def rank_variants(name: str) -> list[str]:
    """Само название, затем ранги ниже, затем название без ранга."""
    m = _RANK_RE.match(name)
    if not m:
        return [name]
    base, rank = m.group(1), m.group(2)
    i = ROMAN.index(rank)
    return [name] + [f"{base} {ROMAN[j]}" for j in range(i - 1, -1, -1)] + [base]


def api_urls(titles: list[str]) -> dict[str, str]:
    """Название -> прямой адрес картинки.

    Путь на CDN можно вычислить и самому (это md5 имени файла), но API всё
    равно опрашивается ради проверки существования и возвращает точный
    адрес — а вычисленный иногда мимо, если файл заливали под другим именем.
    """
    query = urllib.parse.urlencode({
        "action": "query", "prop": "imageinfo", "iiprop": "url",
        "titles": "|".join(f"File:{t}.gif" for t in titles), "format": "json",
    })
    req = urllib.request.Request(f"{API}?{query}", headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.load(resp)
    out = {}
    for page in data.get("query", {}).get("pages", {}).values():
        if "missing" in page or not page.get("imageinfo"):
            continue
        out[page["title"][5:-4]] = page["imageinfo"][0]["url"]
    return out


def skills_from_log(path: str, encoding: str) -> list[str]:
    raw = Path(path).read_bytes().decode(encoding, "replace")
    names: set[str] = set()
    for ts, body in iter_records(raw.split("\r\n")):
        ev = parse(ts, body)
        if ev is not None and ev.kind in ("damage", "heal") and ev.skill:
            names.add(ev.skill)
    return sorted(names)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--log", help="путь к Chat.log")
    # %% — иначе argparse примет %A за спецификатор формата
    ap.add_argument("--out", help="куда складывать (по умолчанию %%APPDATA%%/AionMeter/skillicons)")
    ap.add_argument("--limit", type=int, default=1500, help="потолок числа загрузок")
    args = ap.parse_args()

    cfg = cfgmod.load()
    log_path = args.log or cfgmod.resolve_log_path(cfg)
    if not log_path or not Path(log_path).is_file():
        print("Не найден Chat.log. Укажите его: --log <путь>")
        return 1

    out = Path(args.out) if args.out else cfgmod.config_dir() / "skillicons"
    out.mkdir(parents=True, exist_ok=True)

    encoding = cfg.get("encoding", "auto")
    if encoding == "auto":
        encoding = cfgmod.detect_encoding(Path(log_path).read_bytes()[-65536:])

    skills = skills_from_log(log_path, encoding)
    todo = [s for s in skills if not (out / f"{s}.gif").exists()]
    print(f"скиллов в логе: {len(skills)}, уже скачано: {len(skills) - len(todo)}")
    if not todo:
        print("всё на месте")
        return 0

    # Спрашиваем вики про все варианты рангов разом, потом качаем по одному
    wanted: list[str] = []
    for name in todo:
        for variant in rank_variants(name):
            if variant not in wanted:
                wanted.append(variant)
    print(f"проверяем в вики {len(wanted)} названий…")

    urls: dict[str, str] = {}
    for i in range(0, len(wanted), BATCH):
        try:
            urls.update(api_urls(wanted[i:i + BATCH]))
        except (urllib.error.URLError, OSError, ValueError) as e:
            print(f"  запрос не прошёл: {e}")
            break
        time.sleep(PAUSE)
        print(f"  {min(i + BATCH, len(wanted))}/{len(wanted)}", end="\r", flush=True)
    print(f"  найдено названий: {len(urls)}          ")

    ok = fallback = 0
    for n, name in enumerate(todo, 1):
        if ok + fallback >= args.limit:
            print(f"достигнут потолок --limit {args.limit}, останавливаюсь")
            break
        source = next((v for v in rank_variants(name) if v in urls), None)
        if source is None:
            continue
        req = urllib.request.Request(urls[source],
                                     headers={"User-Agent": UA, "Accept": "image/*"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                blob = resp.read()
        except (urllib.error.URLError, OSError) as e:
            print(f"  {name}: не скачалось ({e})")
            continue
        # Имя файла — исходный скилл, даже если арт взят с младшего ранга
        (out / f"{name}.gif").write_bytes(blob)
        if source == name:
            ok += 1
        else:
            fallback += 1
        if n % 25 == 0:
            print(f"  {n}/{len(todo)}", end="\r", flush=True)
        time.sleep(PAUSE)

    total = ok + fallback
    print(f"\nскачано {total}: точных {ok}, с откатом на младший ранг {fallback}")
    print(f"покрытие {100 * total / max(1, len(todo)):.0f}% от новых скиллов")
    print(f"папка: {out}")
    print("укажите её в настройках метра, поле «Иконки скиллов»")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
