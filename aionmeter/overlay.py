"""Окно метра.

По умолчанию это обычное непрозрачное приложение: сплошной фон, панель с
кнопками, перетаскивается за любое свободное место. Прозрачность и режим
«клик насквозь» включаются отдельно — они нужны, только когда окно висит
поверх игры.

Управление вынесено на панель, а не спрятано в меню: переключатели метрики,
режима счёта и состава — на виду и в один клик. Меню осталось для редкого.

Что нужно от Windows, когда окно работает оверлеем:

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
BG_BAR = QColor(28, 35, 44)
BG_STATS = QColor(23, 29, 36)
BG_TAB = QColor(44, 56, 68)
LINE = QColor(255, 255, 255, 28)
LINE_STRONG = QColor(255, 255, 255, 46)
TEXT = QColor(226, 232, 236)
TEXT_DIM = QColor(139, 152, 163)
TEXT_FAINT = QColor(104, 116, 127)
ACCENT = QColor(237, 165, 73)
TEAL = QColor(70, 195, 180)
BTN_HOVER = QColor(255, 255, 255, 24)
BAR_SELF = QColor(237, 165, 73, 66)
BAR_PARTY = QColor(70, 195, 180, 44)
BAR_OTHER = QColor(150, 160, 175, 32)
BAR_SKILL = QColor(120, 140, 165, 40)

METRIC_TITLE = {"damage": "Урон", "heal": "Хил", "taken": "Полученный урон"}
METRIC_TABS = (("damage", "Урон"), ("heal", "Хил"), ("taken", "Получ."))
SECTION_TITLE = {"party": "ГРУППА", "other": "ОСТАЛЬНЫЕ"}
MODE_LABEL = {"session": "сессия", "encounter": "бой"}
SCOPE_LABEL = {"split": "группа+все", "party": "группа", "all": "все"}
SCOPE_CYCLE = ("split", "party", "all")

BUTTONS = (
    ("reset", "Очистить"),
    ("copy", "Скопировать в буфер"),
    ("settings", "Настройки"),
    ("through", "Клик насквозь"),
    ("menu", "Ещё"),
    ("close", "Выход"),
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
    BAR_H = 28
    STATS_H = 22
    SECTION_H = 17
    FOOTER_H = 20

    def __init__(self, engine, cfg: dict, on_settings=None, on_quit=None):
        super().__init__(None)
        self.engine = engine
        self.cfg = cfg
        self.on_settings = on_settings
        self.on_quit = on_quit
        self.snapshot: dict = {"rows": [], "sections": [], "loot": {}, "total": 0,
                               "duration": 0, "metric": cfg.get("metric", "damage"),
                               "mode": cfg.get("mode", "session"), "stats": {}}
        self.selected = ""             # чей разбор по скиллам раскрыт
        self._drag: QPoint | None = None
        self._resizing = False
        self._hot = ""                 # что под курсором
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
        self.setMinimumSize(330, 140)
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
        пересоздать. Заодно может смениться HWND — значит стили и хоткеи
        нужно навесить заново, иначе они останутся на мёртвом окне.
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
        self.font_tab = QFont("Segoe UI", max(8, size - 1), QFont.DemiBold)
        self.font_small = QFont("Segoe UI", max(7, size - 3))
        self.font_num = QFont("Consolas", size - 1)
        self.font_skill = QFont("Segoe UI", max(7, size - 3))
        self.row_h = QFontMetrics(self.font_body).height() + 8
        self.skill_h = QFontMetrics(self.font_skill).height() + 3

    @property
    def head_h(self) -> int:
        return self.BAR_H + self.STATS_H

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
        self.selected = ""
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

    def action_cycle_scope(self) -> None:
        cur = self.cfg.get("scope", "split")
        idx = SCOPE_CYCLE.index(cur) if cur in SCOPE_CYCLE else 0
        self.cfg["scope"] = SCOPE_CYCLE[(idx + 1) % len(SCOPE_CYCLE)]
        self._refresh()

    def set_metric(self, metric: str) -> None:
        self.cfg["metric"] = metric
        self.selected = ""
        self._refresh()

    def set_scope(self, scope: str) -> None:
        self.cfg["scope"] = scope
        self._refresh()

    def _set_party(self, name: str, is_party: bool) -> None:
        self.engine.meter.set_party(name, is_party)
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
        self._hit = []
        self._paint_bar(p, w, snap)
        self._paint_stats(p, w, snap)

        bottom = h - 4 - self.footer_h
        self._row_rects = []
        y = self.head_h
        rows = snap.get("rows", [])
        if not rows:
            p.setFont(self.font_small)
            p.setPen(TEXT_DIM)
            msg = snap.get("error") or "ждём боевых событий…"
            p.drawText(QRect(12, y + 10, w - 24, 46), Qt.AlignHCenter | Qt.TextWordWrap, msg)
        else:
            split = snap.get("split") and len({r["section"] for r in rows}) > 1
            current = None
            for i, r in enumerate(rows):
                if split and r["section"] != current:
                    if y + self.SECTION_H > bottom:
                        break
                    current = r["section"]
                    self._paint_section(p, current, y, w, snap)
                    y += self.SECTION_H
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

    # -- панель --

    def _paint_bar(self, p: QPainter, w: int, snap: dict) -> None:
        p.fillRect(QRect(0, 0, w, self.BAR_H), BG_BAR)

        # вкладки метрик слева
        p.setFont(self.font_tab)
        fm = QFontMetrics(self.font_tab)
        cur = self.cfg.get("metric", "damage")
        x = 5
        for key, label in METRIC_TABS:
            tw = fm.horizontalAdvance(label) + 16
            rect = QRect(x, 3, tw, self.BAR_H - 6)
            self._hit.append((f"metric:{key}", rect))
            if key == cur:
                p.fillRect(rect, BG_TAB)
                p.setPen(ACCENT)
            elif self._hot == f"metric:{key}":
                p.fillRect(rect, BTN_HOVER)
                p.setPen(TEXT)
            else:
                p.setPen(TEXT_DIM)
            p.drawText(rect, Qt.AlignCenter, label)
            x += tw + 2

        # кнопки справа
        size = self.BAR_H - 8
        bx = w - 5 - size
        for name, _tip in reversed(BUTTONS):
            rect = QRect(bx, 4, size, size)
            self._hit.append((f"btn:{name}", rect))
            active = name == "through" and bool(self.cfg.get("click_through"))
            if active:
                p.fillRect(rect, BG_TAB)
            elif self._hot == f"btn:{name}":
                p.fillRect(rect, BTN_HOVER)
            colour = ACCENT if active else (TEXT if self._hot == f"btn:{name}" else TEXT_DIM)
            self._draw_icon(p, name, rect, colour)
            bx -= size + 2

        p.setPen(QPen(LINE, 1))
        p.drawLine(0, self.BAR_H, w, self.BAR_H)

    def _draw_icon(self, p: QPainter, name: str, rect: QRect, colour: QColor) -> None:
        """Иконки рисуются примитивами, а не глифами шрифта: символы вроде
        ⚙ и ↺ есть не во всех шрифтах и молча превращаются в квадратики."""
        p.setPen(QPen(colour, 1.4))
        cx, cy = rect.center().x() + 1, rect.center().y() + 1
        if name == "close":
            p.drawLine(cx - 4, cy - 4, cx + 4, cy + 4)
            p.drawLine(cx + 4, cy - 4, cx - 4, cy + 4)
        elif name == "menu":
            for dy in (-4, 0, 4):
                p.drawLine(cx - 5, cy + dy, cx + 5, cy + dy)
        elif name == "reset":
            p.drawArc(QRect(cx - 5, cy - 5, 10, 10), 45 * 16, 280 * 16)
            p.drawLine(cx + 4, cy - 5, cx + 4, cy - 1)
            p.drawLine(cx + 4, cy - 5, cx + 7, cy - 4)
        elif name == "copy":
            p.drawRect(QRect(cx - 5, cy - 5, 7, 7))
            p.drawRect(QRect(cx - 2, cy - 2, 7, 7))
        elif name == "settings":
            p.drawEllipse(QPoint(cx, cy), 3, 3)
            for dx, dy in ((0, -6), (0, 6), (-6, 0), (6, 0), (-4, -4), (4, 4)):
                p.drawLine(cx + dx // 2, cy + dy // 2, cx + dx, cy + dy)
        elif name == "through":
            p.drawRect(QRect(cx - 5, cy - 5, 10, 10))
            p.drawLine(cx - 2, cy, cx + 2, cy)

    def _paint_stats(self, p: QPainter, w: int, snap: dict) -> None:
        top = self.BAR_H
        if not self.cfg.get("transparent"):
            p.fillRect(QRect(0, top, w, self.STATS_H), BG_STATS)
        p.setFont(self.font_small)
        fm = QFontMetrics(self.font_small)

        x = 6
        for key, label, colour in (
            ("mode", MODE_LABEL.get(snap.get("mode", "session"), "сессия"),
             TEAL if snap.get("mode") == "session" else ACCENT),
            ("scope", SCOPE_LABEL.get(self.cfg.get("scope", "split"), "группа+все"), TEXT_DIM),
        ):
            cw = fm.horizontalAdvance(label) + 12
            rect = QRect(x, top + 3, cw, self.STATS_H - 6)
            self._hit.append((f"chip:{key}", rect))
            p.fillRect(rect, BTN_HOVER if self._hot == f"chip:{key}" else BG_TAB)
            p.setPen(colour)
            p.drawText(rect, Qt.AlignCenter, label)
            x += cw + 4

        dur = snap.get("duration", 0)
        left = (f"{dur // 3600}:{dur // 60 % 60:02d}:{dur % 60:02d}" if dur >= 3600
                else f"{dur // 60}:{dur % 60:02d}")
        if snap.get("target"):
            left += f" · {snap['target']}"
        p.setPen(TEXT_FAINT)
        total_w = QFontMetrics(self.font_num).horizontalAdvance(fmt(snap.get("total", 0))) + 16
        p.drawText(x + 2, top + self.STATS_H - 7,
                   fm.elidedText(left, Qt.ElideRight, max(20, w - x - total_w)))

        p.setFont(self.font_num)
        p.setPen(ACCENT)
        p.drawText(QRect(0, top, w - 8, self.STATS_H),
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
        if self.selected == r["name"]:
            p.setPen(QPen(LINE_STRONG, 1))
            p.drawRect(QRect(4, y + 2, w - 9, self.row_h - 4))

        p.setFont(self.font_body)
        p.setPen(ACCENT if r["is_self"] else TEXT)
        cols_w = sum(cw for _k, cw in self._columns(w))
        fm = QFontMetrics(self.font_body)
        mark = "▾ " if self.selected == r["name"] else ""
        name = fm.elidedText(f"{mark}{i + 1}. {r['name']}", Qt.ElideRight,
                             max(60, w - 18 - cols_w))
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

    def _paint_skills(self, p: QPainter, r: dict, y: int, w: int, bottom: int) -> int:
        """Разбор по скиллам для раскрытой строки.

        Данные уже собираются агрегатором, показать их почти ничего не стоит.
        У автоатак имени скилла в логе нет — они идут отдельной строкой.
        """
        skills = r.get("skills") or []
        named = sum(v for _k, v in skills)
        auto = max(0, r["total"] - named)
        items = list(skills[:6])
        if auto > 0:
            items.append(("автоатака", auto))
        if not items:
            items = [("нет разбивки по скиллам", 0)]
        top = max((v for _k, v in items), default=1) or 1

        p.setFont(self.font_skill)
        for label, value in items:
            if y + self.skill_h > bottom:
                break
            bw = int((w - 40) * value / top)
            p.fillRect(QRect(24, y + 1, bw, self.skill_h - 2), BAR_SKILL)
            p.setPen(TEXT_DIM)
            fm = QFontMetrics(self.font_skill)
            p.drawText(28, y + self.skill_h - 4,
                       fm.elidedText(label, Qt.ElideRight, int(w * 0.55)))
            if value:
                pct = 100.0 * value / (r["total"] or 1)
                p.setPen(TEXT_FAINT)
                p.drawText(QRect(0, y, w - 10, self.skill_h),
                           Qt.AlignRight | Qt.AlignVCenter, f"{fmt(value)}   {pct:.0f}%")
            y += self.skill_h
        return y

    def _paint_footer(self, p: QPainter, w: int, h: int, snap: dict) -> None:
        top = h - self.FOOTER_H
        if not self.cfg.get("transparent"):
            p.fillRect(QRect(0, top, w, self.FOOTER_H), BG_STATS)
        p.setPen(QPen(LINE, 1))
        p.drawLine(0, top, w, top)

        loot = snap.get("loot", {})
        parts = []
        if loot.get("exp"):
            parts.append(f"опыт {fmt(loot['exp'])}")
        if loot.get("ap"):
            parts.append(f"AP {fmt(loot['ap'])}")
        if loot.get("kinah_in"):
            parts.append(f"кинах {fmt(loot['kinah_in'])}")
        if loot.get("kills"):
            parts.append(f"убито {loot['kills']}")
        if loot.get("pvp_kills"):
            parts.append(f"PvP {loot['pvp_kills']}")
        if loot.get("deaths") or loot.get("pvp_deaths"):
            parts.append(f"смертей {loot.get('deaths', 0) + loot.get('pvp_deaths', 0)}")
        text = " · ".join(parts) or "добычи пока нет"

        p.setFont(self.font_small)
        p.setPen(TEXT_FAINT)
        fm = QFontMetrics(self.font_small)
        p.drawText(9, top + self.FOOTER_H - 6, fm.elidedText(text, Qt.ElideRight, w - 60))

        stats = snap.get("stats", {})
        if stats.get("read"):
            health = f"{stats.get('parsed', 0)}/{stats['read']}"
            p.drawText(QRect(0, top, w - 9, self.FOOTER_H),
                       Qt.AlignRight | Qt.AlignVCenter, health)

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
            # Клик по строке раскрывает разбор по скиллам
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
        elif kind == "chip" and value == "mode":
            self.action_toggle_mode()
        elif kind == "chip" and value == "scope":
            self.action_cycle_scope()
        elif kind == "btn":
            if value == "reset":
                self.action_reset()
            elif value == "copy":
                self.action_copy()
            elif value == "settings" and self.on_settings:
                self.on_settings()
            elif value == "through":
                self.action_toggle_click()
            elif value == "menu":
                self._show_menu(global_pos)
            elif value == "close" and self.on_quit:
                self.on_quit()

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
        self._show_menu(e.globalPos(), self._row_at(e.pos()))

    def _show_menu(self, at, row: dict | None = None) -> None:
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu{background:#141a21;color:#dfe6e8;border:1px solid #2a343c;padding:4px}"
            "QMenu::item{padding:5px 22px 5px 12px}"
            "QMenu::item:selected{background:#233040}"
            "QMenu::separator{height:1px;background:#2a343c;margin:4px 6px}"
        )
        # Ростер строится по событиям входа, по строкам получения урона и по
        # групповому чату. Дальнобойного согруппника, который не получает
        # урона и молчит, так не поймать — поэтому даём правку руками.
        if row is not None and row["name"] != "(периодический)":
            name = row["name"]
            if row["section"] == "party":
                menu.addAction(f"{name}: убрать из группы",
                               lambda _c=False, n=name: self._set_party(n, False))
            else:
                menu.addAction(f"{name}: считать согруппником",
                               lambda _c=False, n=name: self._set_party(n, True))
            menu.addSeparator()

        scope = self.cfg.get("scope", "split")
        for key, label in (("split", "Группа и остальные"), ("party", "Только группа"),
                           ("all", "Все одним списком")):
            act = QAction(label, self, checkable=True, checked=scope == key)
            act.triggered.connect(lambda _c, k=key: self.set_scope(k))
            menu.addAction(act)
        menu.addSeparator()

        for key, label in (("show_loot", "Строка добычи"),
                           ("transparent", "Прозрачный фон"),
                           ("always_on_top", "Поверх всех окон")):
            act = QAction(label, self, checkable=True, checked=bool(self.cfg.get(key)))
            act.triggered.connect(lambda _c, k=key: self._toggle_cfg(k))
            menu.addAction(act)
        menu.addSeparator()
        menu.addAction("Настройки…", lambda: self.on_settings and self.on_settings())
        menu.addAction("Выход", lambda: self.on_quit and self.on_quit())
        menu.exec(at)

    def _toggle_cfg(self, key: str) -> None:
        self.cfg[key] = not self.cfg.get(key)
        if key == "transparent":
            self.apply_appearance()
        else:
            self.update()

    def ensure_on_screen(self) -> None:
        area = QGuiApplication.primaryScreen().availableGeometry()
        if not area.intersects(self.geometry()):
            self.move(area.x() + 60, area.y() + 60)
