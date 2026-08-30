"""Окно метра.

Устройство простое и без сюрпризов:

    ┌──────────────────────────────────────┐
    │ [▶] [■] [⟲] [⧉] [⚙]            [×]  │  кнопки
    │  Урон   Хил   Получено   02:14  101k │  вкладки + состояние
    ├──────────────────────────────────────┤
    │ 1  Weisti       37.9k  2 946/с  51%  │
    │ 2  Steepeek     21.9k  1 697/с  30%  │  своя строка выделена
    ├──────────────────────────────────────┤
    │ опыт 1.5M · AP 129 · убито 3         │
    └──────────────────────────────────────┘

Показываем всех, кто наносил урон. Своя строка подсвечена полосой слева и
цветом — этого достаточно, чтобы найти себя взглядом.

Что нужно от Windows, когда окно работает поверх игры:

* WS_EX_NOACTIVATE — окно не забирает фокус. Без этого клик по нему
  сворачивает игру в оконном полноэкранном режиме.
* WS_EX_TRANSPARENT — мышь проходит сквозь окно (режим «клик насквозь»).
* Периодический SetWindowPos(HWND_TOPMOST) — игра сбрасывает чужой z-order
  при переключении фокуса, поэтому «поверх всех» приходится подтверждать.

Поверх ЭКСКЛЮЗИВНОГО полноэкранного режима не рисуется ничего: DWM тогда
отключён. Нужен оконный полноэкранный (borderless).
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from PySide6.QtCore import QPoint, QRect, Qt, QTimer
from PySide6.QtGui import (QAction, QColor, QFont, QFontMetrics, QGuiApplication,
                           QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygon)
from PySide6.QtWidgets import QApplication, QMenu, QWidget

from pathlib import Path

from . import config as cfgmod
from . import hotkeys as hk
from . import skilldb

IS_WINDOWS = hasattr(ctypes, "windll")

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
HWND_TOPMOST = -1
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010

# --- палитра --------------------------------------------------------------

BG = QColor(19, 24, 30)
BG_TRANSPARENT = QColor(16, 20, 26, 225)
BG_HEAD = QColor(27, 34, 42)
BG_STRIP = QColor(23, 29, 36)
BTN_BG = QColor(38, 48, 59)
BTN_BG_HOVER = QColor(52, 65, 79)
BTN_EDGE = QColor(255, 255, 255, 30)
LINE = QColor(255, 255, 255, 28)
LINE_STRONG = QColor(255, 255, 255, 46)
TEXT = QColor(226, 232, 236)
TEXT_DIM = QColor(146, 158, 169)
TEXT_FAINT = QColor(108, 120, 132)
ACCENT = QColor(237, 165, 73)
GREEN = QColor(96, 194, 128)
RED = QColor(232, 106, 106)
ROW_SELF_BG = QColor(237, 165, 73, 34)
BAR_SELF = QColor(237, 165, 73, 92)
BAR_OTHER = QColor(120, 150, 180, 52)
BAR_SKILL = QColor(120, 140, 165, 44)

#: Полупрозрачные версии цветов классов для полос
_CLASS_BAR: dict[tuple[str, int], QColor] = {}


#: Кэш иконок классов: код -> QPixmap нужного размера или None
_ICON_CACHE: dict[tuple[str, str, int], object] = {}
_ICON_EXT = (".png", ".dds", ".bmp", ".jpg")


def class_icon(icons_dir: str, code: str, size: int):
    """Иконка класса из папки пользователя или None.

    Ничего не скачиваем и ничего не кладём в репозиторий: иконки — это art
    NCSoft. Человек указывает свою папку сам, файлы остаются у него.
    """
    if not icons_dir or not code:
        return None
    key = (icons_dir, code, size)
    if key in _ICON_CACHE:
        return _ICON_CACHE[key]
    pm = None
    folder = Path(icons_dir)
    if folder.is_dir():
        wanted = skilldb.icon_candidates(code)
        by_stem = {}
        try:
            for f in folder.iterdir():
                if f.suffix.lower() in _ICON_EXT:
                    by_stem.setdefault(f.stem.lower(), f)
        except OSError:
            by_stem = {}
        for name in wanted:
            f = by_stem.get(name)
            if f is None:
                continue
            loaded = QPixmap(str(f))
            if not loaded.isNull():
                pm = loaded.scaled(size, size, Qt.KeepAspectRatio,
                                   Qt.SmoothTransformation)
            break
    _ICON_CACHE[key] = pm
    return pm


def skill_icon(icons_dir: str, name: str, size: int):
    """Иконка скилла из локальной папки или None.

    Папку наполняет отдельная утилита tools/fetch_skill_icons.py, запускаемая
    руками. Сам метр в сеть не ходит.
    """
    if not icons_dir or not name:
        return None
    key = (icons_dir, "skill:" + name, size)
    if key in _ICON_CACHE:
        return _ICON_CACHE[key]
    pm = None
    for ext in _ICON_EXT:
        f = Path(icons_dir) / (name + ext)
        if f.is_file():
            loaded = QPixmap(str(f))
            if not loaded.isNull():
                pm = loaded.scaled(size, size, Qt.KeepAspectRatio,
                                   Qt.SmoothTransformation)
            break
    _ICON_CACHE[key] = pm
    return pm


def class_colour(code: str, alpha: int = 255) -> QColor | None:
    """Цвет класса или None, если класс не определён."""
    if not code:
        return None
    key = (code, alpha)
    c = _CLASS_BAR.get(key)
    if c is None:
        hexrgb = skilldb.COLOURS.get(code)
        if not hexrgb:
            return None
        c = QColor(hexrgb)
        c.setAlpha(alpha)
        _CLASS_BAR[key] = c
    return c


METRIC_TABS = (("damage", "Урон"), ("heal", "Хил"), ("taken", "Получено"))
METRIC_TITLE = {"damage": "Урон", "heal": "Хил", "taken": "Полученный урон"}

#: name, подпись под курсором
TOOLBAR = (
    ("start", "Старт"),
    ("stop", "Стоп"),
    ("clear", "Очистить"),
    ("copy", "Скопировать в чат"),
    ("settings", "Настройки"),
)


def fmt(n: float) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 10_000:
        return f"{n / 1000:.1f}k"
    if n >= 1000:
        return f"{n:,.0f}".replace(",", " ")
    return f"{n:.0f}"


def make_icon() -> QIcon:
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(20, 26, 33))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(2, 2, 60, 60, 12, 12)
    p.setBrush(ACCENT)
    for i, h in enumerate((16, 28, 40)):
        p.drawRect(14 + i * 13, 50 - h, 8, h)
    p.end()
    return QIcon(pm)


class Overlay(QWidget):
    BTN = 28              # сторона квадратной кнопки
    BAR_H = 38            # ряд кнопок
    TAB_H = 28            # ряд вкладок и состояния
    FOOTER_H = 20

    def __init__(self, engine, cfg: dict, on_settings=None, on_quit=None):
        super().__init__(None)
        self.engine = engine
        self.cfg = cfg
        self.on_settings = on_settings
        self.on_quit = on_quit
        self.snapshot: dict = {"rows": [], "loot": {}, "total": 0, "duration": 0,
                               "metric": cfg.get("metric", "damage"), "stats": {}}
        self.selected = ""
        self._drag: QPoint | None = None
        self._resizing = False
        self._hot = ""
        self._hit: list[tuple[str, QRect]] = []
        self._row_rects: list[tuple[QRect, dict]] = []
        self.hotkeys: hk.HotkeyManager | None = None

        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
            | Qt.Tool | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setWindowTitle("AionMeter")
        self.setMouseTracking(True)

        w = cfg["window"]
        self.setGeometry(w["x"], w["y"], w["w"], w["h"])
        self.setMinimumSize(300, 150)
        self._apply_font()
        self._apply_translucency()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh)
        self.timer.start(250)

        self.topmost_timer = QTimer(self)
        self.topmost_timer.timeout.connect(self._keep_on_top)
        self.topmost_timer.start(2000)

    # -- внешний вид --

    def _apply_translucency(self) -> None:
        transparent = bool(self.cfg.get("transparent"))
        self.setAttribute(Qt.WA_TranslucentBackground, transparent)
        self.setWindowOpacity(self.cfg.get("opacity", 1.0) if transparent else 1.0)

    def apply_appearance(self) -> None:
        """Смена прозрачности меняет тип нативного окна, поэтому его надо
        пересоздать, а стили и хоткеи навесить заново."""
        was_visible = self.isVisible()
        self.hide()
        self._apply_translucency()
        self._apply_font()
        if was_visible:
            self.show()
        self.apply_window_flags()
        self.setup_hotkeys()
        self.update()

    def _apply_font(self) -> None:
        size = self.cfg.get("font_size", 12)
        self.font_body = QFont("Segoe UI", size)
        self.font_self = QFont("Segoe UI", size, QFont.DemiBold)
        self.font_tab = QFont("Segoe UI", size, QFont.DemiBold)
        self.font_small = QFont("Segoe UI", max(7, size - 3))
        self.font_num = QFont("Consolas", size - 1)
        self.font_skill = QFont("Segoe UI", max(7, size - 3))
        self.row_h = QFontMetrics(self.font_body).height() + 9
        self.skill_h = QFontMetrics(self.font_skill).height() + 3

    @property
    def head_h(self) -> int:
        return self.BAR_H + self.TAB_H

    @property
    def footer_h(self) -> int:
        return self.FOOTER_H if self.cfg.get("show_loot", True) else 0

    # -- Win32 --

    @property
    def hwnd(self) -> int:
        return int(self.winId())

    def _ex_style(self, add: int = 0, remove: int = 0) -> None:
        if not IS_WINDOWS:
            return
        u = ctypes.windll.user32
        get = getattr(u, "GetWindowLongPtrW", u.GetWindowLongW)
        setl = getattr(u, "SetWindowLongPtrW", u.SetWindowLongW)
        get.restype = ctypes.c_longlong
        setl.restype = ctypes.c_longlong
        setl.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_longlong]
        style = get(wintypes.HWND(self.hwnd), GWL_EXSTYLE)
        setl(wintypes.HWND(self.hwnd), GWL_EXSTYLE, (style | add) & ~remove)

    def apply_window_flags(self) -> None:
        add = WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
        if self.cfg.get("click_through"):
            self._ex_style(add=add | WS_EX_TRANSPARENT)
        else:
            self._ex_style(add=add, remove=WS_EX_TRANSPARENT)

    def _keep_on_top(self) -> None:
        if not IS_WINDOWS or not self.cfg.get("always_on_top", True):
            return
        ctypes.windll.user32.SetWindowPos(
            wintypes.HWND(self.hwnd), wintypes.HWND(HWND_TOPMOST),
            0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)

    def setup_hotkeys(self) -> None:
        if not IS_WINDOWS:
            return
        if self.hotkeys is not None:
            self.hotkeys.unregister_all()
        self.hotkeys = hk.HotkeyManager(self.hwnd)
        binds = self.cfg.get("hotkeys", {})
        self.hotkeys.register(binds.get("reset", ""), self.action_clear)
        self.hotkeys.register(binds.get("click_through", ""), self.action_toggle_click)
        self.hotkeys.register(binds.get("hide", ""), self.action_toggle_hide)
        self.hotkeys.register(binds.get("copy", ""), self.action_copy)
        self.hotkeys.register(binds.get("pause", ""), self.action_toggle_pause)

    def nativeEvent(self, event_type, message):
        if self.hotkeys is not None and event_type == b"windows_generic_MSG":
            try:
                msg = wintypes.MSG.from_address(int(message))
            except (TypeError, ValueError):
                return False, 0
            if msg.message == hk.WM_HOTKEY and self.hotkeys.handle(msg.wParam):
                return True, 0
        return False, 0

    # -- действия --

    def action_start(self) -> None:
        self.engine.set_paused(False)
        self._refresh()

    def action_stop(self) -> None:
        self.engine.set_paused(True)
        self._refresh()

    def action_toggle_pause(self) -> None:
        self.engine.set_paused(not self.engine.paused)
        self._refresh()

    def action_clear(self) -> None:
        self.engine.reset()
        self.selected = ""
        self._refresh()

    def action_toggle_click(self) -> None:
        self.cfg["click_through"] = not self.cfg.get("click_through")
        self.apply_window_flags()
        self.update()

    def action_toggle_hide(self) -> None:
        self.setVisible(not self.isVisible())

    def set_metric(self, metric: str) -> None:
        self.cfg["metric"] = metric
        self.selected = ""
        self._refresh()

    def action_copy(self) -> None:
        """Строка для вставки в игровой чат (лимит около 255 символов)."""
        snap = self.snapshot
        rows = snap.get("rows", [])
        if not rows:
            return
        dur = snap.get("duration", 0)
        parts = [f"{METRIC_TITLE.get(snap.get('metric', 'damage'), 'Урон')} "
                 f"{dur // 60}:{dur % 60:02d}:"]
        for i, r in enumerate(rows[:8], 1):
            parts.append(f"{i}.{r['display']} {fmt(r['total'])} "
                         f"({fmt(r['avg'])}dps {r['pct']:.0f}%)")
        lines, line = [], ""
        for token in parts:
            if len(line) + len(token) + 1 > 250:
                lines.append(line)
                line = token
            else:
                line = f"{line} {token}".strip()
        if line:
            lines.append(line)
        QApplication.clipboard().setText("\n".join(lines))

    # -- отрисовка --

    def _refresh(self) -> None:
        self.snapshot = self.engine.snapshot()
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()

        if self.cfg.get("transparent"):
            path = QPainterPath()
            path.addRoundedRect(0, 0, w, h, 6, 6)
            p.fillPath(path, BG_TRANSPARENT)
        else:
            p.fillRect(0, 0, w, h, BG)
            p.setPen(QPen(LINE_STRONG, 1))
            p.drawRect(0, 0, w - 1, h - 1)

        snap = self.snapshot
        self._hit = []
        self._paint_toolbar(p, w, snap)
        self._paint_tabs(p, w, snap)

        bottom = h - 4 - self.footer_h
        self._row_rects = []
        y = self.head_h
        rows = snap.get("rows", [])
        if not rows:
            p.setFont(self.font_small)
            p.setPen(TEXT_DIM)
            msg = snap.get("error") or ("на паузе — нажмите «Старт»"
                                        if snap.get("paused") else "ждём боевых событий…")
            p.drawText(QRect(14, y + 12, w - 28, 48), Qt.AlignHCenter | Qt.TextWordWrap, msg)
        else:
            for i, r in enumerate(rows):
                if y + self.row_h > bottom:
                    break
                self._paint_row(p, i, r, y, w)
                self._row_rects.append((QRect(0, y, w, self.row_h), r))
                y += self.row_h
                if self.selected == r["name"]:
                    y = self._paint_skills(p, r, y, w, bottom)

        if self.footer_h:
            self._paint_footer(p, w, h, snap)
        self._paint_grip(p, w, h)

    def _paint_toolbar(self, p: QPainter, w: int, snap: dict) -> None:
        p.fillRect(QRect(0, 0, w, self.BAR_H), BG_HEAD)
        paused = bool(snap.get("paused"))
        top = (self.BAR_H - self.BTN) // 2
        x = 6
        for name, _label in TOOLBAR:
            rect = QRect(x, top, self.BTN, self.BTN)
            self._hit.append((f"btn:{name}", rect))
            lit = (name == "start" and not paused) or (name == "stop" and paused)
            self._paint_button(p, rect, name, hot=self._hot == f"btn:{name}", lit=lit)
            x += self.BTN + 5

        rect = QRect(w - 6 - self.BTN, top, self.BTN, self.BTN)
        self._hit.append(("btn:close", rect))
        self._paint_button(p, rect, "close", hot=self._hot == "btn:close")
        rect = QRect(w - 11 - self.BTN * 2, top, self.BTN, self.BTN)
        self._hit.append(("btn:menu", rect))
        self._paint_button(p, rect, "menu", hot=self._hot == "btn:menu")

    def _paint_button(self, p: QPainter, rect: QRect, name: str,
                      hot: bool = False, lit: bool = False) -> None:
        p.setPen(Qt.NoPen)
        p.setBrush(BTN_BG_HOVER if hot else BTN_BG)
        p.drawRoundedRect(rect, 3, 3)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(BTN_EDGE, 1))
        p.drawRoundedRect(rect, 3, 3)

        colour = TEXT
        if lit:
            colour = GREEN if name == "start" else ACCENT
        elif name == "close" and hot:
            colour = RED
        self._draw_icon(p, name, rect, colour)

    def _draw_icon(self, p: QPainter, name: str, rect: QRect, colour: QColor) -> None:
        """Значки рисуются примитивами, а не глифами шрифта: символы вроде
        ⚙ и ↺ есть не во всех шрифтах и молча превращаются в квадратики."""
        cx, cy = rect.center().x() + 1, rect.center().y() + 1
        p.setPen(QPen(colour, 1.5))
        p.setBrush(Qt.NoBrush)
        if name == "start":
            p.setBrush(colour)
            p.setPen(Qt.NoPen)
            p.drawPolygon(QPolygon([QPoint(cx - 3, cy - 6), QPoint(cx + 6, cy),
                                    QPoint(cx - 3, cy + 6)]))
        elif name == "stop":
            p.setBrush(colour)
            p.setPen(Qt.NoPen)
            p.drawRect(QRect(cx - 5, cy - 5, 10, 10))
        elif name == "clear":
            p.drawArc(QRect(cx - 6, cy - 6, 12, 12), 50 * 16, 280 * 16)
            p.drawLine(cx + 5, cy - 7, cx + 5, cy - 2)
            p.drawLine(cx + 5, cy - 7, cx + 9, cy - 6)
        elif name == "copy":
            p.drawRect(QRect(cx - 6, cy - 6, 8, 8))
            p.drawRect(QRect(cx - 2, cy - 2, 8, 8))
        elif name == "settings":
            # Три ползунка: на 28 пикселях читается лучше шестерёнки
            p.setBrush(colour)
            for i, (dy, knob) in enumerate(((-5, 2), (0, -2), (5, 4))):
                p.drawLine(cx - 7, cy + dy, cx + 7, cy + dy)
                p.setPen(Qt.NoPen)
                p.drawRect(QRect(cx + knob - 1, cy + dy - 3, 3, 6))
                p.setPen(QPen(colour, 1.5))
            p.setBrush(Qt.NoBrush)
        elif name == "menu":
            for dy in (-4, 0, 4):
                p.drawLine(cx - 6, cy + dy, cx + 6, cy + dy)
        elif name == "close":
            p.drawLine(cx - 5, cy - 5, cx + 5, cy + 5)
            p.drawLine(cx + 5, cy - 5, cx - 5, cy + 5)

    def _paint_tabs(self, p: QPainter, w: int, snap: dict) -> None:
        top = self.BAR_H
        if not self.cfg.get("transparent"):
            p.fillRect(QRect(0, top, w, self.TAB_H), BG_STRIP)

        p.setFont(self.font_tab)
        fm = QFontMetrics(self.font_tab)
        cur = self.cfg.get("metric", "damage")
        x = 8
        for key, label in METRIC_TABS:
            tw = fm.horizontalAdvance(label) + 14
            rect = QRect(x, top, tw, self.TAB_H)
            self._hit.append((f"metric:{key}", rect))
            if key == cur:
                p.setPen(ACCENT)
                p.drawText(rect, Qt.AlignCenter, label)
                p.fillRect(QRect(x + 4, top + self.TAB_H - 3, tw - 8, 2), ACCENT)
            else:
                p.setPen(TEXT if self._hot == f"metric:{key}" else TEXT_FAINT)
                p.drawText(rect, Qt.AlignCenter, label)
            x += tw

        # Справа — итог, слева от него состояние. Под курсором вместо
        # состояния показываем название кнопки: подсказка без всплывашек.
        p.setFont(self.font_num)
        total_text = fmt(snap.get("total", 0))
        p.setPen(ACCENT)
        p.drawText(QRect(0, top, w - 9, self.TAB_H),
                   Qt.AlignRight | Qt.AlignVCenter, total_text)
        total_w = QFontMetrics(self.font_num).horizontalAdvance(total_text) + 20

        hint = dict(TOOLBAR + (("menu", "Ещё"), ("close", "Выход"))).get(
            self._hot.partition(":")[2] if self._hot.startswith("btn:") else "")
        if hint:
            status, colour = hint, TEXT
        elif snap.get("paused"):
            status, colour = "на паузе", ACCENT
        else:
            dur = snap.get("duration", 0)
            status = (f"{dur // 3600}:{dur // 60 % 60:02d}:{dur % 60:02d}" if dur >= 3600
                      else f"{dur // 60}:{dur % 60:02d}")
            n = len(snap.get("rows", []))
            if n:
                status += f"   {n} {_plural(n, 'игрок', 'игрока', 'игроков')}"
            colour = TEXT_FAINT
        p.setFont(self.font_small)
        p.setPen(colour)
        fms = QFontMetrics(self.font_small)
        p.drawText(QRect(x + 10, top, max(20, w - x - total_w - 10), self.TAB_H),
                   Qt.AlignRight | Qt.AlignVCenter,
                   fms.elidedText(status, Qt.ElideRight, max(20, w - x - total_w - 12)))

        p.setPen(QPen(LINE, 1))
        p.drawLine(0, top + self.TAB_H, w, top + self.TAB_H)

    def _paint_row(self, p: QPainter, i: int, r: dict, y: int, w: int) -> None:
        is_self = r["is_self"]
        if is_self:
            p.fillRect(QRect(1, y, w - 2, self.row_h), ROW_SELF_BG)

        bar_w = int((w - 10) * max(0.0, min(1.0, r.get("bar", 0))))
        # Цвет полосы = цвет класса: строку узнаёшь по цвету, не читая имя.
        colour = class_colour(r.get("cls", ""), 90 if is_self else 64)
        if colour is None:
            colour = BAR_SELF if is_self else BAR_OTHER
        p.fillRect(QRect(5, y + 2, bar_w, self.row_h - 4), colour)
        if is_self:
            p.fillRect(QRect(1, y, 3, self.row_h), ACCENT)      # полоса слева
        if self.selected == r["name"]:
            p.setPen(QPen(LINE_STRONG, 1))
            p.setBrush(Qt.NoBrush)
            p.drawRect(QRect(4, y + 1, w - 9, self.row_h - 3))

        # Своя строка набрана полужирным, поэтому и мерить её надо тем же
        # шрифтом — иначе метка класса налезает на ник.
        font = self.font_self if is_self else self.font_body
        p.setFont(font)
        p.setPen(ACCENT if is_self else TEXT)
        cols_w = sum(cw for _k, cw in self._columns(w))
        fm = QFontMetrics(font)
        icon_size = self.row_h - 8
        icon = class_icon(cfgmod.icons_dir(self.cfg), r.get("cls", ""), icon_size)
        x_name = 11
        if icon is not None:
            p.drawPixmap(11, y + 4, icon)
            x_name = 13 + icon.width()

        mark = "▾ " if self.selected == r["name"] else ""
        name = fm.elidedText(f"{mark}{i + 1}  {r['display']}", Qt.ElideRight,
                             max(60, w - 9 - x_name - cols_w))
        p.drawText(x_name, y + self.row_h - 8, name)
        # Название класса — только если иконки нет: иначе строка теснится,
        # а класс и так виден.
        cls_colour = None if icon is not None else class_colour(r.get("cls", ""))
        if cls_colour and r.get("cls_name"):
            x_cls = x_name + 2 + fm.horizontalAdvance(name)
            room = w - 12 - cols_w - x_cls
            if room > 30:
                p.setFont(self.font_small)
                p.setPen(cls_colour)
                p.drawText(x_cls, y + self.row_h - 8,
                           QFontMetrics(self.font_small).elidedText(
                               r["cls_name"], Qt.ElideRight, room))

        # Каждая колонка в своей ячейке, а не склейкой в строку: иначе числа
        # разной длины не выстраиваются по вертикали и таблицу не прочитать.
        p.setFont(self.font_num)
        x = w - 10
        for key, cell_w in reversed(self._columns(w)):
            x -= cell_w
            value = self._cell_text(key, r)
            if not value:
                continue
            p.setPen(TEXT_DIM if key in ("pct", "hits", "crit") else TEXT)
            p.drawText(QRect(x, y, cell_w - 6, self.row_h),
                       Qt.AlignRight | Qt.AlignVCenter, value)

    def _paint_skills(self, p: QPainter, r: dict, y: int, w: int, bottom: int) -> int:
        """Разбор по скиллам для раскрытой строки.

        У автоатак имени скилла в логе нет вовсе — они идут одной строкой.
        """
        skills = r.get("skills") or []
        auto = max(0, r["total"] - sum(v for _k, v in skills))
        items = list(skills[:6])
        if auto > 0:
            items.append(("автоатака", auto))
        if not items:
            items = [("разбивки по скиллам нет", 0)]
        top = max((v for _k, v in items), default=1) or 1

        p.setFont(self.font_skill)
        fm = QFontMetrics(self.font_skill)
        icons_dir = cfgmod.skill_icons_dir(self.cfg)
        icon_size = self.skill_h - 2
        for label, value in items:
            if y + self.skill_h > bottom:
                break
            p.fillRect(QRect(26, y + 1, int((w - 46) * value / top), self.skill_h - 2), BAR_SKILL)
            icon = skill_icon(icons_dir, label, icon_size)
            x_label = 30
            if icon is not None:
                p.drawPixmap(28, y + 1, icon)
                x_label = 30 + icon.width()
            p.setPen(TEXT_DIM)
            p.drawText(x_label, y + self.skill_h - 4,
                       fm.elidedText(label, Qt.ElideRight, int(w * 0.52)))
            if value:
                p.setPen(TEXT_FAINT)
                p.drawText(QRect(0, y, w - 11, self.skill_h), Qt.AlignRight | Qt.AlignVCenter,
                           f"{fmt(value)}   {100.0 * value / (r['total'] or 1):.0f}%")
            y += self.skill_h
        return y

    def _paint_footer(self, p: QPainter, w: int, h: int, snap: dict) -> None:
        top = h - self.FOOTER_H
        if not self.cfg.get("transparent"):
            p.fillRect(QRect(0, top, w, self.FOOTER_H), BG_STRIP)
        p.setPen(QPen(LINE, 1))
        p.drawLine(0, top, w, top)

        loot = snap.get("loot", {})
        parts = []
        for key, label in (("exp", "опыт"), ("ap", "AP"), ("kinah_in", "кинах")):
            if loot.get(key):
                parts.append(f"{label} {fmt(loot[key])}")
        if loot.get("kills"):
            parts.append(f"убито {loot['kills']}")
        if loot.get("pvp_kills"):
            parts.append(f"PvP {loot['pvp_kills']}")
        deaths = loot.get("deaths", 0) + loot.get("pvp_deaths", 0)
        if deaths:
            parts.append(f"смертей {deaths}")

        p.setFont(self.font_small)
        p.setPen(TEXT_FAINT)
        fm = QFontMetrics(self.font_small)
        p.drawText(11, top + self.FOOTER_H - 6,
                   fm.elidedText(" · ".join(parts) or "добычи пока нет",
                                 Qt.ElideRight, w - 70))
        stats = snap.get("stats", {})
        if stats.get("read"):
            p.drawText(QRect(0, top, w - 11, self.FOOTER_H), Qt.AlignRight | Qt.AlignVCenter,
                       f"{stats.get('parsed', 0)}/{stats['read']}")

    def _columns(self, w: int) -> list[tuple[str, int]]:
        """Ширины колонок — по фактическому содержимому, а не по доле окна.

        Доля окна давала абсурд: пять колонок с короткими числами вроде «4»
        и «0%» забирали 84% ширины, а ник обрезался до «Pocket…». Считаем по
        самому длинному значению в колонке и оставляем нику не меньше трети.
        """
        known = ("dmg", "dps", "pct", "hits", "crit")
        order = [c for c in known if c in self.cfg.get("columns", ["dmg", "dps", "pct"])]
        if not order:
            return []
        fm = QFontMetrics(self.font_num)
        rows = self.snapshot.get("rows", ())
        widths = []
        for key in order:
            longest = max((fm.horizontalAdvance(self._cell_text(key, r)) for r in rows),
                          default=0)
            widths.append([key, max(34, longest + 14)])

        # Ник не должен ужиматься ниже трети окна — иначе таблица нечитаема
        limit = int((w - 20) * 0.62)
        total = sum(cw for _k, cw in widths)
        if total > limit and total:
            scale = limit / total
            for pair in widths:
                pair[1] = max(30, int(pair[1] * scale))
        return [(k, cw) for k, cw in widths]

    @staticmethod
    def _cell_text(key: str, r: dict) -> str:
        if key == "dmg":
            return fmt(r["total"])
        if key == "dps":
            return f"{fmt(r['dps'])}/с" if r["dps"] >= 1 else "·"
        if key == "pct":
            return f"{r['pct']:.0f}%"
        if key == "hits":
            return str(r["hits"])
        if key == "crit":
            return f"{r['crit']:.0f}%" if r.get("crit") is not None else "—"
        return ""

    def _paint_grip(self, p: QPainter, w: int, h: int) -> None:
        p.setPen(QPen(TEXT_FAINT, 1))
        for off in (3, 7):
            p.drawLine(w - off - 4, h - 4, w - 4, h - off - 4)

    # -- мышь --

    def _hit_at(self, pos) -> str:
        point = pos.toPoint() if hasattr(pos, "toPoint") else pos
        for name, rect in self._hit:
            if rect.contains(point):
                return name
        return ""

    def _row_at(self, pos) -> dict | None:
        point = pos.toPoint() if hasattr(pos, "toPoint") else pos
        for rect, row in self._row_rects:
            if rect.contains(point):
                return row
        return None

    def _in_grip(self, pos) -> bool:
        return pos.x() > self.width() - 16 and pos.y() > self.height() - 16

    def mousePressEvent(self, e) -> None:
        if e.button() != Qt.LeftButton:
            return
        hit = self._hit_at(e.position())
        if hit:
            self._activate(hit, e.globalPosition().toPoint())
            return
        row = self._row_at(e.position())
        if row is not None:
            self.selected = "" if self.selected == row["name"] else row["name"]
            self.update()
            return
        if self._in_grip(e.position()):
            self._resizing = True
        else:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
        e.accept()

    def _activate(self, hit: str, global_pos) -> None:
        kind, _, value = hit.partition(":")
        if kind == "metric":
            self.set_metric(value)
            return
        actions = {
            "start": self.action_start,
            "stop": self.action_stop,
            "clear": self.action_clear,
            "copy": self.action_copy,
            "settings": lambda: self.on_settings and self.on_settings(),
            "menu": lambda: self._show_menu(global_pos),
            "close": lambda: self.on_quit and self.on_quit(),
        }
        action = actions.get(value)
        if action:
            action()

    def mouseMoveEvent(self, e) -> None:
        hot = self._hit_at(e.position())
        if hot != self._hot:
            self._hot = hot
            self.update()
        if hot or self._row_at(e.position()) is not None:
            self.setCursor(Qt.PointingHandCursor)
        elif self._in_grip(e.position()):
            self.setCursor(Qt.SizeFDiagCursor)
        else:
            self.setCursor(Qt.ArrowCursor)

        if self._resizing and e.buttons() & Qt.LeftButton:
            g = self.geometry()
            self.resize(max(self.minimumWidth(), int(e.globalPosition().x()) - g.x()),
                        max(self.minimumHeight(), int(e.globalPosition().y()) - g.y()))
        elif self._drag is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag)

    def leaveEvent(self, _e) -> None:
        if self._hot:
            self._hot = ""
            self.update()

    def mouseReleaseEvent(self, _e) -> None:
        self._drag = None
        self._resizing = False
        self._store_geometry()

    def _store_geometry(self) -> None:
        g = self.geometry()
        self.cfg["window"] = {"x": g.x(), "y": g.y(), "w": g.width(), "h": g.height()}

    def contextMenuEvent(self, e) -> None:
        self._show_menu(e.globalPos())

    def _show_menu(self, at) -> None:
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu{background:#141a21;color:#dfe6e8;border:1px solid #2a343c;padding:4px}"
            "QMenu::item{padding:6px 22px 6px 12px}"
            "QMenu::item:selected{background:#233040}"
            "QMenu::separator{height:1px;background:#2a343c;margin:4px 6px}"
        )
        for key, label in (("show_loot", "Показывать добычу внизу"),
                           ("click_through", "Клик проходит насквозь"),
                           ("transparent", "Прозрачный фон"),
                           ("always_on_top", "Поверх всех окон")):
            act = QAction(label, self, checkable=True, checked=bool(self.cfg.get(key)))
            act.triggered.connect(lambda _c, k=key: self._toggle_cfg(k))
            menu.addAction(act)
        menu.addSeparator()
        menu.addAction("Скрыть окно", self.action_toggle_hide)
        menu.addAction("Настройки…", lambda: self.on_settings and self.on_settings())
        menu.addAction("Выход", lambda: self.on_quit and self.on_quit())
        menu.exec(at)

    def _toggle_cfg(self, key: str) -> None:
        self.cfg[key] = not self.cfg.get(key)
        if key == "transparent":
            self.apply_appearance()
        elif key == "click_through":
            self.apply_window_flags()
            self.update()
        else:
            self.update()

    def ensure_on_screen(self) -> None:
        """Возвращает окно на экран, если оно осталось за границей.

        Проверяем ВСЕ мониторы: окно на втором экране имеет отрицательный x
        и по одному лишь главному экрану выглядело бы потерянным.
        """
        geo = self.geometry()
        for screen in QGuiApplication.screens():
            if screen.availableGeometry().intersects(geo):
                return
        area = QGuiApplication.primaryScreen().availableGeometry()
        self.move(area.x() + 60, area.y() + 60)


def _plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(n) % 100
    if 11 <= n <= 14:
        return many
    n %= 10
    if n == 1:
        return one
    if 2 <= n <= 4:
        return few
    return many
