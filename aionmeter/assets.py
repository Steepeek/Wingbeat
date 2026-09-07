"""Ассет-пак: иконки скиллов и классов, названия предметов.

Пак собирается один раз инструментом сборки из клиента игры и кладётся
рядом с программой. В рантайме здесь только чтение файлов — ни сети, ни
криптографии, ни обращений к клиенту. Если пака нет, всё работает как
раньше: метр показывает буквенные фишки классов и номера предметов.

Порядок поиска: папка пользователя из настроек перебивает пак. Это
сохраняет старую возможность подложить свои картинки.
"""

from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

DIR_NAME = "assets"

_root: Path | None = None
_root_done = False
_skills: dict[str, str] | None = None
_items: dict[str, list] | None = None
_item_icons: dict[str, str] | None = None
_manifest: dict | None = None


def _candidates() -> list[Path]:
    """Где искать пак, от самого специфичного к общему."""
    out: list[Path] = []
    frozen = getattr(sys, "frozen", False)
    if frozen:
        # PyInstaller --onedir: рядом с exe. _MEIPASS нужен для --onefile.
        out.append(Path(sys.executable).parent / DIR_NAME)
        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            out.append(Path(meipass) / DIR_NAME)
    out.append(Path(__file__).resolve().parent.parent / DIR_NAME)
    from . import config as cfgmod
    out.append(cfgmod.config_dir() / DIR_NAME)
    return out


def root() -> Path | None:
    """Папка пака или None. Ищется один раз за запуск."""
    global _root, _root_done
    if _root_done:
        return _root
    _root_done = True
    for path in _candidates():
        if (path / "manifest.json").is_file():
            _root = path
            break
    return _root


def manifest() -> dict:
    global _manifest
    if _manifest is None:
        base = root()
        try:
            _manifest = json.loads((base / "manifest.json").read_text("utf-8"))
        except (OSError, ValueError, TypeError):
            _manifest = {}
    return _manifest


def skill_map() -> dict[str, str]:
    """Отображаемое имя скилла -> имя файла иконки без расширения."""
    global _skills
    if _skills is None:
        base = root()
        try:
            _skills = json.loads((base / "skills.json").read_text("utf-8"))
        except (OSError, ValueError, TypeError):
            _skills = {}
    return _skills


def _load_gz(name: str) -> dict:
    base = root()
    try:
        return json.loads(gzip.decompress((base / name).read_bytes()).decode("utf-8"))
    except (OSError, ValueError, TypeError):
        return {}


def items() -> dict[str, list]:
    """Номер предмета -> [название, качество]."""
    global _items
    if _items is None:
        _items = _load_gz("items.json.gz")
    return _items


def item_icons() -> dict[str, str]:
    """Номер предмета -> имя файла иконки без расширения."""
    global _item_icons
    if _item_icons is None:
        _item_icons = _load_gz("item_icons.json.gz")
    return _item_icons


_by_name: dict[str, str] | None = None


def item_icon_by_name(name: str) -> Path | None:
    """Иконка предмета по НАЗВАНИЮ, а не по номеру.

    Нужна для расходников: строка «You have used <предмет>» называет
    предмет словами, номера в ней нет. Обратный указатель строится один
    раз и лениво — только если такая строка вообще встретилась.
    """
    global _by_name
    base = root()
    if base is None or not name:
        return None
    if _by_name is None:
        icons = item_icons()
        _by_name = {}
        for item_id, row in items().items():
            title = row[0] if row else ""
            if title and item_id in icons:
                _by_name.setdefault(title, icons[item_id])
    stem = _by_name.get(name)
    if not stem:
        return None
    path = base / "items" / (stem + ".png")
    return path if path.is_file() else None


def item_icon_path(item_id: str) -> Path | None:
    base = root()
    if base is None or not item_id:
        return None
    stem = item_icons().get(str(item_id))
    if not stem:
        return None
    path = base / "items" / (stem + ".png")
    return path if path.is_file() else None


def skill_icon_path(display: str) -> Path | None:
    """Путь к иконке по отображаемому имени скилла из лога.

    Хвост « on you» клиент дописывает к имени в строках, адресованных
    игроку. Без его снятия терялось 24 имени и 709 событий на живом логе.
    """
    base = root()
    if base is None or not display:
        return None
    table = skill_map()
    name = table.get(display)
    if name is None and display.lower().endswith(" on you"):
        name = table.get(display[:-7].rstrip())
    if name is None:
        return None
    path = base / "skills" / (name + ".png")
    return path if path.is_file() else None


def ui_icon_path(name: str) -> Path | None:
    """Иконка для самого интерфейса: kinah, exp, autoattack."""
    base = root()
    if base is None or not name:
        return None
    path = base / "ui" / (name + ".png")
    return path if path.is_file() else None


def class_icon_path(code: str) -> Path | None:
    """Путь к эмблеме класса по коду вроде «RA»."""
    base = root()
    if base is None or not code:
        return None
    from . import skilldb
    folder = base / "classes"
    for alias in skilldb.icon_candidates(code):
        path = folder / f"icon_emblem_{alias}.png"
        if path.is_file():
            return path
    return None


def reload() -> None:
    global _root, _root_done, _skills, _items, _manifest, _item_icons
    _root = None
    _root_done = False
    _skills = _items = _manifest = _item_icons = None
    globals()['_by_name'] = None


def describe() -> str:
    """Строка для окна настроек: что подхватилось."""
    base = root()
    if base is None:
        return "не найден"
    man = manifest()
    return (f"{base}\n{man.get('skill_icons', 0)} иконок скиллов, "
            f"{man.get('class_icons', 0)} классов, "
            f"{man.get('items', 0)} предметов")
