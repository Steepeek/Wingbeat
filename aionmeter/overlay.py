"""Окно метра.

    ┌────────────────────────────────────────────────┐
    │  Урон  Хил  Получено           ▶ ⟲ ⧉   ≡ ✕    │ шапка
    │  #  ИГРОК              УРОН   DPS    %   УД.   │ заголовки колонок
    ╞════════════════════════════════════════════════╡
    │▌◆ 1  Steepeek         8,40M  12,4k  35%   284  │ строка
    │▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓│ рельс = доля от лидера
    │▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔                   │ нить = темп прямо сейчас
    ├────────────────────────────────────────────────┤
    │ ● 02:14   AP 82,2k              ИТОГО  16,1M   │ футер
    └────────────────────────────────────────────────┘

Почему полоса — рельс по нижней кромке, а не заливка строки: заливка цветом
класса роняла контраст текста поверх яркого кадра игры до 2.1:1 у худшего
класса, и подбором альфы это не чинилось. Рельс убирает текст с цветного
фона совсем, а тон класса остаётся виден.

Нить темпа — единственное, чего нет у Details, Skada, Kagerou и loa-logs:
они показывают только накопленное. Нить длиннее рельса значит «разгоняется»,
короче — «сдулся», нет — «стоит».

Что нужно от Windows, когда окно висит поверх игры:

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
from pathlib import Path

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import (QAction, QColor, QFont, QFontMetrics, QGuiApplication,
                           QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygon)
from PySide6.QtWidgets import QApplication, QMenu, QWidget

from . import config as cfgmod
from . import hotkeys as hk
from . import skilldb
from .aggregate import UNATTRIBUTED
from .theme import (ACCENT, ALPHA_GLASS, ALPHA_GLASS_CHROME, DANGER, EDGE_DARK,
                    EDGE_LIT, GAP, GROUP, HAIR, HOVER, INK, INK2, INK3, INK_MUTE,
                    L0, L1, L2, LIVE, PAD, PRESS, R_BUTTON, R_CHIP, R_WINDOW,
                    RULE, SHADOW, SORTBG, TRACK, class_triple, fmt_chat, fmt_ui,
                    snap)

IS_WINDOWS = hasattr(ctypes, "windll")

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
HWND_TOPMOST = -1
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010

METRIC_TABS = (("damage", "Урон"), ("heal", "Хил"), ("taken", "Получено"))
METRIC_TITLE = {"damage": "Урон", "heal": "Хил", "taken": "Полученный урон"}

#: Заголовки колонок под каждую вкладку. Потолок — 6 символов: длиннее не
#: влезает в узкое окно, а обрезанный заголовок хуже отсутствующего.
CAPTIONS = {
    "damage": {"dmg": "УРОН", "dps": "DPS", "pct": "%", "hits": "УД.", "crit": "КР."},
    "heal": {"dmg": "ХИЛ", "dps": "HPS", "pct": "%", "hits": "КАСТ", "crit": "КР."},
    "taken": {"dmg": "УРОН", "dps": "DPS", "pct": "%", "hits": "УД.", "crit": "КР."},
}
COL_ORDER = ("dmg", "dps", "pct", "hits", "crit")

#: Эталонные значения для замера ширины колонки. Меряем ОДИН раз по ним, а
#: не по данным на каждом кадре: иначе блок колонок скачет на 56 px, когда
#: лидер переходит через миллион, и колонки видимо ползают.
COL_REF = {"dmg": ("888,88", "M"), "dps": ("888,8", "k"), "pct": ("100%", ""),
           "hits": ("8888", ""), "crit": ("100%", "")}

TOOLBAR = (("play", "Старт / стоп"), ("clear", "Очистить"), ("copy", "Скопировать в чат"))
TOOLBAR_RIGHT = (("menu", "Меню"), ("close", "Выход"))

_ICON_CACHE: dict[tuple, object] = {}
_ICON_EXT = (".png", ".gif", ".webp", ".dds", ".bmp", ".jpg")


def class_icon(icons_dir: str, code: str, size: int, dpr: float = 1.0):
    """Иконка класса из папки пользователя или None."""
    if not icons_dir or not code:
        return None
    key = (icons_dir, code, size, round(dpr, 2))
    if key in _ICON_CACHE:
        return _ICON_CACHE[key]
    pm = None
    folder = Path(icons_dir)
    if folder.is_dir():
        by_stem = {}
        try:
            for f in folder.iterdir():
                if f.suffix.lower() in _ICON_EXT:
                    by_stem.setdefault(f.stem.lower(), f)
        except OSError:
            by_stem = {}
        for name in skilldb.icon_candidates(code):
            f = by_stem.get(name)
            if f is None:
                continue
            loaded = QPixmap(str(f))
            if not loaded.isNull():
                px = max(1, int(round(size * dpr)))
                pm = loaded.scaled(px, px, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                pm.setDevicePixelRatio(dpr)
            break
    _ICON_CACHE[key] = pm
    return pm


def skill_icon(icons_dir: str, name: str, size: int, dpr: float = 1.0):
    """Иконка скилла из локальной папки или None."""
    if not icons_dir or not name:
        return None
    key = (icons_dir, "s:" + name, size, round(dpr, 2))
    if key in _ICON_CACHE:
        return _ICON_CACHE[key]
    pm = None
    for ext in _ICON_EXT:
        f = Path(icons_dir) / (name + ext)
        if f.is_file():
            loaded = QPixmap(str(f))
            if not loaded.isNull():
                px = max(1, int(round(size * dpr)))
                pm = loaded.scaled(px, px, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                pm.setDevicePixelRatio(dpr)
            break
    _ICON_CACHE[key] = pm
    return pm


def make_icon() -> QIcon:
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(17, 22, 28))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(2, 2, 60, 60, 12, 12)
    p.setBrush(ACCENT)
    for i, h in enumerate((16, 28, 40)):
        p.drawRect(14 + i * 13, 50 - h, 8, h)
    p.end()
    return QIcon(pm)


class Overlay(QWidget):
    def __init__(self, engine, cfg: dict, on_settings=None, on_quit=None,
                 on_loot=None):
        super().__init__(None)
        self.engine = engine
        self.cfg = cfg
        self.on_settings = on_settings
        self.on_quit = on_quit
        self.on_loot = on_loot
        self.snapshot: dict = {"rows": [], "loot": {}, "total": 0, "duration": 0,
                               "metric": cfg.get("metric", "damage"), "stats": {}}
        self.selected = ""
        self._drag: QPoint | None = None
        self._resizing = False
        self._hot = ""
        self._hit: list[tuple[str, QRect]] = []
        self._row_rects: list[tuple[QRect, dict]] = []
        self._anim: dict[str, list[float]] = {}
        self._pulse = 0.0
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
        self.setMinimumSize(280, 140)
        self._apply_font()
        self._apply_translucency()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh)
        self.timer.start(250)

        # 30 Гц, а не 60: при данных раз в секунду глаз разницы не видит,
        # а 60 Гц на реальном окне съедает до 39 % ядра вместо обещанных 13 %.
        self.anim_timer = QTimer(self)
        self.anim_timer.timeout.connect(self._tick_anim)
        self.anim_timer.start(33)

        self.topmost_timer = QTimer(self)
        self.topmost_timer.timeout.connect(self._keep_on_top)
        self.topmost_timer.start(2000)

    # -- метрики: всё считается от высоты строки шрифта ---------------------

    def _apply_font(self) -> None:
        size = int(self.cfg.get("font_size", 12))
        fam = "Segoe UI"
        self.f_body = QFont(fam, size)
        self.f_self = QFont(fam, size, QFont.DemiBold)
        self.f_tab = QFont(fam, size, QFont.DemiBold)
        self.f_num = QFont(fam, max(7, size - 1))
        self.f_small = QFont(fam, max(7, size - 3))
        self.f_caps = QFont(fam, max(6, size - 4), QFont.DemiBold)
        self.f_caps.setCapitalization(QFont.AllUppercase)

        fm = QFontMetrics(self.f_body)
        self.H = fm.height()
        self.ROW_H = self.H + 8
        self.HEAD_H = self.H + 8
        self.COL_H = QFontMetrics(self.f_caps).ascent() + 6
        self.FOOT_H = QFontMetrics(self.f_small).height() + 6
        self.SKILL_H = QFontMetrics(self.f_small).height() + 4
        self.RAIL = max(2, round(self.H / 8))
        self.ICON = max(12, min(20, self.H - 5))
        self.BTN = self.H + 4
        # Одна базовая линия на все три кегля в строке
        self.BASE = (self.ROW_H - self.RAIL - 2 + fm.ascent() - fm.descent()) // 2
        self._measure_columns()

    def resizeEvent(self, e) -> None:
        self._colcache.clear()
        super().resizeEvent(e)

    def _measure_columns(self) -> None:
        """Ширины колонок считаются один раз по эталонам, а не по данным."""
        fm_num, fm_small = QFontMetrics(self.f_num), QFontMetrics(self.f_small)
        fm_body, fm_caps = QFontMetrics(self.f_body), QFontMetrics(self.f_caps)
        caps = CAPTIONS.get(self.cfg.get("metric", "damage"), CAPTIONS["damage"])
        self._colw = {}
        self._colcache: dict[int, list] = {}
        for key, (mant, suf) in COL_REF.items():
            fm_val = fm_body if key == "dmg" else fm_num
            value = fm_val.horizontalAdvance(mant) + (
                fm_small.horizontalAdvance(suf) if suf else 0)
            head = fm_caps.horizontalAdvance(caps.get(key, "").upper())
            self._colw[key] = max(value, head) + GAP

    #: Порядок отбрасывания колонок в узком окне. Урон не выбрасывается
    #: никогда: без него таблица теряет смысл.
    DROP_ORDER = ("crit", "hits", "pct", "dps")
    NAME_MIN = 96

    def columns(self, w: int | None = None) -> list[tuple[str, int]]:
        """Видимые колонки. В узком окне лишние отбрасываются.

        Ник важнее любой числовой колонки: строку, где от имени осталось
        «Ste…», читать невозможно, а доля и удары — справочные величины.
        """
        active = [c for c in COL_ORDER
                  if c in self.cfg.get("columns", ["dmg", "dps", "pct"])
                  and c in self._colw]
        if w is None:
            return [(c, self._colw[c]) for c in active]
        cached = self._colcache.get(w)
        if cached is not None:
            return cached
        left = PAD + self.ICON + GAP + 20          # иконка + ранг
        for drop in (None,) + self.DROP_ORDER:
            if drop is not None:
                if len(active) <= 1:
                    break
                active = [c for c in active if c != drop]
            used = sum(self._colw[c] for c in active)
            if w - PAD - left - used >= self.NAME_MIN:
                break
        result = [(c, self._colw[c]) for c in active]
        self._colcache[w] = result
        return result

    @property
    def chrome_h(self) -> int:
        return self.HEAD_H + self.COL_H + 1 + 1 + self.foot_h

    @property
    def foot_h(self) -> int:
        return self.FOOT_H if self.cfg.get("show_loot", True) else 0

    # -- внешний вид --------------------------------------------------------

    def _apply_translucency(self) -> None:
        transparent = bool(self.cfg.get("transparent"))
        self.setAttribute(Qt.WA_TranslucentBackground, transparent)
        self.setWindowOpacity(self.cfg.get("opacity", 1.0) if transparent else 1.0)

    def apply_appearance(self) -> None:
        was_visible = self.isVisible()
        self.hide()
        self._apply_translucency()
        self._apply_font()
        if was_visible:
            self.show()
        self.apply_window_flags()
        self.setup_hotkeys()
        self.update()

    def _bg(self, base: QColor, chrome: bool = False) -> QColor:
        if not self.cfg.get("transparent"):
            return base
        c = QColor(base)
        c.setAlpha(ALPHA_GLASS_CHROME if chrome else ALPHA_GLASS)
        return c

    # -- Win32 --------------------------------------------------------------

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

    # -- действия -----------------------------------------------------------

    def action_toggle_pause(self) -> None:
        self.engine.set_paused(not self.engine.paused)
        self._refresh()

    def action_clear(self) -> None:
        self.engine.reset()
        self.selected = ""
        self._anim.clear()
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
        self._measure_columns()
        self._refresh()

    def action_copy(self) -> None:
        """Строка для вставки в игровой чат (лимит около 255 символов)."""
        rows = [r for r in self.snapshot.get("rows", ()) if r["name"] != UNATTRIBUTED]
        if not rows:
            return
        dur = self.snapshot.get("duration", 0)
        parts = [f"{METRIC_TITLE.get(self.snapshot.get('metric', 'damage'), 'Урон')} "
                 f"{dur // 60}:{dur % 60:02d}:"]
        for i, r in enumerate(rows[:8], 1):
            parts.append(f"{i}.{r['display']} {fmt_chat(r['total'])} "
                         f"({fmt_chat(r['avg'])}dps {r['pct']:.0f}%)")
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

    # -- данные и анимация --------------------------------------------------

    def _refresh(self) -> None:
        self.snapshot = self.engine.snapshot()
        self.update()

    def _tick_anim(self) -> None:
        """Догоняем целевые длины рельса и нити.

        Не QPropertyAnimation: цель меняется посреди анимации четыре раза в
        секунду, и перезапуск даёт рывок на каждом обновлении.
        """
        if not self.isVisible():
            return
        rows = self.snapshot.get("rows", ())
        peak = max((r.get("dps", 0) for r in rows), default=0) or 1
        moved = False
        alive = set()
        for r in rows:
            name = r["name"]
            alive.add(name)
            tgt_bar = max(0.0, min(1.0, r.get("bar", 0.0)))
            tgt_tempo = max(0.0, min(1.0, r.get("dps", 0) / peak)) if peak > 1 else 0.0
            cur = self._anim.get(name)
            if cur is None:
                self._anim[name] = [tgt_bar, tgt_tempo]
                moved = True
                continue
            for i, tgt in ((0, tgt_bar), (1, tgt_tempo)):
                delta = tgt - cur[i]
                if abs(delta) * max(1, self.width()) < 0.5:
                    if cur[i] != tgt:
                        cur[i] = tgt
                        moved = True
                else:
                    cur[i] += delta * 0.30
                    moved = True
        for gone in [k for k in self._anim if k not in alive]:
            del self._anim[gone]
        self._pulse = (self._pulse + 33 / 1600.0) % 1.0
        if moved or self.snapshot.get("active"):
            self.update()

    # -- отрисовка ----------------------------------------------------------

    def _txt(self, p: QPainter, x: int, base: int, text: str, colour: QColor,
             font: QFont | None = None) -> None:
        """Текст с тенью: за окном может оказаться белая вспышка."""
        if font is not None:
            p.setFont(font)
        p.setPen(SHADOW)
        p.drawText(x, base + 1, text)
        p.setPen(colour)
        p.drawText(x, base, text)

    def _txt_right(self, p: QPainter, right: int, base: int, text: str,
                   colour: QColor, font: QFont) -> int:
        p.setFont(font)
        w = QFontMetrics(font).horizontalAdvance(text)
        self._txt(p, right - w, base, text, colour, font)
        return right - w

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        w, h = self.width(), self.height()
        dpr = self.devicePixelRatioF()

        if self.cfg.get("transparent"):
            path = QPainterPath()
            path.addRoundedRect(QRectF(0, 0, w, h), R_WINDOW, R_WINDOW)
            p.fillPath(path, self._bg(L1))
            p.setClipPath(path)
        else:
            p.fillRect(0, 0, w, h, L1)

        self._hit = []
        self._row_rects = []
        snap_ = self.snapshot

        self._paint_head(p, w, snap_)
        self._paint_colheads(p, w)

        top = self.HEAD_H + self.COL_H + 1
        bottom = h - self.foot_h - 1
        rows = [r for r in snap_.get("rows", ()) if r["name"] != UNATTRIBUTED]
        dot = next((r for r in snap_.get("rows", ()) if r["name"] == UNATTRIBUTED), None)

        if not rows and dot is None:
            self._paint_empty(p, w, top, bottom, snap_)
        else:
            y = top
            for i, r in enumerate(rows):
                if y + self.ROW_H > bottom:
                    break
                self._paint_row(p, i + 1, r, y, w, dpr)
                self._row_rects.append((QRect(0, y, w, self.ROW_H), r))
                y += self.ROW_H
                if self.selected == r["name"]:
                    y = self._paint_skills(p, r, y, w, bottom, dpr)
            # Периодический урон — не игрок: без номера, последней строкой
            if dot is not None and y + self.ROW_H <= bottom:
                p.fillRect(QRectF(0, snap(y, dpr), w, 1), HAIR)
                self._paint_row(p, 0, dot, y + 1, w, dpr, muted=True)
                self._row_rects.append((QRect(0, y + 1, w, self.ROW_H), dot))

        if self.foot_h:
            self._paint_footer(p, w, h, snap_, dpr)
        if not self.cfg.get("transparent"):
            p.setPen(QPen(EDGE_LIT, 1))
            p.drawLine(0, 0, w - 1, 0)
            p.drawLine(0, 0, 0, h - 1)
            p.setPen(QPen(EDGE_DARK, 1))
            p.drawLine(0, h - 1, w - 1, h - 1)
            p.drawLine(w - 1, 0, w - 1, h - 1)
        self._paint_grip(p, w, h)

    # -- шапка --------------------------------------------------------------

    def _paint_head(self, p: QPainter, w: int, snap_: dict) -> None:
        p.fillRect(QRect(0, 0, w, self.HEAD_H), self._bg(L2, chrome=True))
        fm = QFontMetrics(self.f_tab)
        base = (self.HEAD_H + fm.ascent() - fm.descent()) // 2
        cur = self.cfg.get("metric", "damage")

        # Блок кнопок: 3 слева + 2 справа + зазор между группами
        buttons_w = PAD + self.BTN * 5 + 2 * 4 + GROUP
        full = sum(fm.horizontalAdvance(l) + GROUP for _k, l in METRIC_TABS)
        labels = dict(METRIC_TABS)
        if PAD + full > w - buttons_w:
            labels = {"damage": "Урон", "heal": "Хил", "taken": "Получ."}
            short = sum(fm.horizontalAdvance(l) + GROUP for l in labels.values())
            if PAD + short > w - buttons_w:
                labels = {"damage": "Урон", "heal": "Хил", "taken": "Пол."}

        x = PAD
        for key, _full_label in METRIC_TABS:
            label = labels[key]
            tw = fm.horizontalAdvance(label)
            rect = QRect(x - 4, 0, tw + 8, self.HEAD_H)
            self._hit.append((f"metric:{key}", rect))
            if key == cur:
                self._txt(p, x, base, label, INK, self.f_tab)
                p.fillRect(QRectF(x, self.HEAD_H - 3, tw, 2), INK)
            else:
                hot = self._hot == f"metric:{key}"
                self._txt(p, x, base, label, INK2 if hot else INK3, self.f_tab)
            x += tw + GROUP

        size = self.BTN
        top = (self.HEAD_H - size) // 2
        bx = w - PAD - size
        for name, _tip in reversed(TOOLBAR_RIGHT):
            self._button(p, QRect(bx, top, size, size), name, snap_)
            bx -= size + 2
        bx -= GROUP - 2
        for name, _tip in reversed(TOOLBAR):
            self._button(p, QRect(bx, top, size, size), name, snap_)
            bx -= size + 2

    def _button(self, p: QPainter, rect: QRect, name: str, snap_: dict) -> None:
        self._hit.append((f"btn:{name}", rect))
        hot = self._hot == f"btn:{name}"
        if hot:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 107, 107, 46) if name == "close" else HOVER)
            p.drawRoundedRect(rect, R_BUTTON, R_BUTTON)
            p.setBrush(Qt.NoBrush)
        colour = INK if hot else INK3
        if name == "close" and hot:
            colour = DANGER
        if name == "play":
            colour = INK if not snap_.get("paused") else ACCENT
        self._icon(p, name, rect, colour, bool(snap_.get("paused")))

    def _icon(self, p: QPainter, name: str, rect: QRect, colour: QColor,
              paused: bool = False) -> None:
        """Значки — примитивами: символы вроде ⚙ есть не во всех шрифтах."""
        cx, cy = rect.center().x() + 1, rect.center().y() + 1
        p.setPen(QPen(colour, 1.5))
        p.setBrush(Qt.NoBrush)
        if name == "play":
            p.setBrush(colour)
            p.setPen(Qt.NoPen)
            if paused:
                p.drawPolygon(QPolygon([QPoint(cx - 3, cy - 6), QPoint(cx + 6, cy),
                                        QPoint(cx - 3, cy + 6)]))
            else:
                p.drawRect(QRect(cx - 5, cy - 5, 4, 11))
                p.drawRect(QRect(cx + 1, cy - 5, 4, 11))
        elif name == "clear":
            p.drawArc(QRect(cx - 6, cy - 6, 12, 12), 50 * 16, 280 * 16)
            p.drawLine(cx + 5, cy - 7, cx + 5, cy - 2)
            p.drawLine(cx + 5, cy - 7, cx + 9, cy - 6)
        elif name == "copy":
            p.drawRect(QRect(cx - 6, cy - 6, 8, 8))
            p.drawRect(QRect(cx - 2, cy - 2, 8, 8))
        elif name == "menu":
            for dy in (-4, 0, 4):
                p.drawLine(cx - 6, cy + dy, cx + 6, cy + dy)
        elif name == "close":
            p.drawLine(cx - 5, cy - 5, cx + 5, cy + 5)
            p.drawLine(cx + 5, cy - 5, cx - 5, cy + 5)

    def _paint_colheads(self, p: QPainter, w: int) -> None:
        top = self.HEAD_H
        p.fillRect(QRect(0, top, w, self.COL_H), self._bg(L2, chrome=True))
        fm = QFontMetrics(self.f_caps)
        base = top + fm.ascent() + 3
        caps = CAPTIONS.get(self.cfg.get("metric", "damage"), CAPTIONS["damage"])
        p.setFont(self.f_caps)
        p.setPen(INK3)
        p.drawText(PAD + self.ICON + GAP + 4, base, "#")
        p.drawText(PAD + self.ICON + GAP + 24, base, "ИГРОК")
        x = w - PAD
        for key, cw in reversed(self.columns(w)):
            x -= cw
            rect = QRect(x, top, cw, self.COL_H)
            self._hit.append((f"col:{key}", rect))
            label = caps.get(key, "")
            p.setPen(INK2 if self._hot == f"col:{key}" else INK3)
            p.drawText(QRect(x, top, cw - GAP, self.COL_H),
                       Qt.AlignRight | Qt.AlignVCenter, label)
        p.fillRect(QRectF(0, top + self.COL_H, w, 1), RULE)

    # -- строка -------------------------------------------------------------

    def _paint_row(self, p: QPainter, rank: int, r: dict, y: int, w: int,
                   dpr: float, muted: bool = False) -> None:
        wash, rail, tempo = class_triple(skilldb.COLOURS.get(r.get("cls", "")))
        cur = self._anim.get(r["name"], [r.get("bar", 0.0), 0.0])
        bar_w = int(w * max(0.0, min(1.0, cur[0])))
        body_h = self.ROW_H - self.RAIL
        rail_y = snap(y + body_h, dpr)
        rail_h = snap(self.RAIL, dpr)

        if not muted:
            p.fillRect(QRect(0, y, bar_w, body_h), wash)
        if self._hot == f"row:{r['name']}":
            p.fillRect(QRect(0, y, w, body_h), HOVER)
        p.fillRect(QRectF(0, rail_y, w, rail_h), TRACK)
        if not muted:
            p.fillRect(QRectF(0, rail_y, bar_w, rail_h), rail)
            tempo_w = int(w * cur[1])
            if self.snapshot.get("active") and abs(tempo_w - bar_w) >= 3:
                p.fillRect(QRectF(0, rail_y + 1, tempo_w, snap(1, dpr)), tempo)
            p.fillRect(QRect(0, y, 3, body_h), ACCENT if r["is_self"] else rail)

        base = y + self.BASE
        x = PAD
        icon = class_icon(cfgmod.icons_dir(self.cfg), r.get("cls", ""), self.ICON, dpr)
        if icon is not None:
            p.drawPixmap(x, y + (body_h - self.ICON) // 2, icon)
        elif r.get("cls"):
            self._class_chip(p, x, y + (body_h - 14) // 2, r["cls"], rail)
        x += self.ICON + GAP

        if not muted:
            self._txt_right(p, x + 16, base, str(rank), INK2, self.f_small)
        x += 20

        cols_w = sum(cw for _k, cw in self.columns(w))
        name_w = max(40, w - PAD - cols_w - x - GAP)
        font = self.f_self if r["is_self"] else self.f_body
        fm = QFontMetrics(font)
        mark = "▾ " if self.selected == r["name"] else ""
        name = fm.elidedText(mark + r["display"], Qt.ElideRight, name_w)
        self._txt(p, x, base, name, INK3 if muted else INK, font)

        right = w - PAD
        for key, cw in reversed(self.columns(w)):
            self._paint_cell(p, key, r, right, base, cw, muted)
            right -= cw

    def _paint_cell(self, p: QPainter, key: str, r: dict, right: int, base: int,
                    cw: int, muted: bool) -> None:
        colour = INK3 if muted else INK2
        if key == "dmg":
            mant, suf = fmt_ui(r["total"])
            end = right
            if suf:
                end = self._txt_right(p, right, base, suf, colour, self.f_small)
            self._txt_right(p, end, base, mant, INK3 if muted else INK, self.f_body)
            return
        if key == "dps":
            value = r["dps"] if r["dps"] >= 1 else r.get("avg", 0)
            if value < 1:
                self._txt_right(p, right, base, "0", INK_MUTE, self.f_num)
                return
            mant, suf = fmt_ui(value)
            end = right
            if suf:
                end = self._txt_right(p, right, base, suf, colour, self.f_small)
            self._txt_right(p, end, base, mant, colour, self.f_num)
            return
        if key == "pct":
            self._txt_right(p, right, base, f"{r['pct']:.0f}%", colour, self.f_num)
            return
        if key == "hits":
            self._txt_right(p, right, base, str(r["hits"]), colour, self.f_num)
            return
        if key == "crit":
            text = f"{r['crit']:.0f}%" if r.get("crit") is not None else "—"
            self._txt_right(p, right, base, text, colour, self.f_num)

    def _class_chip(self, p: QPainter, x: int, y: int, code: str, colour: QColor) -> None:
        """Заглушка без иконок: цвет — не единственный носитель класса."""
        p.setPen(QPen(colour, 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(x, y, 14, 14), R_CHIP, R_CHIP)
        f = QFont(self.f_small)
        f.setPointSize(max(6, f.pointSize() - 1))
        p.setFont(f)
        p.drawText(QRect(x, y, 14, 14), Qt.AlignCenter, code[:2].upper())

    def _paint_skills(self, p: QPainter, r: dict, y: int, w: int, bottom: int,
                      dpr: float) -> int:
        skills = r.get("skills") or []
        auto = max(0, r["total"] - sum(v for _k, v in skills))
        items = list(skills[:6])
        if auto > 0:
            items.append(("автоатака", auto))
        if not items:
            return y
        top_v = max((v for _k, v in items), default=1) or 1
        _wash, rail, _t = class_triple(skilldb.COLOURS.get(r.get("cls", "")))
        wash = QColor(rail)
        wash.setAlpha(38)

        x0 = PAD + self.ICON + GAP
        icons_dir = cfgmod.skill_icons_dir(self.cfg)
        size = self.SKILL_H - 4
        fm = QFontMetrics(self.f_small)
        start = y
        for label, value in items:
            if y + self.SKILL_H > bottom:
                break
            p.fillRect(QRect(x0, y, int((w - x0 - PAD) * value / top_v),
                             self.SKILL_H - 1), wash)
            xi = x0 + 4
            icon = skill_icon(icons_dir, label, size, dpr)
            if icon is not None:
                p.drawPixmap(xi, y + 2, icon)
                xi += size + 4
            base = y + fm.ascent() + 2
            self._txt(p, xi, base, fm.elidedText(label, Qt.ElideRight, int(w * 0.42)),
                      INK2, self.f_small)
            right = w - PAD
            right = self._txt_right(p, right, base, f"{100.0 * value / (r['total'] or 1):.0f}%",
                                    INK3, self.f_small) - GAP
            mant, suf = fmt_ui(value)
            self._txt_right(p, right, base, mant + suf, INK2, self.f_small)
            y += self.SKILL_H
        p.fillRect(QRectF(snap(x0 - 4, dpr), start, snap(1, dpr), y - start), HAIR)
        return y

    # -- пусто и подвал -----------------------------------------------------

    def _paint_empty(self, p: QPainter, w: int, top: int, bottom: int,
                     snap_: dict) -> None:
        msg = snap_.get("error") or ("на паузе — нажмите «Старт»"
                                     if snap_.get("paused") else "ждём боевых событий…")
        p.setFont(self.f_small)
        p.setPen(DANGER if snap_.get("error") else INK3)
        p.drawText(QRect(PAD * 2, top + 14, w - PAD * 4, 60),
                   Qt.AlignHCenter | Qt.TextWordWrap, msg)

    def _paint_footer(self, p: QPainter, w: int, h: int, snap_: dict,
                      dpr: float) -> None:
        top = h - self.FOOT_H
        p.fillRect(QRectF(0, snap(top - 1, dpr), w, snap(1, dpr)), HAIR)
        p.fillRect(QRect(0, top, w, self.FOOT_H), self._bg(L2, chrome=True))
        fm = QFontMetrics(self.f_small)
        base = top + (self.FOOT_H + fm.ascent() - fm.descent()) // 2

        if snap_.get("error"):
            dot, alpha = DANGER, 1.0
        elif snap_.get("paused"):
            dot, alpha = ACCENT, 1.0
        elif snap_.get("active"):
            dot = LIVE
            alpha = 0.55 + 0.45 * abs(1.0 - 2.0 * self._pulse)
        else:
            dot, alpha = INK3, 1.0
        c = QColor(dot)
        c.setAlphaF(alpha)
        p.setBrush(c)
        p.setPen(Qt.NoPen)
        p.drawEllipse(QRect(PAD, top + (self.FOOT_H - 6) // 2, 6, 6))
        p.setBrush(Qt.NoBrush)

        dur = snap_.get("duration", 0)
        left = (f"{dur // 3600}:{dur // 60 % 60:02d}:{dur % 60:02d}" if dur >= 3600
                else f"{dur // 60}:{dur % 60:02d}")
        x = PAD + 6 + GAP
        self._txt(p, x, base, left, INK2, self.f_small)
        x += fm.horizontalAdvance(left) + GROUP

        loot = snap_.get("loot", {})
        slots = []
        for key, label in (("exp", "опыт"), ("ap", "AP"), ("kinah", "кинах")):
            value = loot.get("kinah_in" if key == "kinah" else key)
            if value:
                mant, suf = fmt_ui(value)
                slots.append((label, mant + suf))
        total_txt = fmt_ui(snap_.get("total", 0))
        total_w = (QFontMetrics(self.f_num).horizontalAdvance(total_txt[0])
                   + fm.horizontalAdvance(total_txt[1] + "ИТОГО") + GROUP + GAP)
        for label, value in slots[:2]:
            piece = f"{label} {value}"
            if x + fm.horizontalAdvance(piece) > w - PAD - total_w:
                break
            p.setFont(self.f_small)
            p.setPen(INK3)
            p.drawText(x, base, label)
            x += fm.horizontalAdvance(label + " ")
            self._txt(p, x, base, value, INK2, self.f_small)
            x += fm.horizontalAdvance(value) + GROUP

        right = w - PAD
        if total_txt[1]:
            right = self._txt_right(p, right, base, total_txt[1], INK2, self.f_small)
        right = self._txt_right(p, right, base, total_txt[0], INK, self.f_num) - GAP
        p.setFont(self.f_caps)
        p.setPen(INK3)
        cw = QFontMetrics(self.f_caps).horizontalAdvance("ИТОГО")
        p.drawText(right - cw, base, "ИТОГО")

    def _paint_grip(self, p: QPainter, w: int, h: int) -> None:
        p.setPen(QPen(INK_MUTE, 1))
        for off in (3, 7):
            p.drawLine(w - off - 4, h - 4, w - 4, h - off - 4)

    # -- мышь ---------------------------------------------------------------

    def _hit_at(self, pos) -> str:
        point = pos.toPoint() if hasattr(pos, "toPoint") else pos
        for name, rect in self._hit:
            if rect.contains(point):
                return name
        for rect, row in self._row_rects:
            if rect.contains(point):
                return f"row:{row['name']}"
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
        if hit.startswith(("metric:", "btn:", "col:")):
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
        if kind == "col":
            return
        actions = {
            "play": self.action_toggle_pause,
            "clear": self.action_clear,
            "copy": self.action_copy,
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
        if hot:
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
            "QMenu{background:#161B23;color:#E9EEF3;"
            "border:1px solid rgba(255,255,255,0.09);border-radius:6px;padding:4px}"
            "QMenu::item{padding:6px 20px 6px 12px;border-radius:4px}"
            "QMenu::item:selected{background:rgba(255,255,255,0.08)}"
            "QMenu::separator{height:1px;background:rgba(255,255,255,0.06);margin:4px 6px}"
        )
        stats = self.snapshot.get("stats", {})
        if stats.get("read"):
            act = QAction(f"разобрано {stats.get('parsed', 0)} из {stats['read']} строк",
                          self, enabled=False)
            menu.addAction(act)
            menu.addSeparator()
        for key, label in (("show_loot", "Показывать добычу внизу"),
                           ("click_through", "Клик проходит насквозь"),
                           ("transparent", "Прозрачный фон"),
                           ("always_on_top", "Поверх всех окон")):
            act = QAction(label, self, checkable=True, checked=bool(self.cfg.get(key)))
            act.triggered.connect(lambda _c, k=key: self._toggle_cfg(k))
            menu.addAction(act)
        menu.addSeparator()
        menu.addAction("Добыча…", lambda: self.on_loot and self.on_loot())
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
