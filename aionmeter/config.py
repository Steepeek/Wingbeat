"""Настройки: чтение, запись, значения по умолчанию, автопоиск игры."""

from __future__ import annotations

import json
import os
from pathlib import Path

APP_NAME = "AionMeter"

DEFAULTS: dict = {
    # --- источник данных ---
    "game_dir": "",              # корень клиента (там, где лежит Chat.log)
    "log_path": "",              # переопределение пути к логу; пусто = game_dir/Chat.log
    "encoding": "auto",          # auto | cp1251 | utf-8 | cp1252
    "self_name": "",             # свой ник; пусто = определить автоматически
    "show_own_nick": False,      # подписывать свою строку ником вместо «You»
    # Свой класс, определённый в прошлый раз. Нужен на старте: пока он
    # неизвестен, разбор хила не может отличить мой хил от чужого.
    "self_class": "",

    # --- расчёт ---
    "dps_window": 10,            # окно "текущего" DPS, секунд
    "encounter_timeout": 12,     # тишина, после которой бой считается законченным
    "active_gap": 8,             # разрыв, после которого игрок считается неактивным
    "kill_grace": 3,             # сколько секунд после смерти цели добираем DoT

    # --- что показывать ---
    "metric": "damage",          # damage | heal | taken
    "mode": "session",           # session (копится до очистки) | encounter (текущий бой)
    "scope": "all",              # показываем всех, кто наносил урон
    "hide_mobs": True,
    "merge_pets": True,          # урон питомцев приписывать владельцу
    "count_reflect": False,      # считать урон щита-отражателя (числа фиктивны)
    # Клиент пишет одну и ту же строку и на дроп с моба, и на взятое
    # со склада или из почты. Отличаем по обстановке: настоящий дроп
    # падает в бою, склад и почта — в городе.
    "loot_in_combat": True,      # в добычу идёт только выпавшее в бою
    "loot_window": 25,           # сколько секунд после боя ещё считаем дропом
    "check_updates": True,       # смотреть на GitHub, вышла ли версия новее
    "show_actions": True,        # полоса крупных кнопок под вкладками
    "action_size": 48,           # сторона кнопки действия, пикселей
    # Один размер на все иконки: эмблема класса, скилл, предмет,
    # опыт, кинара. От него же считаются высоты строк.
    "icon_size": 37,             # 20-64
    "show_stats_strip": True,    # полоска опыта и кинары под кнопками
    "columns": ["dmg", "dps", "pct", "hits"],
    "max_rows": 12,
    "icons_dir": "",             # папка с иконками классов; пусто = без иконок
    "skill_icons_dir": "",       # папка с иконками скиллов (см. tools/fetch_skill_icons.py)
    "show_loot": True,          # строка внизу: опыт, AP, кинах, убийства


    # --- внешний вид ---
    "transparent": False,        # сквозная прозрачность фона (для оверлея поверх игры)
    "opacity": 1.0,
    "font_size": 12,
    "click_through": False,
    "always_on_top": True,
    "window": {"x": 80, "y": 80, "w": 400, "h": 340},

    # --- хоткеи (пусто = выключен) ---
    "hotkeys": {
        "reset": "Ctrl+Alt+R",
        "click_through": "Ctrl+Alt+T",
        "hide": "Ctrl+Alt+H",
        "copy": "Ctrl+Alt+C",
        "pause": "Ctrl+Alt+P",
    },

    "poll_ms": 250,
    # Ноль = метр открывается пустым и считает только то, что произошло
    # после запуска. Так и ожидают: увидеть при открытии чужой бой
    # получасовой давности — это выглядит поломкой, а не удобством.
    "backfill_kb": 0,            # сколько хвоста лога читать при старте, КБ
}


def config_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / APP_NAME


def config_path() -> Path:
    return config_dir() / "config.json"


def load() -> dict:
    cfg = json.loads(json.dumps(DEFAULTS))  # глубокая копия
    p = config_path()
    if p.exists():
        try:
            # utf-8-sig, а не utf-8: Блокнот и PowerShell пишут UTF-8 с BOM,
            # и на обычном utf-8 разбор падает — настройки молча теряются,
            # программа откатывается на значения по умолчанию.
            saved = json.loads(p.read_text("utf-8-sig"))
        except (OSError, ValueError):
            saved = {}
        for k, v in saved.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
    return cfg


def save(cfg: dict) -> None:
    d = config_dir()
    d.mkdir(parents=True, exist_ok=True)
    tmp = config_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), "utf-8")
    tmp.replace(config_path())


# --- поиск игры -----------------------------------------------------------

_CANDIDATE_HINTS = ("aion", "origin", "gameforge", "innova", "ncsoft")


def icons_dir(cfg: dict) -> str:
    """Папка с иконками классов. Пустая настройка = папка по умолчанию.

    Так человеку не нужно ничего вписывать: утилита и установщик кладут
    файлы туда же, куда программа смотрит.
    """
    return _resolve_dir(cfg.get("icons_dir"), "icons")


def skill_icons_dir(cfg: dict) -> str:
    """Папка с иконками скиллов (её наполняет tools/fetch_skill_icons.py)."""
    return _resolve_dir(cfg.get("skill_icons_dir"), "skillicons")


def _resolve_dir(configured: str | None, default_name: str) -> str:
    if configured:
        return configured
    path = config_dir() / default_name
    return str(path) if path.is_dir() else ""


def resolve_log_path(cfg: dict) -> str:
    if cfg.get("log_path"):
        return cfg["log_path"]
    if cfg.get("game_dir"):
        return str(Path(cfg["game_dir"]) / "Chat.log")
    return ""


def autodetect_log() -> str:
    """Ищет Chat.log в типичных местах. Возвращает '' если не нашёл.

    Смотрим сначала туда, где уже прописан путь у существующих инструментов,
    потом перебираем диски. Глубина ограничена, чтобы не сканировать всё.
    """
    for path in _from_known_tools():
        if os.path.isfile(path):
            return path

    for root in _drive_roots():
        for depth_dir in _shallow_dirs(root, max_depth=4):
            name = os.path.basename(depth_dir).lower()
            if not any(h in name for h in _CANDIDATE_HINTS):
                continue
            candidate = os.path.join(depth_dir, "Chat.log")
            if os.path.isfile(candidate):
                return candidate
    return ""


def _from_known_tools():
    """Путь к игре, уже записанный другими программами."""
    appdata = os.environ.get("APPDATA", "")
    # Aion RainMeter хранит LogPath в settings.xml
    arm = Path(appdata) / "Aion Rainmeter" / "settings.xml"
    if arm.exists():
        try:
            import re
            text = arm.read_text("utf-8", errors="replace")
            for m in re.finditer(r"<LogPath>([^<]+)</LogPath>", text):
                p = m.group(1).strip()
                if p:
                    yield os.path.join(p, "Chat.log")
        except OSError:
            pass


def _drive_roots():
    if os.name != "nt":
        yield str(Path.home())
        return
    import string
    for letter in string.ascii_uppercase:
        root = f"{letter}:\\"
        if os.path.isdir(root):
            yield root


def _shallow_dirs(root: str, max_depth: int):
    """Обход в ширину с ограничением глубины и пропуском системных папок."""
    skip = {"windows", "$recycle.bin", "system volume information", "programdata",
            "appdata", "node_modules", "temp", "tmp"}
    frontier = [(root, 0)]
    while frontier:
        path, depth = frontier.pop(0)
        if depth >= max_depth:
            continue
        try:
            with os.scandir(path) as it:
                for entry in it:
                    if not entry.is_dir(follow_symlinks=False):
                        continue
                    if entry.name.lower() in skip or entry.name.startswith("."):
                        continue
                    yield entry.path
                    frontier.append((entry.path, depth + 1))
        except OSError:
            continue


# --- кодировка ------------------------------------------------------------

def detect_encoding(sample: bytes) -> str:
    """Определяет кодировку по куску лога.

    Настройке из чужого конфига доверять нельзя, а UTF-8 однозначно
    отличается от однобайтовых по валидности последовательностей.
    """
    if not sample:
        return "cp1251"
    try:
        sample.decode("utf-8")
        # Чистый ASCII валиден и как utf-8, и как cp1251 — разницы нет.
        return "utf-8"
    except UnicodeDecodeError:
        return "cp1251"
