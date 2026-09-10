"""Одна точка правды о версии.

Читается сборкой, окном настроек и проверкой обновлений. Формат — обычный
semver без префикса: сравнение с тегами GitHub идёт по кортежу чисел, а не
по строке, иначе "0.10.0" оказалось бы меньше "0.9.0".
"""

from __future__ import annotations

__version__ = "0.1.0"

#: Стадия готовности. Пустая строка = релиз. Пока метр не прошёл проверку
#: на чужих машинах и на других серверах, честнее говорить об этом прямо
#: в окне, а не только в описании релиза.
STAGE = "beta"


def display() -> str:
    """Версия так, как её видит человек: «0.1.0 beta»."""
    return f"{__version__} {STAGE}".strip()

#: Куда ходить за обновлениями. Пусто = проверка выключена.
REPO = "Steepeek/AionMeter"


def as_tuple(text: str) -> tuple[int, ...]:
    """'v1.2.3' / '1.2.3-beta' -> (1, 2, 3). Нечисловой хвост отбрасывается."""
    core = text.lstrip("vV").split("-")[0].split("+")[0]
    out = []
    for part in core.split("."):
        digits = ""
        for ch in part:
            if not ch.isdigit():
                break
            digits += ch
        out.append(int(digits) if digits else 0)
    return tuple(out)


def is_newer(remote: str, local: str = __version__) -> bool:
    """Строго новее. Одинаковой длины кортежи сравниваются поэлементно."""
    a, b = as_tuple(remote), as_tuple(local)
    size = max(len(a), len(b))
    return a + (0,) * (size - len(a)) > b + (0,) * (size - len(b))
