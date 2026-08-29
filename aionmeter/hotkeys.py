"""Глобальные хоткеи через RegisterHotKey.

Почему именно так, а не через обычные горячие клавиши Qt: игра идёт в
полноэкранном оконном режиме и держит фокус. Окно метра специально создано
с WS_EX_NOACTIVATE и фокус не получает никогда, поэтому клавиатурные события
до него не доходят. RegisterHotKey работает на уровне системы и не требует,
чтобы окно было активным.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

WM_HOTKEY = 0x0312
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN = 0x1, 0x2, 0x4, 0x8
MOD_NOREPEAT = 0x4000

_MODS = {
    "ctrl": MOD_CONTROL, "control": MOD_CONTROL,
    "alt": MOD_ALT, "shift": MOD_SHIFT,
    "win": MOD_WIN, "meta": MOD_WIN,
}

# Клавиши, которым не соответствует ord() символа.
_KEYS = {
    "space": 0x20, "tab": 0x09, "enter": 0x0D, "return": 0x0D, "esc": 0x1B,
    "escape": 0x1B, "insert": 0x2D, "delete": 0x2E, "home": 0x24, "end": 0x23,
    "pgup": 0x21, "pgdn": 0x22, "up": 0x26, "down": 0x28, "left": 0x25,
    "right": 0x27, "backspace": 0x08,
    "num0": 0x60, "num1": 0x61, "num2": 0x62, "num3": 0x63, "num4": 0x64,
    "num5": 0x65, "num6": 0x66, "num7": 0x67, "num8": 0x68, "num9": 0x69,
    "-": 0xBD, "=": 0xBB, "[": 0xDB, "]": 0xDD, ";": 0xBA, "'": 0xDE,
    ",": 0xBC, ".": 0xBE, "/": 0xBF, "`": 0xC0, "\\": 0xDC,
}
for _i in range(1, 25):
    _KEYS[f"f{_i}"] = 0x6F + _i


def parse(combo: str) -> tuple[int, int] | None:
    """'Ctrl+Alt+R' -> (модификаторы, virtual-key). None, если не разобрали."""
    if not combo:
        return None
    mods = 0
    key = None
    for part in combo.replace(" ", "").split("+"):
        low = part.lower()
        if low in _MODS:
            mods |= _MODS[low]
        elif low:
            key = low
    if key is None:
        return None
    if key in _KEYS:
        vk = _KEYS[key]
    elif len(key) == 1:
        vk = ord(key.upper())
    else:
        return None
    if not mods:
        return None          # без модификатора хоткей перехватит игру
    return mods | MOD_NOREPEAT, vk


class HotkeyManager:
    """Регистрирует хоткеи на окно и раздаёт колбэки по WM_HOTKEY."""

    def __init__(self, hwnd: int):
        self.hwnd = hwnd
        self.user32 = ctypes.windll.user32
        self.callbacks: dict[int, callable] = {}
        self.failed: list[str] = []
        self._next_id = 1

    def register(self, combo: str, callback) -> bool:
        parsed = parse(combo)
        if parsed is None:
            if combo:
                self.failed.append(combo)
            return False
        mods, vk = parsed
        hk_id = self._next_id
        if not self.user32.RegisterHotKey(wintypes.HWND(self.hwnd), hk_id, mods, vk):
            # Занят другой программой — не падаем, просто сообщаем в настройках
            self.failed.append(combo)
            return False
        self.callbacks[hk_id] = callback
        self._next_id += 1
        return True

    def handle(self, hk_id: int) -> bool:
        cb = self.callbacks.get(hk_id)
        if cb is None:
            return False
        cb()
        return True

    def unregister_all(self) -> None:
        for hk_id in list(self.callbacks):
            self.user32.UnregisterHotKey(wintypes.HWND(self.hwnd), hk_id)
        self.callbacks.clear()
        self.failed.clear()
        self._next_id = 1
