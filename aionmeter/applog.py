"""Свой лог программы: %APPDATA%\\AionMeter\\aionmeter.log

Нужен ровно для одного: когда у чужого человека что-то не работает, он
присылает файл, и по нему видно, что произошло. Поэтому пишем немного, но
то, что реально помогает: версию, окружение, найденный клиент, размер и
кодировку лога игры, и полные трейсбеки необъяснённых падений.

Ротация простая: при старте, если файл перевалил за предел, он
переименовывается в .1, а прежний .1 удаляется. Двух файлов достаточно —
это диагностика, а не архив.
"""

from __future__ import annotations

import logging
import logging.handlers
import platform
import sys
import traceback
from pathlib import Path

from . import config as cfgmod
from .version import __version__

LOG_NAME = "aionmeter.log"
MAX_BYTES = 512 * 1024
BACKUPS = 1

log = logging.getLogger("aionmeter")
_ready = False


def path() -> Path:
    return cfgmod.config_dir() / LOG_NAME


def setup(debug: bool = False) -> Path:
    """Включить запись. Повторный вызов ничего не делает."""
    global _ready
    if _ready:
        return path()
    _ready = True

    target = path()
    log.setLevel(logging.DEBUG if debug else logging.INFO)
    log.propagate = False
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s",
                            "%Y-%m-%d %H:%M:%S")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            target, maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8")
        handler.setFormatter(fmt)
        log.addHandler(handler)
    except OSError:
        # Программу поставили туда, куда нельзя писать. Не повод падать:
        # без файла просто останется вывод в консоль.
        pass

    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(fmt)
    stream.setLevel(logging.WARNING)
    log.addHandler(stream)

    log.info("=" * 60)
    log.info("AionMeter %s | Python %s | %s", __version__,
             platform.python_version(), platform.platform())
    log.info("настройки: %s", cfgmod.config_dir())
    return target


def install_excepthook() -> None:
    """Необъяснённое исключение — в лог целиком, а не в никуда.

    Qt глотает исключения из слотов, поэтому без этого падение в отрисовке
    выглядит как «окно замерло» и разбирать нечего.
    """
    previous = sys.excepthook

    def hook(kind, value, tb) -> None:
        log.critical("необработанное исключение\n%s",
                     "".join(traceback.format_exception(kind, value, tb)))
        previous(kind, value, tb)

    sys.excepthook = hook


def describe_environment(cfg: dict) -> None:
    """Одна запись со всем, что обычно спрашивают в баг-репорте."""
    from . import assets
    log_path = cfgmod.resolve_log_path(cfg)
    size = ""
    try:
        size = f"{Path(log_path).stat().st_size / 1024 / 1024:.1f} МБ"
    except OSError:
        size = "нет доступа"
    log.info("клиент: %s", cfg.get("game_dir") or "не задан")
    log.info("лог игры: %s (%s)", log_path or "не найден", size)
    log.info("ассет-пак: %s", (assets.root() or "не найден"))
    man = assets.manifest()
    if man:
        log.info("  иконок %s, классов %s, предметов %s, клиент %s",
                 man.get("skill_icons"), man.get("class_icons"),
                 man.get("items"), man.get("client"))
