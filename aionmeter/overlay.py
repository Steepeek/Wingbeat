"""Окно метра.

По умолчанию это обычное непрозрачное приложение: сплошной фон, шапка с
кнопками, перетаскивается за шапку. Прозрачность и режим «клик насквозь»
включаются отдельно — они нужны, только когда окно висит поверх игры.

Что нужно от Windows, когда окно всё-таки работает оверлеем:

* WS_EX_NOACTIVATE — окно не забирает фокус. Без этого клик по нему
  сворачивает игру в оконном полноэкранном режиме.
* WS_EX_TRANSPARENT — мышь проходит сквозь окно.
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

BG = QColor(19, 24, 30)
BG_TRANSPARENT = QColor(16, 20, 26, 225)
BG_TITLE = QColor(28, 35, 44)
BG_STATS = QColor(23, 29, 36)
LINE = QColor(255, 255, 255, 28)
LINE_STRONG = QColor(255, 255, 255, 46)
TEXT = QColor(226, 232, 236)
TEXT_DIM = QColor(139, 152, 163)
TEXT_FAINT = QColor(104, 116, 127)
ACCENT = QColor(237, 165, 73)
TEAL = QColor(70, 195, 180)
BTN_HOVER = QColor(255, 255, 255, 22)
BAR_SELF = QColor(237, 165, 73, 66)
BAR_PARTY = QColor(70, 195, 180, 44)
BAR_OTHER = QColor(150, 160, 175, 32)

METRIC_TITLE = {"damage": "Урон", "heal": "Хил", "taken": "Полученный урон"}
SECTION_TITLE = {"party": "ГРУППА", "other": "ОСТАЛЬНЫЕ"}


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
    TITLE_H = 26
    STATS_H = 22
    SECTION_H = 17

    def __init__(self, engine, cfg: dict, on_settings=None, on_quit=None):
        super().__init__(None)
        self.engine = engine
        self.cfg = cfg
        self.on_settings = on_settings
        self.on_quit = on_quit
        self.snapshot: dict = {"rows": [], "sections": [], "total": 0, "duration": 0,
                               "metric": cfg.get("metric", "damage"),
                               "mode": cfg.get("mode", "session"), "stats": {}}
        self._drag: QPoint | None = None
        self._resizing = False
        self._hover_btn = ""
        self._buttons: list[tuple[str, QRect]] = []
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
        self.setMinimumSize(260, 120)
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
        """Применяет смену прозрачности на лету.

        WA_TranslucentBackground меняет тип нативного окна, поэтому его надо
        пересоздать. Заодно меняется HWND — значит стили и хоткеи нужно
        навесить заново, иначе они останутся на мёртвом окне.
        """
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
        self.font_title = QFont("Segoe UI", size, QFont.DemiBold)
        self.font_small = QFont("Segoe UI", max(7, size - 3))
        self.font_num = QFont("Consolas", size - 1)
        self.row_h = QFontMetrics(self.font_body).height() + 8

    @property
    def head_h(self) -> int:
        return self.TITLE_H + self.STATS_H

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

    def action_toggle_mode(self) -> None:
        self.cfg["mode"] = "encounter" if self.cfg.get("mode") == "session" else "session"
        self._refresh()

    def _set_party(self, name: str, is_party: bool) -> None:
        self.engine.meter.set_party(name, is_party)
        self._refresh()

    def set_metric(self, metric: str) -> None:
        self.cfg["metric"] = metric
        self._refresh()

    def set_scope(self, scope: str) -> None:
        self.cfg["scope"] = scope
        self._refresh()

    def action_copy(self) -> None:
        """Строка для вставки в игровой чат (лимит около 255 символов)."""
        snap = self.snapshot
        rows = [r for r in snap.get("rows", []) if r["section"] == "party"] \
            or snap.get("rows", [])
        if not rows:
            return
        head = METRIC_TITLE.get(snap.get("metric", "damage"), "Урон")
        dur = snap.get("duration", 0)
        parts = [f"{head} {dur // 60}:{dur % 60:02d}:"]
        for i, r in enumerate(rows[:8], 1):
            parts.append(f"{i}.{r['name']} {fmt(r['total'])} ({fmt(r['avg'])}dps {r['pct']:.0f}%)")
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
        transparent = bool(self.cfg.get("transparent"))

        if transparent:
            path = QPainterPath()
            path.addRoundedRect(0, 0, w, h, 6, 6)
            p.fillPath(path, BG_TRANSPARENT)
        else:
            p.fillRect(0, 0, w, h, BG)
            p.setPen(QPen(LINE_STRONG, 1))
            p.drawRect(0, 0, w - 1, h - 1)

        snap = self.snapshot
        self._paint_title(p, w, snap)
        self._paint_stats(p, w, snap)

        self._row_rects = []
        y = self.head_h
        rows = snap.get("rows", [])
        if not rows:
            p.setFont(self.font_small)
            p.setPen(TEXT_DIM)
            msg = snap.get("error") or "ждём боевых событий…"
            p.drawText(QRect(12, y + 10, w - 24, 46), Qt.AlignHCenter | Qt.TextWordWrap, msg)
            self._paint_grip(p, w, h)
            return

        split = snap.get("split") and len({r["section"] for r in rows}) > 1
        current = None
        for i, r in enumerate(rows):
            if split and r["section"] != current:
                if y + self.SECTION_H > h - 4:
                    break
                current = r["section"]
                self._paint_section(p, current, y, w, snap)
                y += self.SECTION_H
            if y + self.row_h > h - 4:
                break
            self._paint_row(p, i, r, y, w)
            self._row_rects.append((QRect(0, y, w, self.row_h), r))
            y += self.row_h
        self._paint_grip(p, w, h)

    def _paint_title(self, p: QPainter, w: int, snap: dict) -> None:
        p.fillRect(QRect(0, 0, w, self.TITLE_H), BG_TITLE)
        p.setFont(self.font_title)
        p.setPen(TEXT)
        p.drawText(10, self.TITLE_H - 8, METRIC_TITLE.get(snap.get("metric", "damage"), "Урон"))
        self._paint_buttons(p, w)
        p.setPen(QPen(LINE, 1))
        p.drawLine(0, self.TITLE_H, w, self.TITLE_H)

    def _paint_buttons(self, p: QPainter, w: int) -> None:
        """Кнопки рисуются примитивами, а не глифами шрифта: символы вроде
        ⚙ и ↺ есть не во всех шрифтах и молча превращаются в квадратики."""
        self._buttons = []
        size = self.TITLE_H - 8
        x = w - 6 - size
        for name in ("close", "menu", "reset"):
            rect = QRect(x, 4, size, size)
            self._buttons.append((name, rect))
            if self._hover_btn == name:
                p.fillRect(rect, BTN_HOVER)
            p.setPen(QPen(TEXT_DIM if self._hover_btn != name else TEXT, 1.4))
            cx, cy = rect.center().x() + 1, rect.center().y() + 1
            if name == "close":
                p.drawLine(cx - 4, cy - 4, cx + 4, cy + 4)
                p.drawLine(cx + 4, cy - 4, cx - 4, cy + 4)
            elif name == "menu":
                for dy in (-4, 0, 4):
                    p.drawLine(cx - 5, cy + dy, cx + 5, cy + dy)
            else:                                  # reset — круговая стрелка
                p.drawArc(QRect(cx - 5, cy - 5, 10, 10), 45 * 16, 280 * 16)
                p.drawLine(cx + 4, cy - 5, cx + 4, cy - 1)
                p.drawLine(cx + 4, cy - 5, cx + 7, cy - 4)
            x -= size + 2

    def _paint_stats(self, p: QPainter, w: int, snap: dict) -> None:
        top = self.TITLE_H
        if not self.cfg.get("transparent"):
            p.fillRect(QRect(0, top, w, self.STATS_H), BG_STATS)
        p.setFont(self.font_small)

        mode_session = snap.get("mode", "session") == "session"
        label = "сессия" if mode_session else "бой"
        p.setPen(TEAL if mode_session else ACCENT)
        p.drawText(10, top + self.STATS_H - 7, label)
        chip_w = QFontMetrics(self.font_small).horizontalAdvance(label) + 8

        dur = snap.get("duration", 0)
        left = (f"{dur // 3600}:{dur // 60 % 60:02d}:{dur % 60:02d}" if dur >= 3600
                else f"{dur // 60}:{dur % 60:02d}")
        if snap.get("kills"):
            left += f" · убито {snap['kills']}"
        if snap.get("target"):
            left += f" · {snap['target']}"
        p.setPen(TEXT_FAINT)
        fm = QFontMetrics(self.font_small)
        avail = w - 10 - chip_w - 90
        p.drawText(10 + chip_w, top + self.STATS_H - 7,
                   fm.elidedText(left, Qt.ElideRight, max(30, avail)))

        p.setFont(self.font_num)
        p.setPen(ACCENT)
        p.drawText(QRect(0, top, w - 10, self.STATS_H),
                   Qt.AlignRight | Qt.AlignVCenter, fmt(snap.get("total", 0)))
        p.setPen(QPen(LINE, 1))
        p.drawLine(0, top + self.STATS_H, w, top + self.STATS_H)

    def _paint_section(self, p: QPainter, key: str, y: int, w: int, snap: dict) -> None:
        p.setFont(self.font_small)
        p.setPen(TEXT_FAINT)
        title = SECTION_TITLE.get(key, key.upper())
        p.drawText(10, y + self.SECTION_H - 5, title)
        total = next((s["total"] for s in snap.get("sections", []) if s["key"] == key), 0)
        p.drawText(QRect(0, y, w - 10, self.SECTION_H),
                   Qt.AlignRight | Qt.AlignVCenter, fmt(total))
        fm = QFontMetrics(self.font_small)
        x0 = 12 + fm.horizontalAdvance(title)
        p.setPen(QPen(LINE, 1))
        p.drawLine(x0, y + self.SECTION_H - 8, w - 60, y + self.SECTION_H - 8)

    def _paint_row(self, p: QPainter, i: int, r: dict, y: int, w: int) -> None:
        bar_w = int((w - 8) * max(0.0, min(1.0, r.get("bar", 0))))
        colour = BAR_SELF if r["is_self"] else (BAR_PARTY if r["section"] == "party" else BAR_OTHER)
        p.fillRect(QRect(4, y + 2, bar_w, self.row_h - 4), colour)

        p.setFont(self.font_body)
        p.setPen(ACCENT if r["is_self"] else TEXT)
        cols_w = sum(cw for _k, cw in self._columns(w))
        fm = QFontMetrics(self.font_body)
        name = fm.elidedText(f"{i + 1}. {r['name']}", Qt.ElideRight, max(60, w - 18 - cols_w))
        p.drawText(9, y + self.row_h - 7, name)

        # Каждая колонка в своей ячейке, а не склейкой в строку: иначе числа
        # разной длины не выстраиваются по вертикали и таблицу не прочитать.
        p.setFont(self.font_num)
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
        p.setPen(QPen(TEXT_FAINT, 1))
        for off in (3, 7):
            p.drawLine(w - off - 4, h - 4, w - 4, h - off - 4)

    # -- мышь --

    def _button_at(self, pos) -> str:
        for name, rect in self._buttons:
            if rect.contains(pos.toPoint()):
                return name
        return ""

    def _in_grip(self, pos) -> bool:
        return pos.x() > self.width() - 16 and pos.y() > self.height() - 16

    def mousePressEvent(self, e) -> None:
        if e.button() != Qt.LeftButton:
            return
        btn = self._button_at(e.position())
        if btn == "close":
            if self.on_quit:
                self.on_quit()
            return
        if btn == "reset":
            self.action_reset()
            return
        if btn == "menu":
            self._show_menu(e.globalPosition().toPoint())
            return
        if self._in_grip(e.position()):
            self._resizing = True
        else:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
        e.accept()

    def mouseMoveEvent(self, e) -> None:
        hover = self._button_at(e.position())
        if hover != self._hover_btn:
            self._hover_btn = hover
            self.update()
        if hover:
            self.setCursor(Qt.PointingHandCursor)
        elif self._in_grip(e.position()):
            self.setCursor(Qt.SizeFDiagCursor)
        else:
            self.setCursor(Qt.ArrowCursor)

        if self._resizing and e.buttons() & Qt.LeftButton:
            g = self.geometry()
            self.resize(max(260, int(e.globalPosition().x()) - g.x()),
                        max(120, int(e.globalPosition().y()) - g.y()))
        elif self._drag is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag)

    def leaveEvent(self, _e) -> None:
        if self._hover_btn:
            self._hover_btn = ""
            self.update()

    def mouseReleaseEvent(self, _e) -> None:
        self._drag = None
        self._resizing = False
        self._store_geometry()

    def _store_geometry(self) -> None:
        g = self.geometry()
        self.cfg["window"] = {"x": g.x(), "y": g.y(), "w": g.width(), "h": g.height()}

    def _row_at(self, pos) -> dict | None:
        for rect, row in self._row_rects:
            if rect.contains(pos):
                return row
        return None

    def contextMenuEvent(self, e) -> None:
        self._show_menu(e.globalPos(), self._row_at(e.pos()))

    def _show_menu(self, at, row: dict | None = None) -> None:
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu{background:#141a21;color:#dfe6e8;border:1px solid #2a343c;padding:4px}"
            "QMenu::item{padding:5px 22px 5px 12px}"
            "QMenu::item:selected{background:#233040}"
            "QMenu::separator{height:1px;background:#2a343c;margin:4px 6px}"
        )
        # Ростер строится по событиям входа в группу, по строкам получения
        # урона и по групповому чату. Дальнобойного согруппника, который не
        # получает урона и молчит, так не поймать — поэтому даём правку руками.
        if row is not None and row["name"] != "(периодический)":
            name = row["name"]
            if row["section"] == "party":
                menu.addAction(f"{name}: убрать из группы",
                               lambda _c=False, n=name: self._set_party(n, False))
            else:
                menu.addAction(f"{name}: считать согруппником",
                               lambda _c=False, n=name: self._set_party(n, True))
            menu.addSeparator()

        cur = self.cfg.get("metric", "damage")
        for key, label in (("damage", "Урон"), ("heal", "Хил"), ("taken", "Полученный урон")):
            act = QAction(label, self, checkable=True, checked=cur == key)
            act.triggered.connect(lambda _c, k=key: self.set_metric(k))
            menu.addAction(act)
        menu.addSeparator()

        mode = self.cfg.get("mode", "session")
        for key, label in (("session", "Копить до очистки"), ("encounter", "Только текущий бой")):
            act = QAction(label, self, checkable=True, checked=mode == key)
            act.triggered.connect(lambda _c, k=key: (self.cfg.__setitem__("mode", k),
                                                     self._refresh()))
            menu.addAction(act)
        menu.addSeparator()

        scope = self.cfg.get("scope", "split")
        for key, label in (("split", "Группа и остальные"), ("party", "Только группа"),
                           ("all", "Все одним списком")):
            act = QAction(label, self, checkable=True, checked=scope == key)
            act.triggered.connect(lambda _c, k=key: self.set_scope(k))
            menu.addAction(act)
        menu.addSeparator()

        menu.addAction("Очистить", self.action_reset)
        menu.addAction("Скопировать в буфер", self.action_copy)
        act = QAction("Клик насквозь", self, checkable=True,
                      checked=bool(self.cfg.get("click_through")))
        act.triggered.connect(self.action_toggle_click)
        menu.addAction(act)
        act = QAction("Прозрачный фон", self, checkable=True,
                      checked=bool(self.cfg.get("transparent")))
        act.triggered.connect(lambda: (self.cfg.__setitem__(
            "transparent", not self.cfg.get("transparent")), self.apply_appearance()))
        menu.addAction(act)
        menu.addSeparator()
        menu.addAction("Настройки…", lambda: self.on_settings and self.on_settings())
        menu.addAction("Выход", lambda: self.on_quit and self.on_quit())
        menu.exec(at)

    def ensure_on_screen(self) -> None:
        area = QGuiApplication.primaryScreen().availableGeometry()
        if not area.intersects(self.geometry()):
            self.move(area.x() + 60, area.y() + 60)
