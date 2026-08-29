"""Консольный режим — проверка ядра без графики.

    py console.py            живая таблица, обновляется на лету
    py console.py --once     разобрать весь лог и напечатать отчёт
    py console.py --scope all --metric heal
"""

from __future__ import annotations

import argparse
import os
import sys
import time

# Консоль Windows по умолчанию не в UTF-8 — иначе русский текст превращается в мусор
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass
if os.name == "nt":
    os.system("")   # включает обработку ANSI-последовательностей в cmd/PowerShell

from aionmeter import config as cfgmod
from aionmeter.engine import Engine, load_history

BAR = "█"


def fmt(n: float) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 10_000:
        return f"{n / 1000:.1f}k"
    return f"{n:,.0f}".replace(",", " ")


def render(snap: dict, health: str = "") -> str:
    out = []
    metric = {"damage": "УРОН", "heal": "ХИЛ", "taken": "ПОЛУЧЕНО"}[snap["metric"]]
    head = f"{metric}   бой {snap['duration']}с"
    if snap.get("target"):
        head += f"   цель: {snap['target']}"
    if snap.get("kills"):
        head += f"   убито: {snap['kills']}"
    if health:
        head += f"   разбор: {health}"
    out.append(head)
    out.append("-" * 78)
    out.append(f"{'#':<3}{'игрок':<22}{'всего':>9}{'DPS':>8}{'сред':>8}{'%':>6}{'уд.':>6}{'крит':>7}")
    for i, r in enumerate(snap["rows"], 1):
        mark = "*" if r["is_self"] else (" " if r["is_party"] else "·")
        crit = f"{r['crit']:.0f}%" if r["crit"] is not None else "—"
        out.append(
            f"{i:<3}{mark}{r['name'][:20]:<21}{fmt(r['total']):>9}{fmt(r['dps']):>8}"
            f"{fmt(r['avg']):>8}{r['pct']:>5.1f}%{r['hits']:>6}{crit:>7}"
        )
    if snap.get("hidden"):
        out.append(f"   … ещё {snap['hidden']} скрыто (лимит max_rows)")
    out.append("-" * 78)
    out.append(f"    {'ИТОГО':<21}{fmt(snap['total']):>9}")
    if snap.get("error"):
        out.append(f"!! {snap['error']}")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="разобрать весь лог и выйти")
    ap.add_argument("--scope", choices=["party", "all"])
    ap.add_argument("--metric", choices=["damage", "heal", "taken"])
    ap.add_argument("--log", help="путь к Chat.log")
    args = ap.parse_args()

    cfg = cfgmod.load()
    if args.log:
        cfg["log_path"] = args.log
    if args.scope:
        cfg["scope"] = args.scope
    if args.metric:
        cfg["metric"] = args.metric

    if not cfgmod.resolve_log_path(cfg):
        print("Ищу Chat.log…", flush=True)
        found = cfgmod.autodetect_log()
        if not found:
            print("Не нашёл. Укажите путь: py console.py --log <путь к Chat.log>")
            return 1
        cfg["log_path"] = found
        cfgmod.save(cfg)
        print(f"Нашёл: {found}")

    if args.once:
        t0 = time.perf_counter()
        meter = load_history(cfg)
        # В офлайне интересен весь лог, а не последний бой
        snap = meter.snapshot(cfg["metric"], whole=True)
        print(render(snap))
        print(f"\nразобрано за {time.perf_counter() - t0:.1f}с; "
              f"группа: {', '.join(snap['party']) or '—'}; "
              f"свой ник: {snap['self_name'] or '?'}")
        return 0

    eng = Engine(cfg)
    if not eng.start():
        print(eng.error)
        return 1
    print(f"Читаю {cfgmod.resolve_log_path(cfg)} ({eng.encoding}). Ctrl+C для выхода.\n")
    try:
        while True:
            time.sleep(1.0)
            snap = eng.snapshot()
            sys.stdout.write("\x1b[2J\x1b[H")
            print(render(snap, eng.health()))
    except KeyboardInterrupt:
        pass
    finally:
        eng.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
