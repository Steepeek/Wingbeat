"""Оверлей поверх игры.

Три вещи, без которых окно не годится как оверлей:

* WS_EX_NOACTIVATE — окно никогда не забирает фокус. Без этого клик по нему
  сворачивает игру в оконном полноэкранном режиме.
* WS_EX_TRANSPARENT — режим «клик насквозь»: мышь проходит сквозь плашку.
* Периодический SetWindowPos(HWND_TOPMOST) — игра сбрасывает чужой z-order
  при переключении фокуса, поэтому «поверх всех» надо подтверждать.

Оверлей не виден, если игра в ЭКСКЛЮЗИВНОМ полноэкранном режиме: DWM тогда
отключён и чужие окна не подмешиваются. Нужен оконный полноэкранный.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from PySide6.QtCore import QPoint, QRect, Qt, QTimer
from PySide6.QtGui import (QAction, QColor, QFont, QFontMetrics, QGuiApplication,
                           QIcon, QPainter, QPainterPath, QPen, QPixmap)
from PySide6.QtWidgets import QApplication, QMenu, QWidget

from . import hotkeys as hk

IS_WINDOWS = hasattr(ctypes, "windll")

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
HWND_TOPMOST = -1
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010

# --- палитра --------------------------------------------------------------

BG = QColor(16, 20, 26, 235)
BG_HEADER = QColor(24, 30, 38, 255)
LINE = QColor(255, 255, 255, 26)
TEXT = QColor(226, 232, 236)
TEXT_DIM = QColor(140, 152, 162)
ACCENT = QColor(237, 165, 73)          # свой урон
BAR_SELF = QColor(237, 165, 73, 70)
BAR_PARTY = QColor(70, 195, 180, 46)
BAR_OTHER = QColor(150, 160, 175, 34)
WARN = QColor(236, 113, 120)

METRIC_TITLE = {"damage": "УРОН", "heal": "ХИЛ", "taken": "ПОЛУЧЕНО"}


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
    def __init__(self, engine, cfg: dict, on_settings=None, on_quit=None):
        super().__init__(None)
        self.engine = engine
        self.cfg = cfg
        self.on_settings = on_settings
        self.on_quit = on_quit
        self.snapshot: dict = {"rows": [], "total": 0, "duration": 0,
                               "metric": cfg.get("metric", "damage"), "stats": {}}
        self._drag: QPoint | None = None
        self._resizing = False
        self.hotkeys: hk.HotkeyManager | None = None

        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
            | Qt.Tool | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_TranslucentBackground)     # per-pixel alpha
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setWindowTitle("AionMeter")
        self.setMouseTracking(True)

        w = cfg["window"]
        self.setGeometry(w["x"], w["y"], w["w"], w["h"])
        self.setMinimumSize(220, 90)
        self.setWindowOpacity(cfg.get("opacity", 0.88))
        self._apply_font()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh)
        self.timer.start(250)

        self.topmost_timer = QTimer(self)
        self.topmost_timer.timeout.connect(self._keep_on_top)
        self.topmost_timer.start(2000)

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
        style = (style | add) & ~remove
        setl(wintypes.HWND(self.hwnd), GWL_EXSTYLE, style)

    def apply_window_flags(self) -> None:
        """Ставит NOACTIVATE/TOOLWINDOW и режим клик-сквозь."""
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
        self.hotkeys.register(binds.get("reset", ""), self.action_reset)
        self.hotkeys.register(binds.get("click_through", ""), self.action_toggle_click)
        self.hotkeys.register(binds.get("hide", ""), self.action_toggle_hide)
        self.hotkeys.register(binds.get("copy", ""), self.action_copy)

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

    def action_reset(self) -> None:
        self.engine.reset()
        self._refresh()

    def action_toggle_click(self) -> None:
        self.cfg["click_through"] = not self.cfg.get("click_through")
        self.apply_window_flags()
        self.update()

    def action_toggle_hide(self) -> None:
        self.setVisible(not self.isVisible())

    def action_copy(self) -> None:
        """Строка для вставки в игровой чат.

        Игровой чат ограничен ~255 символами, поэтому режем на части.
        """
        snap = self.snapshot
        rows = snap.get("rows", [])
        if not rows:
            return
        title = METRIC_TITLE.get(snap.get("metric", "damage"), "УРОН")
        parts = [f"{title} {snap.get('duration', 0)}s:"]
        for i, r in enumerate(rows[:8], 1):
            parts.append(f"{i}.{r['name']} {fmt(r['total'])} ({fmt(r['avg'])}dps {r['pct']:.0f}%)")
        text, line = [], ""
        for token in parts:
            if len(line) + len(token) + 1 > 250:
                text.append(line)
                line = token
            else:
                line = f"{line} {token}".strip()
        if line:
            text.append(line)
        QApplication.clipboard().setText("\n".join(text))

    def set_metric(self, metric: str) -> None:
        self.cfg["metric"] = metric
        self._refresh()

    def set_scope(self, scope: str) -> None:
        self.cfg["scope"] = scope
        self._refresh()

    # -- отрисовка --

    def _apply_font(self) -> None:
        size = self.cfg.get("font_size", 12)
        self.font_body = QFont("Segoe UI", size)
        self.font_bold = QFont("Segoe UI", size, QFont.DemiBold)
        self.font_small = QFont("Segoe UI", max(7, size - 3))
        self.font_num = QFont("Consolas", size - 1)
        self.row_h = QFontMetrics(self.font_body).height() + 8
        self.head_h = QFontMetrics(self.font_small).height() * 2 + 12

    def _refresh(self) -> None:
        self.snapshot = self.engine.snapshot()
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()

        path = QPainterPath()
        path.addRoundedRect(0, 0, w, h, 6, 6)
        p.fillPath(path, BG)
        p.fillRect(QRect(0, 0, w, self.head_h), BG_HEADER)

        snap = self.snapshot
        self._paint_header(p, w, snap)

        y = self.head_h
        rows = snap.get("rows", [])
        if not rows:
            p.setFont(self.font_small)
            p.setPen(TEXT_DIM)
            msg = snap.get("error") or "ждём боевых событий…"
            p.drawText(QRect(10, y + 8, w - 20, 40), Qt.AlignHCenter | Qt.TextWordWrap, msg)
            self._paint_grip(p, w, h)
            return

        for i, r in enumerate(rows):
            if y + self.row_h > h - 4:
                break
            self._paint_row(p, i, r, y, w)
            y += self.row_h
        self._paint_grip(p, w, h)

    def _paint_header(self, p: QPainter, w: int, snap: dict) -> None:
        p.setFont(self.font_bold)
        p.setPen(TEXT)
        title = METRIC_TITLE.get(snap.get("metric", "damage"), "УРОН")
        p.drawText(9, 15, title)

        p.setFont(self.font_num)
        p.setPen(ACCENT)
        total = fmt(snap.get("total", 0))
        p.drawText(QRect(0, 3, w - 9, 15), Qt.AlignRight | Qt.AlignVCenter, total)

        p.setFont(self.font_small)
        p.setPen(TEXT_DIM)
        dur = snap.get("duration", 0)
        left = (f"{dur // 3600}:{dur // 60 % 60:02d}:{dur % 60:02d}" if dur >= 3600
                else f"{dur // 60}:{dur % 60:02d}")
        if snap.get("target"):
            left += f" · {snap['target'][:22]}"
        if snap.get("kills"):
            left += f" · убито {snap['kills']}"
        p.drawText(9, self.head_h - 6, left)

        scope = "группа" if self.cfg.get("scope") == "party" else "все"
        right = f"окно {snap.get('window', 10)}с · {scope}"
        if self.cfg.get("click_through"):
            right = "⇢ " + right
        p.drawText(QRect(0, self.head_h - 18, w - 9, 14),
                   Qt.AlignRight | Qt.AlignVCenter, right)
        p.setPen(QPen(LINE, 1))
        p.drawLine(0, self.head_h, w, self.head_h)

    def _paint_row(self, p: QPainter, i: int, r: dict, y: int, w: int) -> None:
        bar_w = int((w - 8) * max(0.0, min(1.0, r.get("bar", 0))))
        colour = BAR_SELF if r["is_self"] else (BAR_PARTY if r["is_party"] else BAR_OTHER)
        p.fillRect(QRect(4, y + 2, bar_w, self.row_h - 4), colour)

        p.setFont(self.font_body)
        p.setPen(ACCENT if r["is_self"] else TEXT)
        name = f"{i + 1}. {r['name']}"
        fm = QFontMetrics(self.font_body)
        cols_w = sum(cw for _k, cw in self._columns(w))
        name = fm.elidedText(name, Qt.ElideRight, max(60, w - 18 - cols_w))
        p.drawText(9, y + self.row_h - 7, name)

        # Каждая колонка рисуется в своей ячейке, а не склеивается в строку:
        # иначе числа разной длины не выстраиваются по вертикали и таблицу
        # невозможно читать взглядом сверху вниз.
        p.setFont(self.font_num)
        p.setPen(TEXT)
        x = w - 9
        for key, cell_w in reversed(self._columns(w)):
            x -= cell_w
            value = self._cell_text(key, r)
            if not value:
                continue
            p.setPen(TEXT_DIM if key in ("pct", "hits", "crit") else TEXT)
            p.drawText(QRect(x, y, cell_w - 6, self.row_h),
                       Qt.AlignRight | Qt.AlignVCenter, value)

    def _columns(self, w: int) -> list[tuple[str, int]]:
        """Ключ колонки и её ширина в пикселях, слева направо."""
        share = {"dmg": 0.21, "dps": 0.19, "pct": 0.13, "hits": 0.16, "crit": 0.15}
        active = [c for c in self.cfg.get("columns", ["dmg", "dps", "pct"]) if c in share]
        order = [c for c in ("dmg", "dps", "pct", "hits", "crit") if c in active]
        avail = w - 18
        return [(c, max(34, int(avail * share[c]))) for c in order]

    @staticmethod
    def _cell_text(key: str, r: dict) -> str:
        if key == "dmg":
            return fmt(r["total"])
        if key == "dps":
            return fmt(r["dps"]) if r["dps"] >= 1 else "·"
        if key == "pct":
            return f"{r['pct']:.0f}%"
        if key == "hits":
            return str(r["hits"])
        if key == "crit":
            return f"{r['crit']:.0f}%" if r.get("crit") is not None else "—"
        return ""

    def _paint_grip(self, p: QPainter, w: int, h: int) -> None:
        p.setPen(QPen(TEXT_DIM, 1))
        for off in (3, 7):
            p.drawLine(w - off - 4, h - 4, w - 4, h - off - 4)

    # -- мышь --

    def _in_grip(self, pos) -> bool:
        return pos.x() > self.width() - 16 and pos.y() > self.height() - 16

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.LeftButton:
            if self._in_grip(e.position()):
                self._resizing = True
            else:
                self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            e.accept()

    def mouseMoveEvent(self, e) -> None:
        self.setCursor(Qt.SizeFDiagCursor if self._in_grip(e.position()) else Qt.ArrowCursor)
        if self._resizing and e.buttons() & Qt.LeftButton:
            g = self.geometry()
            self.resize(max(220, e.globalPosition().x() - g.x()),
                        max(90, e.globalPosition().y() - g.y()))
        elif self._drag is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, _e) -> None:
        self._drag = None
        self._resizing = False
        self._store_geometry()

    def _store_geometry(self) -> None:
        g = self.geometry()
        self.cfg["window"] = {"x": g.x(), "y": g.y(), "w": g.width(), "h": g.height()}

    def contextMenuEvent(self, e) -> None:
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu{background:#141a21;color:#dfe6e8;border:1px solid #2a343c;padding:4px}"
            "QMenu::item{padding:5px 22px 5px 12px}"
            "QMenu::item:selected{background:#233040}"
            "QMenu::separator{height:1px;background:#2a343c;margin:4px 6px}"
        )
        cur_metric = self.cfg.get("metric", "damage")
        for key, label in (("damage", "Урон"), ("heal", "Хил"), ("taken", "Полученный урон")):
            act = QAction(label, self, checkable=True, checked=cur_metric == key)
            act.triggered.connect(lambda _c, k=key: self.set_metric(k))
            menu.addAction(act)
        menu.addSeparator()
        cur_scope = self.cfg.get("scope", "party")
        for key, label in (("party", "Только группа"), ("all", "Все видимые")):
            act = QAction(label, self, checkable=True, checked=cur_scope == key)
            act.triggered.connect(lambda _c, k=key: self.set_scope(k))
            menu.addAction(act)
        menu.addSeparator()
        menu.addAction("Сбросить бой", self.action_reset)
        menu.addAction("Скопировать в буфер", self.action_copy)
        act = QAction("Клик насквозь", self, checkable=True,
                      checked=bool(self.cfg.get("click_through")))
        act.triggered.connect(self.action_toggle_click)
        menu.addAction(act)
        menu.addSeparator()
        menu.addAction("Настройки…", lambda: self.on_settings and self.on_settings())
        menu.addAction("Выход", lambda: self.on_quit and self.on_quit())
        menu.exec(e.globalPos())

    def ensure_on_screen(self) -> None:
        """Не даём окну остаться за границей после смены монитора."""
        area = QGuiApplication.primaryScreen().availableGeometry()
        g = self.geometry()
        if not area.intersects(g):
            self.move(area.x() + 60, area.y() + 60)
