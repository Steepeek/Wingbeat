"""Проверка новой версии на GitHub Releases.

Сеть здесь — единственная во всей программе, и она необязательная: любой
отказ означает «ничего не известно», а не ошибку. Проверка идёт в отдельном
потоке, потому что до ответа сервера может пройти несколько секунд, а окно
метра обязано появиться сразу.

Скачиванием и установкой не занимаемся намеренно: тихо подменять exe у
человека — плохая идея, а рисовать прогресс-бар ради одного архива в год
не стоит того. Показываем строку и ссылку.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

from .version import REPO, __version__, is_newer

TIMEOUT = 8
API = "https://api.github.com/repos/{repo}/releases/latest"

#: Результат последней проверки: None — ещё не проверяли или не вышло.
latest: dict | None = None


def releases_url(repo: str = REPO) -> str:
    return f"https://github.com/{repo}/releases/latest"


def is_newer_than_current(tag: str) -> bool:
    """Обёртка, чтобы вызывающим не тащить version напрямую."""
    return is_newer(tag)


def fetch(repo: str = REPO, timeout: int = TIMEOUT) -> dict | None:
    """Данные последнего релиза или None. Исключений не выпускает."""
    if not repo:
        return None
    try:
        req = urllib.request.Request(
            API.format(repo=repo),
            headers={"User-Agent": f"AionMeter/{__version__}",
                     "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None
    tag = str(data.get("tag_name") or "")
    if not tag:
        return None
    return {"tag": tag, "name": str(data.get("name") or tag),
            "url": str(data.get("html_url") or releases_url(repo)),
            "notes": str(data.get("body") or "")}


def check_async(on_newer, repo: str = REPO) -> None:
    """Проверить в фоне и позвать on_newer(info), если вышла новее.

    Колбэк вызывается из чужого потока, поэтому в Qt его нельзя дёргать
    напрямую — вызывающая сторона обязана перебросить в главный поток.
    """
    def run() -> None:
        global latest
        info = fetch(repo)
        if info is None:
            return
        latest = info
        if is_newer(info["tag"]):
            try:
                on_newer(info)
            except Exception:
                pass

    threading.Thread(target=run, name="update-check", daemon=True).start()
