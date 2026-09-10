"""Сессии на диске: одна закрытая сессия — один файл JSON.

Почему файлы, а не база. Сессия пишется РЕДКО (кнопка «Очистить» и выход
из программы), а читается пачкой при открытии вкладки. Это ровно тот
случай, для которого база данных — лишняя зависимость: SQLite потребовал
бы схемы и миграций ради выборки «покажи последние двадцать», а каталог
с файлами человек может открыть, переслать и удалить руками. Файл сессии
заодно и есть тот отчёт, который приложат к сообщению о проблеме.

Формат имени: session-<дата>-<время>.json по времени НАЧАЛА сессии, взятому
из самого лога, а не из системных часов. Так файлы сортируются по имени в
том же порядке, что и по времени, а сессия, разобранная из старого лога,
не притворяется сегодняшней.

Что внутри — см. Meter.export(). Поле "v" — версия формата: когда состав
полей изменится, старые файлы надо будет уметь прочитать, а не выбросить.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from . import config as cfgmod

#: Версия формата файла. Растёт, когда меняется состав полей.
VERSION = 1

#: Потолок на всякий случай: файл сессии за десять дней фарма — это сотни
#: килобайт, и читать такое в список вкладки незачем.
MAX_BYTES = 4 * 1024 * 1024


def sessions_dir() -> Path:
    return cfgmod.config_dir() / "sessions"


def _stamp(epoch: int) -> str:
    return time.strftime("%Y%m%d-%H%M%S", time.localtime(epoch or time.time()))


def save(data: dict, cfg: dict | None = None) -> Path | None:
    """Записать сессию. Возвращает путь к файлу или None, если не вышло.

    Ошибку записи наверх не выпускаем намеренно: сессия — это удобство, а
    не работа метра, и полный диск не повод ронять кнопку «Очистить».
    """
    if not data:
        return None
    cfg = cfg or {}
    if not cfg.get("save_sessions", True):
        return None
    d = sessions_dir()
    try:
        d.mkdir(parents=True, exist_ok=True)
        payload = dict(data)
        payload["v"] = VERSION
        payload["saved_at"] = int(time.time())
        path = d / f"session-{_stamp(data.get('start', 0))}.json"
        # Двух сессий с одной секундой начала не бывает, но лог можно
        # перечитать заново — тогда имя совпадёт, и старый файл терять
        # незачем: дописываем суффикс.
        n = 1
        while path.exists():
            n += 1
            path = d / f"session-{_stamp(data.get('start', 0))}-{n}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), "utf-8")
        _write_head(path, payload)
    except OSError:
        return None
    prune(int(cfg.get("keep_sessions", 200) or 0))
    return path


#: Поля, которых хватает списку на вкладке. Всё остальное — состав по
#: игрокам, скиллам и целям — читается только когда строку раскрыли.
HEAD_FIELDS = ("v", "start", "end", "duration", "boss", "kill_count",
               "total", "you", "self_name", "self_class", "saved_at")


def head_path(path: Path) -> Path:
    return path.with_suffix(".head.json")


def _write_head(path: Path, payload: dict) -> None:
    """Короткая шапка рядом с сессией.

    Зачем отдельный файл. Список на вкладке обновляется, пока она открыта,
    а полный файл сессии — это сотни килобайт: замер показал 1,2-1,9 с
    заморозки окна и +550 МБ памяти на двухстах сессиях. Шапка занимает
    двести байт, и список читается мгновенно.
    """
    head = {k: payload[k] for k in HEAD_FIELDS if k in payload}
    head["players"] = len(payload.get("damage", ()))
    try:
        head_path(path).write_text(json.dumps(head, ensure_ascii=False), "utf-8")
    except OSError:
        pass


def load_head(path: str | Path) -> dict | None:
    """Шапка сессии. Если её нет (файл от прошлой версии) — построить."""
    p = Path(path)
    hp = head_path(p)
    try:
        if hp.is_file():
            data = json.loads(hp.read_text("utf-8"))
            if isinstance(data, dict):
                data["path"] = str(p)
                return data
    except (OSError, ValueError):
        pass
    full = load(p)
    if full is None:
        return None
    _write_head(p, full)
    head = {k: full[k] for k in HEAD_FIELDS if k in full}
    head["players"] = len(full.get("damage", ()))
    head["path"] = str(p)
    return head


def prune(keep: int) -> None:
    """Оставить последние keep файлов. Ноль или меньше — не трогать ничего."""
    if keep <= 0:
        return
    try:
        files = sorted(f for f in sessions_dir().glob("session-*.json")
                       if not f.name.endswith(".head.json"))
    except OSError:
        return
    for path in files[:-keep]:
        for victim in (path, head_path(path)):
            try:
                victim.unlink()
            except OSError:
                pass


def load(path: str | Path) -> dict | None:
    p = Path(path)
    try:
        if p.stat().st_size > MAX_BYTES:
            return None
        data = json.loads(p.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    data["path"] = str(p)
    return data


def listing(limit: int = 100) -> list[dict]:
    """Последние сессии, новые первыми. Читает только шапки.

    Отдельного индекса нет намеренно: индекс — это второй источник правды,
    который рассинхронизируется при первом же ручном удалении файла.
    Вместо него у каждой сессии лежит своя шапка в двести байт, и список
    собирается из них: двести сессий читаются за миллисекунды, тогда как
    полные файлы занимали секунды и сотни мегабайт памяти.
    """
    try:
        files = sorted((f for f in sessions_dir().glob("session-*.json")
                        if not f.name.endswith(".head.json")), reverse=True)
    except OSError:
        return []
    out = []
    for path in files[:limit]:
        data = load_head(path)
        if data:
            out.append(data)
    return out


def delete(path: str | Path) -> bool:
    p = Path(path)
    ok = False
    for victim in (p, head_path(p)):
        try:
            victim.unlink()
            ok = True
        except OSError:
            pass
    return ok
