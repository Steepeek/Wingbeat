"""База названий предметов: номер -> название, качество, тип.

Наполняется утилитой tools/fetch_item_names.py и лежит в %APPDATA%. Если её
нет, окно добычи показывает номера — программа от этого не ломается.
"""

from __future__ import annotations

import json

from . import config as cfgmod

DB_NAME = "items.json"

#: Цвета качества предметов — как в самой игре: обычный белый, дальше
#: зелёный, синий, золотой, оранжевый, красный.
QUALITY_COLOURS: dict[str, str] = {
    "JUNK": "#8A8F96",
    "COMMON": "#E9EEF3",
    "RARE": "#66C06A",
    "LEGEND": "#5596E6",
    "UNIQUE": "#E0B34A",
    "EPIC": "#E07A3C",
    "MYTHIC": "#D2544A",
}

#: Слова взяты из самого клиента (STR_ITEMQUALITY_*), а не переведены на
#: слух: игрок видит в игре ровно эти, и расхождение сбивало бы с толку.
QUALITY_NAMES: dict[str, str] = {
    "JUNK": "Junk",
    "COMMON": "Common",
    "RARE": "Superior",
    "LEGEND": "Heroic",
    "UNIQUE": "Fabled",
    "EPIC": "Eternal",
    "MYTHIC": "Mythic",
}

_cache: dict[str, list] | None = None


def load() -> dict[str, list]:
    """Готовая база или пустой словарь. Читается один раз за запуск.

    Основа — ассет-пак, собранный из клиента. Файл в %APPDATA% кладётся
    ПОВЕРХ: он появляется только если человек собрал базу сам утилитой,
    и его выбор должен быть последним.
    """
    global _cache
    if _cache is not None:
        return _cache
    from . import assets
    _cache = dict(assets.items())
    try:
        raw = json.loads((cfgmod.config_dir() / DB_NAME).read_text("utf-8"))
        _cache.update(raw.get("items", {}))
    except (OSError, ValueError):
        pass
    return _cache


def reload() -> dict[str, list]:
    global _cache
    _cache = None
    return load()


def lookup(item_id: str) -> tuple[str, str, str]:
    """(название, качество, тип). Название пустое, если предмета нет в базе."""
    row = load().get(str(item_id))
    if not row:
        return "", "", ""
    name = row[0] if len(row) > 0 else ""
    quality = row[1] if len(row) > 1 else ""
    group = row[2] if len(row) > 2 else ""
    return name, quality, group
