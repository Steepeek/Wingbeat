"""База «скилл → класс», собираемая из вашего клиента Aion.

Класс персонажа в Chat.log не пишется. Но имя строки скилла в самом клиенте
его кодирует: `STR_SKILL_RA_MovingShot_G1` = «Gale Arrow I», где `RA` —
рейнджер. Файл `strings/client_strings_skill.xml` лежит внутри
`L10N/<язык>/data/data.pak`, а это обычный ZIP — читается без ключей.

Проверено на живом логе: 4653 однозначных скилла, класс каждого из 14
активных игроков определился без единого конфликтующего голоса.

База собирается на машине пользователя из его собственной установки игры и
кладётся в %APPDATA%. В репозиторий она не входит: это данные клиента, и
раздавать их вместе с программой нельзя.
"""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path

from . import config as cfgmod

#: Префикс имени строки скилла -> класс. Разобрано по client_strings_skill.xml.
CLASSES: dict[str, str] = {
    "FI": "Гладиатор",
    "KN": "Темплар",
    "AS": "Ассасин",
    "RA": "Рейнджер",
    "WI": "Волшебник",
    "EL": "Спиритмастер",
    "PR": "Клирик",
    "CH": "Чантер",
    "Ba": "Бард",
    "Gu": "Стрелок",
    "RI": "Аэротех",
}

#: Цвет класса. Своя палитра, а не из игры: нужен контраст на тёмном фоне
#: и различимость соседних строк, а не соответствие оригиналу.
COLOURS: dict[str, str] = {
    "FI": "#d2544a",
    "KN": "#c0a94f",
    "AS": "#a86ad0",
    "RA": "#57c476",
    "WI": "#5596e6",
    "EL": "#8570db",
    "PR": "#e4d88f",
    "CH": "#4ec7ad",
    "Ba": "#e277b6",
    "Gu": "#d98a44",
    "RI": "#8fa3b8",
}

_ROW_RE = re.compile(
    r"<name>STR_SKILL_([A-Za-z0-9]+)_[^<]*</name>\s*<body>([^<]*)</body>")

DB_NAME = "skills.json"


def db_path() -> Path:
    return cfgmod.config_dir() / DB_NAME


def find_data_pak(game_dir: str) -> Path | None:
    """Ищет data.pak с таблицей скиллов среди языковых папок клиента."""
    root = Path(game_dir) / "L10N"
    if not root.is_dir():
        return None
    candidates = sorted(root.glob("*/data/data.pak"), key=lambda p: p.stat().st_size,
                        reverse=True)
    for pak in candidates:
        try:
            with zipfile.ZipFile(pak) as z:
                if "strings/client_strings_skill.xml" in z.namelist():
                    return pak
        except (OSError, zipfile.BadZipFile):
            continue
    return None


def build(game_dir: str) -> dict:
    """Собирает базу из клиента. Бросает исключение, если не получилось."""
    pak = find_data_pak(game_dir)
    if pak is None:
        raise FileNotFoundError(
            "не найден L10N/<язык>/data/data.pak с таблицей скиллов")
    with zipfile.ZipFile(pak) as z:
        raw = z.read("strings/client_strings_skill.xml")

    text = raw.decode("utf-16-le" if raw[:2] == b"\xff\xfe" else "utf-8", "replace")

    # Одно и то же название может встречаться у нескольких классов
    # («Advanced Sword Training I» есть и у рейнджера, и у темплара).
    # Такие записи выбрасываем: неоднозначный голос хуже отсутствующего.
    seen: dict[str, set[str]] = {}
    for prefix, body in _ROW_RE.findall(text):
        if prefix not in CLASSES:
            continue
        name = body.strip()
        if not name or name[0].isdigit():
            continue
        seen.setdefault(name, set()).add(prefix)

    skills = {name: next(iter(codes)) for name, codes in seen.items() if len(codes) == 1}
    return {"source": str(pak), "skills": skills}


def save(data: dict) -> None:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    tmp.replace(path)


def load() -> dict[str, str]:
    """Готовая база или пустой словарь, если её ещё не собрали."""
    try:
        return json.loads(db_path().read_text("utf-8")).get("skills", {})
    except (OSError, ValueError):
        return {}


def ensure(game_dir: str) -> dict[str, str]:
    """Отдаёт базу, собирая её при первом запуске."""
    skills = load()
    if skills or not game_dir:
        return skills
    try:
        data = build(game_dir)
    except Exception:                      # noqa: BLE001 - без базы метр работает
        return {}
    save(data)
    return data["skills"]
