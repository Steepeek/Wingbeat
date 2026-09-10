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
import math
import time
from ctypes import wintypes
from pathlib import Path

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import (QAction, QColor, QFont, QFontMetrics, QGuiApplication,
                           QIcon, QImage, QLinearGradient, QPainter,
                           QPainterPath, QPen, QPixmap, QPolygon)
from PySide6.QtWidgets import QApplication, QMenu, QWidget

from . import assets
from . import config as cfgmod
from . import hotkeys as hk
from . import itemdb
from . import sessions as sessmod
from . import skilldb
from .aggregate import UNATTRIBUTED, UNKNOWN_HEALER
from .parser import SELF
from .version import __version__, display as version_display

#: Строки, у которых нет игрока-владельца: рисуются приглушённо и
#: последними, в чат не копируются. Периодический урон без автора и
#: хил, у которого клиент не назвал лекаря.
NO_OWNER = (UNATTRIBUTED, UNKNOWN_HEALER)
from .theme import (ACCENT, ALPHA_GLASS, ALPHA_GLASS_CHROME, DANGER, EDGE_DARK,
                    EDGE_LIT, GAP, GOLD, GOLD_DIM, GROUP, HAIR, HOVER, INK,
                    INK2, INK3, INK_MUTE,
                    L0, L1, L2, LIVE, PAD, PRESS, R_BUTTON, R_CHIP, R_WINDOW,
                    RULE, SHADOW, SORTBG, TRACK, class_triple, fmt_chat, fmt_ui,
                    snap)

IS_WINDOWS = hasattr(ctypes, "windll")

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
HWND_TOPMOST, HWND_NOTOPMOST = -1, -2
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010

#: Название в шапке. Отдельной константой: оно попадает и в заголовок
#: окна, и в подпись снимка экрана, и меняться должно в одном месте.
APP_NAME = "Wingbeat"

METRIC_TABS = (("damage", "Damage"), ("heal", "Healing"), ("taken", "Taken"),
               ("loot", "Loot"), ("sessions", "Sessions"))

#: Значок для каждой вкладки. Рисуются примитивами: подходящих символов нет
#: ни в одном системном шрифте, а тащить ради четырёх картинок шрифт иконок
#: в сборку дороже, чем два десятка строк QPainter. Вектор тут ещё и
#: единственный вариант по существу: размер значков настраивается, окно
#: живёт при DPR 1 и 2, а готовые картинки интерфейса в клиенте лежат по
#: 20-24 px и на кнопке в 48 px расплываются.
TAB_GLYPH = {"damage": "sword", "heal": "cross", "taken": "shield",
             "loot": "chest", "sessions": "scroll"}

#: Крупные кнопки действий. Порядок слева направо — по частоте нажатий.
ACTIONS = (("play", "Start / pause"), ("clear", "Reset"),
           ("boss", "Boss damage only"), ("copy", "Copy to game chat"),
           ("shot", "Screenshot"), ("stream", "Streamer mode"),
           ("settings", "Settings"), ("about", "About Wingbeat"))
METRIC_TITLE = {"damage": "Damage", "heal": "Healing", "taken": "Damage taken",
                "loot": "Loot", "sessions": "Sessions"}

#: Заголовки колонок под каждую вкладку. Потолок — 6 символов: длиннее не
#: влезает в узкое окно, а обрезанный заголовок хуже отсутствующего.
CAPTIONS = {
    "damage": {"dmg": "DMG", "dps": "DPS", "pct": "%", "hits": "HITS", "crit": "CRIT"},
    "heal": {"dmg": "HEAL", "dps": "HPS", "pct": "%", "hits": "CASTS", "crit": "CRIT"},
    "taken": {"dmg": "DMG", "dps": "DPS", "pct": "%", "hits": "HITS", "crit": "CRIT"},
    "loot": {"dmg": "QTY", "dps": "", "pct": "%", "hits": "TYPES", "crit": ""},
    # У сессии «удары» — это убитые мобы, а доля — вклад своего урона в общий.
    "sessions": {"dmg": "DMG", "dps": "DPS", "pct": "YOU%", "hits": "KILLS",
                 "crit": ""},
}
#: На вкладке добычи нет ни DPS, ни критов — там считают предметы.
LOOT_COLUMNS = ("dmg", "pct", "hits")
#: У сессии крита нет: он у каждого игрока свой, а строка — про всю сессию.
SESSION_COLUMNS = ("dmg", "dps", "pct", "hits")
#: Полоска сводки: значок, подпись, ключ в счётчике добычи. Порядок сверху вниз.
STATS_ROWS = (("exp", "XP", "exp"), ("kinah", "Kinah", "kinah_in"),
              ("ap", "AP", "ap"), ("glory", "Glory Points", "glory"),
              ("kills", "Mobs killed", "kills"))

#: Подпись строки автоатаки. Вынесена в константу: по ней же ищется иконка.
AUTOATTACK = "auto-attack"
COL_ORDER = ("dmg", "dps", "pct", "hits", "crit")

#: Эталонные значения для замера ширины колонки. Меряем ОДИН раз по ним, а
#: не по данным на каждом кадре: иначе блок колонок скачет на 56 px, когда
#: лидер переходит через миллион, и колонки видимо ползают.
COL_REF = {"dmg": ("888,88", "M"), "dps": ("888,8", "k"), "pct": ("100%", ""),
           "hits": ("8888", ""), "crit": ("100%", "")}

#: Справа в шапке остаётся только закрытие. Кнопку меню убрали: всё, что
#: в нём было, либо лежит в настройках, либо вызывается правым кликом по
#: окну и значком в трее, а место в шапке дороже.
#: Что показывать при наведении: (заголовок, пояснение). Пиктограммы в
#: шапке без слов не угадываются — облако для «скопировать в чат» тому
#: пример, — а одного названия мало: «Reset» не говорит, что счёт при этом
#: уезжает в файл сессии. Поэтому две строки, не больше.
HELP: dict[str, tuple[str, str]] = {
    "btn:play": ("Start / pause",
                 "Stops counting. The log keeps being read, so nothing is lost."),
    "btn:clear": ("Reset",
                  "Clears the table and saves the finished session to disk."),
    "btn:boss": ("Boss damage only",
                 "Counts only damage on the main target, ignoring adds."),
    "btn:copy": ("Copy to game chat",
                 "Puts the result on the clipboard, formatted for chat."),
    "btn:shot": ("Screenshot",
                 "Copies a picture of this window to the clipboard."),
    "btn:settings": ("Settings",
                     "Game folder, window, sessions and hotkeys."),
    "btn:about": ("About Wingbeat",
                  "Version, what it does, links and folders."),
    "btn:close": ("Quit", "Closes Wingbeat completely."),
    "btn:stream": ("Streamer mode",
                   "Bars only, on a chroma key background for OBS."),
    "btn:stream_off": ("Leave streamer mode",
                       "Brings back the tabs, buttons and footer."),
    "btn:live": ("Back to live",
                 "Leaves the saved session and shows current numbers again."),
    "metric:damage": ("Damage", "Damage and DPS for everyone you can see."),
    "metric:heal": ("Healing", "Healing done, split by skill."),
    "metric:taken": ("Taken", "Damage taken, split by source."),
    "metric:loot": ("Loot", "Items picked up during this session."),
    "metric:sessions": ("Sessions", "Saved sessions. Click one to open it here."),
    "col:dmg": ("Damage", "Total for the current count."),
    "col:dps": ("DPS", "Sliding window; average once the fight is over."),
    "col:pct": ("Share", "Share of everything shown in the table."),
    "col:hits": ("Hits", "Damage lines counted, ticks included."),
    "col:crit": ("Crit", "Share of hits that were critical."),
}

TOOLBAR_RIGHT = (("close", "Close"),)

_ICON_CACHE: dict[tuple, object] = {}
_ICON_EXT = (".png", ".gif", ".webp", ".dds", ".bmp", ".jpg")


def class_icon(icons_dir: str, code: str, size: int, dpr: float = 1.0):
    """Эмблема класса: сначала папка пользователя, потом ассет-пак."""
    if not code:
        return None
    key = (icons_dir, code, size, round(dpr, 2))
    if key in _ICON_CACHE:
        return _ICON_CACHE[key]
    pm = None
    folder = Path(icons_dir) if icons_dir else None
    if folder is not None and folder.is_dir():
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
    if pm is None:
        pm = _from_pack(assets.class_icon_path(code), size, dpr)
    _ICON_CACHE[key] = pm
    return pm


def _from_pack(path, size: int, dpr: float):
    """Иконка из ассет-пака. Пак — запасной вариант: папка пользователя выше."""
    if path is None:
        return None
    loaded = QPixmap(str(path))
    if loaded.isNull():
        return None
    px = max(1, int(round(size * dpr)))
    pm = loaded.scaled(px, px, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    pm.setDevicePixelRatio(dpr)
    return pm


def skill_icon(icons_dir: str, name: str, size: int, dpr: float = 1.0):
    """Иконка скилла: сначала папка пользователя, потом ассет-пак."""
    if not name:
        return None
    key = (icons_dir, "s:" + name, size, round(dpr, 2))
    if key in _ICON_CACHE:
        return _ICON_CACHE[key]
    pm = None
    if icons_dir:
        for ext in _ICON_EXT:
            f = Path(icons_dir) / (name + ext)
            if f.is_file():
                loaded = QPixmap(str(f))
                if not loaded.isNull():
                    px = max(1, int(round(size * dpr)))
                    pm = loaded.scaled(px, px, Qt.KeepAspectRatio,
                                       Qt.SmoothTransformation)
                    pm.setDevicePixelRatio(dpr)
                break
    if pm is None:
        pm = _from_pack(assets.skill_icon_path(name), size, dpr)
    _ICON_CACHE[key] = pm
    return pm


_ART_CACHE: dict[str, object] = {}


def frame_pixmap():
    """Картинка рамки окна целиком. Нарезает её отрисовка."""
    if "frame" not in _ART_CACHE:
        path = assets.ui_icon_path("frame")
        pm = QPixmap(str(path)) if path else None
        _ART_CACHE["frame"] = None if (pm is None or pm.isNull()) else pm
    return _ART_CACHE["frame"]


def panel_pixmap():
    """Фактура поля таблицы. Тянется, не повторяется."""
    if "panel" not in _ART_CACHE:
        path = assets.ui_icon_path("panel")
        pm = QPixmap(str(path)) if path else None
        _ART_CACHE["panel"] = None if (pm is None or pm.isNull()) else pm
    return _ART_CACHE["panel"]


def art_pixmap(name: str):
    """Любой рисованный элемент оформления из пака, с кэшем."""
    if name not in _ART_CACHE:
        path = assets.ui_icon_path(name)
        pm = QPixmap(str(path)) if path else None
        _ART_CACHE[name] = None if (pm is None or pm.isNull()) else pm
    return _ART_CACHE[name]


def header_pixmap():
    """Фактура шапки и полосы кнопок. Растягивается на всю ширину."""
    if "header" not in _ART_CACHE:
        path = assets.ui_icon_path("header")
        pm = QPixmap(str(path)) if path else None
        _ART_CACHE["header"] = None if (pm is None or pm.isNull()) else pm
    return _ART_CACHE["header"]


def ui_icon(name: str, size: int, dpr: float = 1.0):
    """Иконка интерфейса из ассет-пака: kinah, exp, autoattack."""
    if not name:
        return None
    key = ("ui", name, size, round(dpr, 2))
    if key in _ICON_CACHE:
        return _ICON_CACHE[key]
    pm = _from_pack(assets.ui_icon_path(name), size, dpr)
    _ICON_CACHE[key] = pm
    return pm


_TAB_CACHE: dict = {}


def tab_icon(key: str, size: int, dpr: float, lit: bool):
    """Значок вкладки — писаный спрайт из клиента, если пак собран.

    Неактивная вкладка обесцвечивается. Перекрасить писаный спрайт, как
    вектор, нельзя, а состояние показывать надо: одной прозрачности мало,
    активная и соседняя различались едва. Заодно снимается вопрос четырёх
    насыщенных пятен в полосе, где цветом кодируются классы, «это ты» и
    «бой идёт»: цвет остаётся ровно у той вкладки, на которой стоишь.
    """
    ck = (key, size, round(dpr, 2), lit)
    if ck in _TAB_CACHE:
        return _TAB_CACHE[ck]
    pm = _from_pack(assets.ui_icon_path("tab_" + key), size, dpr)
    if pm is not None and not lit:
        img = pm.toImage().convertToFormat(QImage.Format_ARGB32)
        for y in range(img.height()):
            for x in range(img.width()):
                c = img.pixelColor(x, y)
                if not c.alpha():
                    continue
                g = round(0.2126 * c.red() + 0.7152 * c.green()
                          + 0.0722 * c.blue())
                img.setPixelColor(x, y, QColor(g, g, g, c.alpha()))
        pm = QPixmap.fromImage(img)
        pm.setDevicePixelRatio(dpr)
    _TAB_CACHE[ck] = pm
    return pm


def item_icon_named(label: str, size: int, dpr: float = 1.0):
    """Иконка расходника по названию, с кэшем.

    Кэш здесь обязателен: без него картинка перечитывалась с диска на
    КАЖДОМ кадре раскрытой строки, то есть тридцать раз в секунду.
    """
    key = ("byname", label, size, round(dpr, 2))
    if key not in _ICON_CACHE:
        _ICON_CACHE[key] = _from_pack(assets.item_icon_by_name(label), size, dpr)
    return _ICON_CACHE[key]


def item_icon(item_id: str, size: int, dpr: float = 1.0):
    """Иконка предмета из ассет-пака по номеру."""
    if not item_id:
        return None
    key = ("item", item_id, size, round(dpr, 2))
    if key in _ICON_CACHE:
        return _ICON_CACHE[key]
    pm = _from_pack(assets.item_icon_path(item_id), size, dpr)
    _ICON_CACHE[key] = pm
    return pm


def make_icon() -> QIcon:
    """Значок программы. Из пака, если он есть, иначе рисуем примитивами."""
    art = art_pixmap("appicon")
    if art is not None:
        return QIcon(art)
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
                 on_about=None):
        super().__init__(None)
        self.engine = engine
        self.cfg = cfg
        self.on_settings = on_settings
        self.on_quit = on_quit
        self.on_about = on_about
        self.snapshot: dict = {"rows": [], "loot": {}, "total": 0, "duration": 0,
                               "metric": cfg.get("metric", "damage"), "stats": {}}
        self.selected = ""
        #: Прокрутка таблицы в пикселях и размеры для её ограничения.
        self._scroll = 0
        self._content_h = 0
        self._view_h = 1
        #: Кадры оставшейся засветки после снимка окна. Гасит
        #: таймер анимации, отдельный таймер заводить незачем.
        self._flash = 0
        self._drag: QPoint | None = None
        self._resizing = False
        self._hot = ""
        #: Прямоугольник элемента под курсором — над ним встаёт подсказка.
        self._hot_rect: QRect | None = None
        self._hit: list[tuple[str, QRect]] = []
        self._row_rects: list[tuple[QRect, dict]] = []
        self._anim: dict[str, list[float]] = {}
        self._pulse = 0.0
        self.hotkeys: hk.HotkeyManager | None = None
        #: Прочитанные с диска сессии и время чтения. Список обновляется
        #: редко: файлы меняются только при «Очистить» и при выходе, а
        #: перечитывать каталог четыре раза в секунду незачем.
        self._sessions: list[dict] = []
        self._sessions_at = 0.0
        #: Последняя прочитанная сессия целиком: (путь, данные).
        self._session_cache: tuple[str | None, dict | None] = (None, None)
        #: Сессия, открытая в обычных вкладках. None — живые данные.
        self._open_session: dict | None = None
        #: Пока открыто окно настроек, метр не лезет наверх.
        self._suspend_topmost = False

        # Флаг «поверх всех окон» ставится ПО НАСТРОЙКЕ, а не намертво.
        # Раньше он был зашит здесь, и галочка в настройках ничего не
        # меняла: окно висело сверху всегда, в том числе над собственным
        # окном настроек.
        self.setWindowFlags(self._flags())
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setWindowTitle(APP_NAME)
        self.setMouseTracking(True)

        w = cfg["window"]
        self.setGeometry(w["x"], w["y"], w["w"], w["h"])
        # Минимум считается ВНУТРИ _apply_font: он зависит от размеров
        # шрифта и иконок, которых до этого вызова ещё не существует.
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

    #: Запасные семейства, если выбранного в системе нет.
    FONT_FALLBACK = ("Bahnschrift", "Segoe UI", "Tahoma", "Verdana", "Arial")

    def _apply_font(self) -> None:
        size = int(self.cfg.get("font_size", 12))
        fam = self.cfg.get("font_family") or self.FONT_FALLBACK[0]
        self.f_body = QFont(fam, size)
        for f in (self.f_body,):
            f.setFamilies([fam, *self.FONT_FALLBACK])
        self.f_self = QFont(fam, size, QFont.DemiBold)
        self.f_tab = QFont(fam, size, QFont.DemiBold)
        self.f_num = QFont(fam, max(7, size - 1))
        self.f_small = QFont(fam, max(7, size - 3))
        self.f_caps = QFont(fam, max(6, size - 4), QFont.DemiBold)
        self.f_caps.setCapitalization(QFont.AllUppercase)
        # Заголовок: чуть крупнее вкладок, с разрядкой между буквами.
        self.f_title = QFont(fam, size + 1, QFont.DemiBold)
        self.f_title.setLetterSpacing(QFont.PercentageSpacing, 108)
        # Полоска сводки: крупный жирный кегль, отдельный от таблицы.
        self.f_stat = QFont(fam, size + 3, QFont.Bold)
        self.f_stat_cap = QFont(fam, size, QFont.DemiBold)

        fm = QFontMetrics(self.f_body)
        self.H = fm.height()
        self.HEAD_H = self.H + 8
        self.COL_H = QFontMetrics(self.f_caps).ascent() + 6
        self.FOOT_H = QFontMetrics(self.f_small).height() + 6
        self.SKILL_H = QFontMetrics(self.f_small).height() + 4
        self.RAIL = max(2, round(self.H / 8))
        self.BTN = self.H + 4

        # ОДИН размер на все иконки, которые изображают вещь: эмблема класса,
        # иконка скилла, предмета, опыта и кинары. Раньше они жили каждая по
        # своим правилам — 16, 44 и 42 пикселя рядом друг с другом — и это
        # бросалось в глаза. Всё, что ниже, считается от этого числа, включая
        # высоты строк: подгонять их отдельно значит снова разъехаться.
        self.ICON = max(20, min(64, int(self.cfg.get("icon_size", 37))))
        self.LOOT_ICON = self.ICON
        self.LOOT_H = self.ICON + 8
        self.SKILL_H = self.LOOT_H
        self.STATS_ICON = self.ICON
        self.STATS_H = 0
        # Строка игрока обязана вмещать эмблему класса, иначе та обрежется.
        self.ROW_H = max(self.H + 8, self.ICON + 6)
        # Значок вкладки чуть меньше: он рядом с текстом подписи, и вровень
        # с ним смотрится соразмернее, чем вровень с иконкой предмета.
        self.TAB_ICON = max(16, min(self.ICON, self.H + 6))
        self.GRIP = max(12, min(20, self.H - 4))
        self.HEAD_H = max(self.H + 14, self.TAB_ICON + 10)
        # Полоса с логотипом и названием над вкладками. Высота от кегля,
        # чтобы при крупном шрифте она не выглядела приплюснутой.
        self.TITLE_ICON = max(14, min(28, self.H + 2))
        self.TITLE_H = self.TITLE_ICON + 8
        # Панель действий: размер кнопки настраивается, потому что вкус на
        # «достаточно крупно» у всех разный, а места в оверлее мало.
        self.ACT = max(28, min(96, int(self.cfg.get("action_size", 48))))
        # Полоса кнопок есть всегда: без неё окно теряет старт, очистку и
        # копирование в чат, а искать их в меню в бою никто не станет.
        self.ACT_H = self.ACT + 10
        # Одна базовая линия на все три кегля в строке
        self.BASE = (self.ROW_H - self.RAIL - 2 + fm.ascent() - fm.descent()) // 2
        # Сводка меряется последней: её раскладка зависит от всех
        # остальных высот обвязки, включая подвал и рамку.
        self._stats_w = None
        self._measure_stats(self.width())
        self._measure_columns()
        self._apply_min_size()

    #: Сколько строк таблицы обязано влезать при минимальном размере.
    MIN_ROWS_VISIBLE = 3

    def _apply_min_size(self) -> None:
        """Минимум окна = обвязка плюс несколько строк.

        Прежние 280x140 были меньше самой обвязки: при заводских размерах
        она занимает 259 px, и в окне минимального размера таблицы не
        оставалось вовсе — подвал рисовался поверх кнопок. Минимум обязан
        ехать вместе с размером шрифта и иконок, поэтому считается здесь,
        а не задаётся числом.
        """
        inset = self.frame_inset() * 2
        h = self.chrome_h + self.MIN_ROWS_VISIBLE * self.ROW_H + inset
        self.setMinimumSize(360, h)

    def resizeEvent(self, e) -> None:
        # Размер берём из события: self.width() внутри resizeEvent
        # ещё старый, и полоска сводки считалась бы по прежней ширине.
        self._measure_stats(e.size().width())
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
        wanted = self.cfg.get("columns", ["dmg", "dps", "pct"])
        metric = self.cfg.get("metric")
        if metric == "loot":
            wanted = LOOT_COLUMNS
        elif metric == "sessions":
            wanted = SESSION_COLUMNS
        active = [c for c in COL_ORDER if c in wanted and c in self._colw]
        if w is None:
            return [(c, self._colw[c]) for c in active]
        # Ключ включает вкладку: на добыче набор колонок другой (LOOT_COLUMNS),
        # и кэш только по ширине оставлял на ней колонку DPS от предыдущей
        # вкладки — с нулями во всех строках.
        ckey = (w, self.cfg.get("metric", "damage"))
        cached = self._colcache.get(ckey)
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
        self._colcache[ckey] = result
        return result

    @property
    def chrome_h(self) -> int:
        return (self.TITLE_H + self.HEAD_H + self.ACT_H + self.STATS_H
                + self.COL_H + 1 + 1 + self.foot_h)

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

    def _flags(self):
        flags = (Qt.FramelessWindowHint | Qt.Tool
                 | Qt.WindowDoesNotAcceptFocus)
        if self.cfg.get("always_on_top", True):
            flags |= Qt.WindowStaysOnTopHint
        return flags

    def apply_window_flags(self) -> None:
        want = self._flags()
        if self.windowFlags() != want:
            # setWindowFlags прячет окно и сбрасывает расширенные стили,
            # поэтому показываем заново и ставим стили после него.
            visible = self.isVisible()
            self.setWindowFlags(want)
            if visible:
                self.show()
        add = WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
        if self.cfg.get("click_through"):
            self._ex_style(add=add | WS_EX_TRANSPARENT)
        else:
            self._ex_style(add=add, remove=WS_EX_TRANSPARENT)
        self._apply_topmost()

    def _apply_topmost(self) -> None:
        """Сказать Windows про порядок окон прямо сейчас.

        Одного Qt-флага мало в обе стороны: игра сбрасывает чужой z-order
        при переключении фокуса, а снятый флаг сам по себе не опускает
        окно вниз — нужен явный HWND_NOTOPMOST.
        """
        if not IS_WINDOWS:
            return
        top = HWND_TOPMOST if self.cfg.get("always_on_top", True) else HWND_NOTOPMOST
        ctypes.windll.user32.SetWindowPos(
            wintypes.HWND(self.hwnd), wintypes.HWND(top),
            0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)

    def _keep_on_top(self) -> None:
        # Подтверждаем позицию, только пока она включена: выключенную
        # настройку таймер не должен возвращать обратно.
        if not IS_WINDOWS or not self.cfg.get("always_on_top", True):
            return
        if self._suspend_topmost:
            return
        self._apply_topmost()

    def suspend_topmost(self, on: bool) -> None:
        """Отпустить верхний слой, пока открыто окно настроек.

        Иначе таймер раз в две секунды поднимает метр обратно поверх
        диалога, и человек настраивает вслепую.
        """
        self._suspend_topmost = on
        if not IS_WINDOWS:
            return
        if on:
            ctypes.windll.user32.SetWindowPos(
                wintypes.HWND(self.hwnd), wintypes.HWND(HWND_NOTOPMOST),
                0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
        else:
            self._apply_topmost()

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
        # Выйти из режима стримера можно и с клавиатуры: кнопка в углу
        # маленькая, а руки во время эфира заняты игрой.
        self.hotkeys.register(binds.get("streamer", ""),
                              self.action_toggle_streamer)

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
        # «Очистить» относится к живому счёту, а не к просмотру прошлого:
        # сначала возвращаемся из открытой сессии.
        self.close_session_view()
        # Кнопка закрывает сессию и кладёт её файлом на диск, поэтому
        # список на вкладке сессий устарел ровно в этот момент.
        self.engine.reset()
        self.selected = ""
        self._anim.clear()
        self._sessions_at = 0.0
        self._refresh()

    def action_toggle_click(self) -> None:
        self.cfg["click_through"] = not self.cfg.get("click_through")
        self.apply_window_flags()
        self.update()

    def action_toggle_hide(self) -> None:
        self.setVisible(not self.isVisible())

    def set_metric(self, metric: str) -> None:
        self.cfg["metric"] = metric
        if metric == "sessions":
            self._sessions_at = 0.0      # открыли вкладку — перечитать сразу
        self.selected = ""
        self._scroll = 0          # у новой вкладки своя длина списка
        self._colcache.clear()
        self._measure_columns()
        self._refresh()

    #: Лимит строки игрового чата. Берём с запасом: часть символов уходит на
    #: служебные обёртки, а обрезанная строка теряет хвост молча.
    CHAT_LIMIT = 240

    def action_screenshot(self) -> None:
        """Снимок окна в буфер обмена, ровно по текущему размеру окна.

        grab() снимает сам виджет, а не область экрана, поэтому размер
        всегда совпадает с окном, что бы ни лежало поверх, и в кадр не
        попадает ни игра, ни другие окна. Наведение и полосу прокрутки
        на время снимка убираем: на картинке они выглядят случайным
        артефактом.
        """
        hot, self._hot = self._hot, ""
        self.repaint()
        pixmap = self.grab()
        self._hot = hot
        self.update()
        QApplication.clipboard().setPixmap(pixmap)
        self._flash = 12          # короткая засветка кромки: снимок сделан
        self.update()

    def action_copy(self) -> None:
        text = self.copy_text()
        if text:
            QApplication.clipboard().setText(text)

    def copy_text(self) -> str:
        """Строка для вставки в игровой чат.

        Вид: «Урон 2:14 | Steepeek 8.40M (1825 dps) | Weisti 5.58M (1800 dps)».
        Записи разделены вертикальной чертой, номера мест не пишем — порядок
        и так по убыванию. Числа форматирует fmt_chat: клиент Aion работает
        в cp1251, и узкий пробел из таблицы туда не проходит.
        """
        snap = self.snapshot
        metric = snap.get("metric", "damage")
        rows = [r for r in snap.get("rows", ()) if r["name"] not in NO_OWNER]
        if not rows:
            return ""

        dur = snap.get("duration", 0)
        head = METRIC_TITLE.get(metric, "Damage")
        if metric != "loot":
            head += f" {dur // 60}:{dur % 60:02d}"

        entries = []
        for r in rows[:10]:
            if metric == "loot":
                entries.append(f"{r['display']} {r['total']} pcs")
            else:
                rate = "hps" if metric == "heal" else "dps"
                entries.append(f"{r['display']} {fmt_chat(r['total'])} "
                               f"({fmt_chat(r['avg'])} {rate})")

        # Режем по лимиту чата, не разрывая запись пополам
        lines, line = [], head
        for entry in entries:
            candidate = f"{line} | {entry}"
            if len(candidate) > self.CHAT_LIMIT:
                lines.append(line)
                line = entry
            else:
                line = candidate
        if line:
            lines.append(line)
        return "\n".join(lines)

    # -- данные и анимация --------------------------------------------------

    #: Как часто перечитывать каталог сессий, пока вкладка открыта.
    SESSIONS_TTL = 5.0

    def _refresh(self) -> None:
        metric = self.cfg.get("metric")
        if self._open_session and metric in self.SESSION_METRICS:
            # На экране прошлое: живой движок продолжает считать в фоне,
            # но показываем сохранённое.
            self.snapshot = self._saved_snapshot(metric)
            self.update()
            return
        snap = self.engine.snapshot()
        if metric == "sessions":
            snap = dict(snap)
            snap.update(self._sessions_snapshot())
        self.snapshot = snap
        self.update()

    def open_session(self, path: str) -> None:
        """Открыть сохранённую сессию в обычных вкладках.

        Раньше клик по строке лишь раскрывал список участников — то есть
        сессию можно было увидеть, но не РАЗОБРАТЬ: ни скиллов, ни хила,
        ни полученного урона. Теперь метр переключается на неё целиком,
        как на прошлый бой в чужих метрах, и живые данные при этом никуда
        не деваются — они продолжают считаться в фоне.
        """
        data = self._session_full(path)
        if not data:
            return
        self._open_session = data
        self.selected = ""
        self._scroll = 0
        self._anim.clear()
        # Уходим с вкладки списка на урон: смотреть сессию человек пришёл
        # ради цифр, а не ради строки в перечне.
        if self.cfg.get("metric") == "sessions":
            self.cfg["metric"] = "damage"
            self._colcache.clear()
            self._measure_columns()
        self._refresh()

    def close_session_view(self) -> None:
        """Вернуться к живым данным."""
        if not self._open_session:
            return
        self._open_session = None
        self.selected = ""
        self._scroll = 0
        self._anim.clear()
        self._refresh()

    #: Соответствие вкладки и раздела в файле сессии.
    SESSION_METRICS = {"damage": "damage", "heal": "heal", "taken": "taken"}

    def _saved_snapshot(self, metric: str) -> dict:
        """Снимок из сохранённой сессии — в том же виде, что и живой."""
        data = self._open_session or {}
        rows_raw = data.get(self.SESSION_METRICS.get(metric, "damage"), [])
        rows, total = [], sum(int(r.get("total", 0)) for r in rows_raw)
        top = max((int(r.get("total", 0)) for r in rows_raw), default=0) or 1
        boss = data.get("boss") or ""
        for r in rows_raw:
            hits = int(r.get("hits", 0))
            crits = int(r.get("crits", 0))
            name = r.get("name", "?")
            targets = dict(r.get("targets") or [])
            rows.append({
                "name": name,
                "display": name,
                "cls": r.get("cls", ""),
                "cls_name": skilldb.CLASSES.get(r.get("cls", ""), ""),
                "section": "party",
                "total": int(r.get("total", 0)),
                # У сохранённой сессии «текущего» DPS быть не может — бой
                # давно кончился. Показываем средний по активному времени:
                # это единственная честная цифра для прошлого.
                "dps": float(r.get("avg", 0.0)),
                "avg": float(r.get("avg", 0.0)),
                "hits": hits,
                "crit": (100.0 * crits / hits) if hits else None,
                "max": int(r.get("max", 0)),
                "boss_total": int(targets.get(boss, 0)),
                "boss_hits": 0,
                "is_self": name == SELF,
                "is_party": True,
                "skills": [tuple(x) for x in (r.get("skills") or [])],
                "buffs": [],
            })
        rows.sort(key=lambda r: (-r["total"], r["name"]))
        for r in rows:
            r["pct"] = 100.0 * r["total"] / (total or 1)
            r["bar"] = r["total"] / top
        limit = self.cfg.get("max_rows", 12)
        dur = int(data.get("duration", 0))
        return {
            "rows": rows[:limit + 2],
            "sections": [{"key": "all", "total": total or 1, "count": len(rows)}],
            "split": False,
            "hidden": max(0, len(rows) - limit),
            "total": total,
            "duration": dur,
            "target": boss,
            "boss": boss,
            "boss_only": False,
            "kills": int(data.get("kill_count", 0)),
            "loot": dict(data.get("loot", {})),
            "items": {},
            "rolls": [],
            "window": self.cfg.get("dps_window", 10),
            "metric": metric,
            "mode": "session",
            "self_name": data.get("self_name", ""),
            "party": list(data.get("party", [])),
            "active": False,
            "stats": {},
            "error": "",
            "paused": False,
            # Метка для подвала: по ней видно, что на экране прошлое.
            "saved_label": self._session_label(data),
            "saved_path": data.get("path", ""),
        }

    def _sessions_snapshot(self) -> dict:
        """Строки вкладки «Сессии» — из файлов, а не из памяти метра.

        Одна строка — одна закрытая сессия; раскрытие показывает её состав
        по игрокам, тем же механизмом, что и разбор по скиллам.
        """
        now = time.monotonic()
        if now - self._sessions_at > self.SESSIONS_TTL:
            self._sessions = sessmod.listing()
            self._sessions_at = now

        rows = []
        top = max((d.get("total", 0) for d in self._sessions), default=0) or 1
        for data in self._sessions:
            you = data.get("you", {}) or {}
            total = int(data.get("total", 0))
            # Состав по игрокам лежит в полном файле, а он тяжёлый. Читаем
            # его только для РАСКРЫТОЙ строки: список на экране обновляется
            # постоянно, и тянуть ради него сотни килобайт на каждую сессию
            # значило подвешивать окно на секунды.
            players, classes = [], {}
            rows.append({
                "name": data.get("path", ""),
                "display": self._session_label(data),
                "cls": data.get("self_class", ""),
                "cls_name": skilldb.CLASSES.get(data.get("self_class", ""), ""),
                "section": "party",
                "total": total,
                # DPS сессии — свой средний, а не общий по всем: сравнивать
                # свои заходы между собой человек будет именно по нему.
                "dps": float(you.get("avg", 0.0)),
                "avg": float(you.get("avg", 0.0)),
                "hits": int(data.get("kill_count", 0)),
                "crit": None,
                "max": 0,
                "pct": 100.0 * you.get("total", 0) / total if total else 0.0,
                "bar": total / top,
                "is_self": False,
                "is_party": False,
                "skills": players,
                "classes": classes,
                "buffs": [],
                "session": data,
            })
        return {
            "rows": rows,
            "sections": [{"key": "all", "total": sum(r["total"] for r in rows),
                          "count": len(rows)}],
            "split": False,
            "hidden": 0,
            "total": sum(r["total"] for r in rows),
            "metric": "sessions",
        }

    def _session_full(self, path: str) -> dict | None:
        """Полная сессия с диска, с памятью на одну последнюю.

        Больше одной держать незачем: раскрыта всегда одна строка, а файл
        занимает сотни килобайт.
        """
        if not path:
            return None
        if getattr(self, "_session_cache", (None, None))[0] != path:
            self._session_cache = (path, sessmod.load(path))
        return self._session_cache[1]

    def _session_label(self, data: dict) -> str:
        """Подпись сессии: когда была, сколько длилась, по кому били."""
        start = int(data.get("start", 0))
        when = time.strftime("%d.%m %H:%M", time.localtime(start)) if start else "—"
        dur = int(data.get("duration", 0))
        span = (f"{dur // 3600}:{dur // 60 % 60:02d}:{dur % 60:02d}" if dur >= 3600
                else f"{dur // 60}:{dur % 60:02d}")
        boss = data.get("boss") or ""
        return f"{when} · {span}" + (f" · {boss}" if boss else "")

    def _tick_anim(self) -> None:
        """Догоняем целевые длины рельса и нити.

        Не QPropertyAnimation: цель меняется посреди анимации четыре раза в
        секунду, и перезапуск даёт рывок на каждом обновлении.
        """
        if self._flash > 0:
            self._flash -= 1
            self.update()
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
        full_w, full_h = self.width(), self.height()
        dpr = self.devicePixelRatioF()
        # Рисованная рамка занимает место по краям. Сдвигаем ВСЮ отрисовку
        # внутрь и уменьшаем рабочую ширину, иначе кант ложится поверх
        # правой колонки и подвала.
        inset = self.frame_inset()
        w, h = full_w - 2 * inset, full_h - 2 * inset
        # Раскладка полоски сводки зависит от ширины. Считаем здесь, а не
        # только в resizeEvent: скрытое окно события ресайза не получает.
        self._measure_stats(w)
        if inset:
            p.translate(inset, inset)

        if self.cfg.get("transparent"):
            path = QPainterPath()
            path.addRoundedRect(QRectF(0, 0, w, h), R_WINDOW, R_WINDOW)
            p.fillPath(path, self._bg(L1))
            p.setClipPath(path)
        else:
            p.fillRect(0, 0, w, h, L1)

        # Фактура поля поверх заливки. Замер по картинке: медианная яркость
        # 9.6 %, у белого текста над ней контраст 6.4:1 — порог 4.5 пройден
        # с запасом, поэтому кладём в полную силу, а не приглушённо.
        panel = panel_pixmap() if self.cfg.get("art_panel", True) else None
        if panel is not None:
            p.drawPixmap(QRect(0, 0, w, h), panel)

        self._hit = []
        self._row_rects = []
        snap_ = self.snapshot

        if self.cfg.get("streamer"):
            if inset:
                p.translate(-inset, -inset)
            self._paint_streamer(p, full_w, full_h, snap_, dpr)
            return

        # Заголовок с логотипом занимает свою полосу сверху, а всё
        # остальное живёт ниже. Сдвигаем систему координат один раз, а не
        # правим полтора десятка смещений по всему файлу.
        self._paint_title(p, w, dpr)
        p.translate(0, self.TITLE_H)
        h -= self.TITLE_H

        self._paint_head(p, w, snap_, dpr)
        if self.ACT_H:
            self._paint_actions(p, w, snap_)
        if self.STATS_H:
            self._paint_stats(p, w, snap_, dpr)
        self._paint_colheads(p, w)

        top = self.HEAD_H + self.ACT_H + self.STATS_H + self.COL_H + 1
        bottom = h - self.foot_h - 1
        rows = [r for r in snap_.get("rows", ()) if r["name"] not in NO_OWNER]
        dot = next((r for r in snap_.get("rows", ()) if r["name"] in NO_OWNER), None)

        # Подложка рисуется под таблицей в любом случае — меняется только
        # сила вуали. Вкладка сессий и добычи её тоже получает: там своя
        # картина, но пустое поле выглядит одинаково голым везде.
        self._paint_idle_art(p, w, top, bottom, dpr,
                             busy=bool(rows or dot is not None))

        if not rows and dot is None:
            self._paint_empty(p, w, top, bottom, snap_)
            self._content_h = 0
            self._view_h = max(1, bottom - top)
        else:
            # Список прокручивается: раскрытая добыча — это до 12 строк по
            # 60 px, в окно они не влезают никогда, а раньше просто
            # обрезались по нижней кромке без всякого признака, что дальше
            # что-то есть. Рисуем со сдвигом и режем по области.
            self._view_h = max(1, bottom - top)
            self._clamp_scroll()
            p.save()
            p.setClipRect(QRect(0, top, w, self._view_h))
            y = top - self._scroll
            for i, r in enumerate(rows):
                if y > bottom:
                    break
                if y + self.ROW_H > top:
                    self._paint_row(p, i + 1, r, y, w, dpr)
                    self._row_rects.append((QRect(0, y, w, self.ROW_H), r))
                y += self.ROW_H
                if self.selected == r["name"]:
                    y = self._paint_skills(p, r, y, w, bottom, dpr)
            if dot is not None:
                if y + self.ROW_H > top and y <= bottom:
                    p.fillRect(QRectF(0, snap(y, dpr), w, 1), HAIR)
                    self._paint_row(p, 0, dot, y + 1, w, dpr, muted=True)
                    self._row_rects.append((QRect(0, y + 1, w, self.ROW_H), dot))
                y += self.ROW_H + 1
            p.restore()
            self._content_h = y + self._scroll - top
            self._paint_scrollbar(p, w, top, dpr)

        if self.foot_h:
            self._paint_footer(p, w, h, snap_, dpr)
        # Подсказка рисуется последней: она всплывает над всем остальным.
        self._paint_hint(p, w, h, dpr)
        p.translate(0, -self.TITLE_H)
        if inset:
            p.translate(-inset, -inset)
        if inset:
            self._paint_frame(p, full_w, full_h)
        elif not self.cfg.get("transparent"):
            p.setPen(QPen(EDGE_LIT, 1))
            p.drawLine(0, 0, w - 1, 0)
            p.drawLine(0, 0, 0, h - 1)
            p.setPen(QPen(EDGE_DARK, 1))
            p.drawLine(0, h - 1, w - 1, h - 1)
            p.drawLine(w - 1, 0, w - 1, h - 1)
        if self._flash > 0:
            glow = QColor(ACCENT)
            glow.setAlpha(min(200, self._flash * 18))
            p.setPen(QPen(glow, 2))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(QRectF(1, 1, w - 2, h - 2),
                              R_WINDOW, R_WINDOW)
        self._paint_grip(p, full_w, full_h)

    # -- шапка --------------------------------------------------------------

    def _paint_title(self, p: QPainter, w: int, dpr: float) -> None:
        """Логотип и название над вкладками.

        Зачем это в окне, где каждый пиксель на счету: метр висит поверх
        игры, его скриншотят и кидают в чат, и по картинке должно быть
        видно, чем она снята. Полоса низкая, текста в ней ровно одно слово.
        """
        p.fillRect(QRect(0, 0, w, self.TITLE_H), self._bg(L2, chrome=True))
        hdr = header_pixmap() if self.cfg.get("art_panel", True) else None
        if hdr is not None:
            p.drawPixmap(QRect(0, 0, w, self.TITLE_H), hdr)

        size = self.TITLE_ICON
        y = (self.TITLE_H - size) // 2
        fm = QFontMetrics(self.f_title)
        base = (self.TITLE_H + fm.ascent() - fm.descent()) // 2

        # Значок и название стоят у левого края, как в заголовке любого
        # окна. Центрирование пробовали — по центру полоса начинает спорить
        # с вкладками под ней за внимание.
        x = PAD

        icon = art_pixmap("appicon")
        if icon is not None:
            scaled = icon.scaled(int(size * dpr), int(size * dpr),
                                 Qt.KeepAspectRatio, Qt.SmoothTransformation)
            scaled.setDevicePixelRatio(dpr)
            p.drawPixmap(x, y, scaled)
        else:
            # Пака нет — рисуем крыло вектором, чтобы полоса не пустовала.
            self._shape(p, "wing", QRect(x, y, size, size), GOLD, size / 19.0)
        x += size + GAP
        self._txt(p, x, base, APP_NAME, INK, self.f_title)

        # Своей зоны перетаскивания полосе не нужно: окно и так тянется за
        # любое место, где нет кнопки, а лишняя запись в списке зон
        # перехватывала клики по вкладкам — они лежат ровно под ней.
        p.fillRect(QRectF(0, self.TITLE_H - 1, w, 1), RULE)

    def action_toggle_streamer(self) -> None:
        """Включить или выключить режим стримера."""
        on = not self.cfg.get("streamer")
        self.cfg["streamer"] = on
        if on:
            # Клик-сквозь пришлось бы отключить всё равно: в этом режиме
            # единственный способ выйти — кнопка в углу окна.
            self._pre_stream = {
                "click_through": self.cfg.get("click_through", False),
                "transparent": self.cfg.get("transparent", False),
            }
            self.cfg["click_through"] = False
            self.cfg["transparent"] = False
        else:
            self.cfg.update(getattr(self, "_pre_stream", {}) or {})
            self._pre_stream = {}
        self.selected = ""
        self.apply_appearance()
        self._refresh()

    def _chroma(self) -> QColor:
        colour = QColor(self.cfg.get("chroma_color") or "#00B140")
        return colour if colour.isValid() else QColor("#00B140")

    #: Высота строки в режиме стримера: плотнее обычной, потому что вокруг
    #: неё нет ни шапки, ни подвала, и место тратить не на что.
    STREAM_ROW_GAP = 2

    def _paint_streamer(self, p: QPainter, w: int, h: int, snap_: dict,
                        dpr: float) -> None:
        """Только полосы урона на однотонном фоне.

        Фон вырезается хромакеем в OBS, поэтому под строками не должно
        быть ни рамки, ни фактуры, ни подложки — иначе всё это окажется
        в кадре. По той же причине текст рисуется с тенью: на зелёном
        белые буквы без обводки расплываются после кеинга.
        """
        p.fillRect(QRect(0, 0, w, h), self._chroma())

        rows = [r for r in snap_.get("rows", ()) if r["name"] not in NO_OWNER]
        step = self.ROW_H + self.STREAM_ROW_GAP
        # Кнопка выхода: маленькая, в правом верхнем углу. Без неё из
        # режима было бы не выбраться — ни вкладок, ни панели тут нет.
        size = max(16, self.H)
        exit_rect = QRect(w - size - 4, 4, size, size)
        self._hit.append(("btn:stream_off", exit_rect))

        top = 0 if not rows else 2
        y = top
        limit = max(1, (h - top) // step)
        for i, r in enumerate(rows[:limit]):
            # Тёмная подложка под КАЖДОЙ строкой. Без неё цифры справа
            # оказываются прямо на зелёном: после кеинга они частично
            # выедаются по краям и читаются плохо. С подложкой в кадр
            # попадают аккуратные строки, а всё вокруг остаётся прозрачным.
            p.fillRect(QRect(0, y, w, self.ROW_H), self._bg(L1))
            self._paint_row(p, i + 1, r, y, w, dpr)
            self._row_rects.append((QRect(0, y, w, self.ROW_H), r))
            y += step

        if not rows:
            # Пустой хромакей выглядит как сломавшаяся программа. Одна
            # строка подписи — и понятно, что метр жив и ждёт боя.
            p.fillRect(QRect(0, 0, w, self.ROW_H), self._bg(L1))
            p.setFont(self.f_small)
            p.setPen(INK3)
            p.drawText(QRect(PAD, 0, w - 2 * PAD - size, self.ROW_H),
                       Qt.AlignLeft | Qt.AlignVCenter,
                       f"{APP_NAME} — waiting for combat")

        hot = self._hot == "btn:stream_off"
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 150 if hot else 90))
        p.drawRoundedRect(exit_rect, 3, 3)
        pen = QPen(QColor(255, 255, 255, 235 if hot else 170), 1.6)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        m = size // 3
        p.drawLine(exit_rect.left() + m, exit_rect.top() + m,
                   exit_rect.right() - m, exit_rect.bottom() - m)
        p.drawLine(exit_rect.right() - m, exit_rect.top() + m,
                   exit_rect.left() + m, exit_rect.bottom() - m)

    def _tab_edge(self) -> int:
        """Ширина золотого завитка на торце плашки вкладки, в пикселях окна.

        Замер по tab.png: плотное золото идёт от края до x=88 при торце в
        128 и высоте картинки 256, и правый торец симметричен левому.
        Дальше от завитка остаются только тонкие рейки канта, на них
        содержимое залезать уже можно. За этот отступ отодвигаются и
        значок слева, и подпись справа: без него значок наезжал на
        завиток, а подпись упиралась во второй. Три пикселя сверху —
        просвет: ровно по границе завитка подпись читалась приклеенной.
        """
        art = art_pixmap("tab") if self.cfg.get("art_frame", True) else None
        if art is None:
            return 7
        cap_src = int(assets.manifest().get("tab_cap", 128))
        curl = cap_src * 0.69 * (self.HEAD_H - 6) / art.height()
        return max(7, round(curl) + 3)

    def _paint_head(self, p: QPainter, w: int, snap_: dict,
                    dpr: float = 1.0) -> None:
        chrome_h = self.HEAD_H + self.ACT_H
        p.fillRect(QRect(0, 0, w, chrome_h), self._bg(L2, chrome=True))
        # Фактура шапки: растягиваем, а не повторяем. Замер по картинке —
        # отклонение вертикальных срезов медианой 12 из 255, то есть она и
        # так почти однородна, а растяжение убирает остаток.
        hdr = header_pixmap() if self.cfg.get("art_panel", True) else None
        if hdr is not None:
            p.drawPixmap(QRect(0, 0, w, chrome_h), hdr)
        fm = QFontMetrics(self.f_tab)
        base = (self.HEAD_H + fm.ascent() - fm.descent()) // 2
        cur = self.cfg.get("metric", "damage")

        # Справа только окно: меню и закрытие. Всё остальное уехало на
        # крупную панель действий ниже — там его видно.
        chrome_w = PAD + self.BTN * 2 + 2
        avail = w - chrome_w - PAD

        # Вкладка = значок + подпись. Если подписи не влезают, остаются одни
        # значки: четыре разные фигуры различимы и без слов, а обрезанный
        # текст хуже отсутствующего.
        icon_w = self.TAB_ICON
        gaps = 6
        edge = self._tab_edge()
        full = sum(fm.horizontalAdvance(l) + icon_w + gaps + 2 * edge
                   for _k, l in METRIC_TABS)
        show_text = full <= avail

        # Картинка значка меньше отведённой ячейки: спрайт заполняет свой
        # квадрат целиком, и без запаса его углы вылезали за кант плашки.
        art_w = min(icon_w, self.HEAD_H - 6 - 8)

        x = PAD
        for key, label in METRIC_TABS:
            tw = fm.horizontalAdvance(label) if show_text else 0
            bw = icon_w + (gaps + tw if show_text else 0) + 2 * edge
            if x + bw > PAD + avail:
                # Полоса кончилась. Рисовать поверх кнопок нельзя: их
                # прямоугольники кладутся в список зон ПОСЛЕ вкладок, и
                # клик по крестику попадал бы во вкладку под ним.
                break
            rect = QRect(x, 3, bw, self.HEAD_H - 6)
            self._hit.append((f"metric:{key}", rect))
            active = key == cur
            hot = self._hot == f"metric:{key}"
            self._plate(p, rect, active=active, hot=hot)
            ink = INK if active else (INK2 if hot else INK3)
            gi = QRect(rect.x() + edge, rect.y(), icon_w, rect.height())
            pm = tab_icon(key, art_w, dpr, active)
            if pm is not None:
                # Наведение приподнимает погасший значок, но не до полного
                # цвета: цвет остаётся признаком выбранной вкладки.
                p.setOpacity(1.0 if active else (0.9 if hot else 0.66))
                p.drawPixmap(gi.x() + (icon_w - art_w) // 2,
                             gi.y() + (gi.height() - art_w) // 2, pm)
                p.setOpacity(1.0)
            else:
                # Пака нет — рисуем вектором. Масштаб от ячейки, а не от
                # rect: прямоугольник вкладки выше значка, а размер значков
                # настраивается.
                self._shape(p, TAB_GLYPH[key], gi, GOLD if active else ink,
                            art_w / 19.0)
            if show_text:
                self._txt(p, gi.right() + gaps, base, label, ink, self.f_tab)
            x += bw + 4

        size = self.BTN
        top = (self.HEAD_H - size) // 2
        bx = w - PAD - size
        for name, _tip in reversed(TOOLBAR_RIGHT):
            self._button(p, QRect(bx, top, size, size), name, snap_)
            bx -= size + 2

    # -- прокрутка ----------------------------------------------------------

    def _max_scroll(self) -> int:
        return max(0, self._content_h - self._view_h)

    def _clamp_scroll(self) -> None:
        self._scroll = max(0, min(self._scroll, self._max_scroll()))

    def wheelEvent(self, e) -> None:
        if self._max_scroll() <= 0:
            e.ignore()
            return
        # Шаг в строках, а не в пикселях: строка добычи втрое выше строки
        # разбора, и одинаковый пиксельный шаг ощущается по-разному.
        step = self.LOOT_H if self.cfg.get("metric") == "loot" else self.ROW_H
        delta = e.angleDelta().y()
        self._scroll -= (delta / 120.0) * step
        self._scroll = int(self._scroll)
        self._clamp_scroll()
        self.update()
        e.accept()

    def _paint_scrollbar(self, p: QPainter, w: int, top: int, dpr: float) -> None:
        """Тонкая полоса справа. Без неё не видно, что список длиннее окна."""
        span = self._max_scroll()
        if span <= 0:
            return
        track_h = self._view_h
        thumb_h = max(24, int(track_h * self._view_h / max(1, self._content_h)))
        pos = int((track_h - thumb_h) * self._scroll / span)
        x = w - 4
        p.fillRect(QRectF(x, top, 2, track_h), TRACK)
        p.fillRect(QRectF(x, top + pos, 2, thumb_h), INK_MUTE)

    def _plate(self, p: QPainter, rect: QRect, active: bool, hot: bool) -> None:
        """Подложка вкладки: прямоугольник со скруглением.

        Активная — светлее фона и с полоской снизу; наведённая — чуть
        подсвечена; остальные без подложки вовсе, иначе четыре плашки в ряд
        превращают шапку в кашу.
        """
        art = art_pixmap("tab") if self.cfg.get("art_frame", True) else None
        if active and art is not None:
            # Торцы в натуральную величину, середина растягивается: весь
            # орнамент сидит в торцах, ровное поле между ними тянется без следа.
            cap_src = int(assets.manifest().get("tab_cap", 128))
            sw, sh = art.width(), art.height()
            cap = max(6, min(rect.width() // 2 - 1, int(cap_src * rect.height() / sh)))
            mid = rect.width() - 2 * cap
            p.drawPixmap(QRect(rect.x(), rect.y(), cap, rect.height()),
                         art, QRect(0, 0, cap_src, sh))
            if mid > 0:
                p.drawPixmap(QRect(rect.x() + cap, rect.y(), mid, rect.height()),
                             art, QRect(cap_src, 0, sw - 2 * cap_src, sh))
            p.drawPixmap(QRect(rect.right() - cap + 1, rect.y(), cap, rect.height()),
                         art, QRect(sw - cap_src, 0, cap_src, sh))
            return
        if active:
            p.setPen(Qt.NoPen)
            p.setBrush(SORTBG)
            p.drawRoundedRect(rect, R_CHIP, R_CHIP)
            p.fillRect(QRectF(rect.x() + 6, rect.bottom() - 1,
                              rect.width() - 12, 2), ACCENT)
        elif hot:
            p.setPen(Qt.NoPen)
            p.setBrush(HOVER)
            p.drawRoundedRect(rect, R_CHIP, R_CHIP)
        p.setBrush(Qt.NoBrush)

    def _paint_actions(self, p: QPainter, w: int, snap_: dict) -> None:
        """Крупные квадратные кнопки по центру своей полосы."""
        size = self.ACT
        gap = max(6, size // 6)
        total = len(ACTIONS) * size + (len(ACTIONS) - 1) * gap
        x = max(PAD, (w - total) // 2)
        y = self.HEAD_H + (self.ACT_H - size) // 2
        for name, tip in ACTIONS:
            rect = QRect(x, y, size, size)
            self._hit.append((f"btn:{name}", rect))
            hot = self._hot == f"btn:{name}"
            paused = bool(snap_.get("paused"))
            # Включённый фильтр по боссу — такое же состояние, как пауза:
            # человек должен видеть, что цифры в таблице урезаны, не
            # открывая меню и не сравнивая их с памятью.
            on = (name == "play" and paused) or (
                name == "boss" and bool(self.cfg.get("boss_only")))
            self._texture(p, rect, hot=hot, lit=on)
            # Активное состояние — единственное, у которого свой цвет:
            # акцент говорит «сейчас включено», и в общем золоте он бы пропал.
            colour = ACCENT if on else (GOLD if hot else GOLD_DIM)
            glyph = QRect(rect.x(), rect.y(), rect.width(), rect.height())
            if name in ("settings", "shot", "boss", "about", "stream"):
                self._shape(p, name, glyph, colour, size / 27.5)
            else:
                self._big_action(p, name, glyph, colour, paused, size / 27.5)
            x += size + gap
        self._paint_wings(p, w)
        p.fillRect(QRectF(0, self.HEAD_H + self.ACT_H - 1, w, 1), RULE)

    def _paint_wings(self, p: QPainter, w: int) -> None:
        """Угловая накладка по краям полосы кнопок.

        Кнопки стоят по центру, и по бокам от них пустое место — накладка
        занимает его, а не спорит со вкладками или с рамкой, у которой в
        углах свой орнамент.
        """
        art = art_pixmap("wings") if self.cfg.get("art_frame", True) else None
        if art is None:
            return
        band = self.ACT_H
        if band < 24:
            return
        h = int(band * 0.86)
        aw = max(1, int(art.width() * h / art.height()))
        free = (w - (len(ACTIONS) * self.ACT + (len(ACTIONS) - 1) * max(6, self.ACT // 6))) // 2
        if aw > free - PAD:                  # не влезает — не рисуем вовсе
            return
        y = self.HEAD_H + (band - h) // 2
        p.drawPixmap(QRect(PAD, y, aw, h), art)
        p.save()
        p.translate(w - PAD, 0)
        p.scale(-1, 1)                       # зеркалим для правого края
        p.drawPixmap(QRect(0, y, aw, h), art)
        p.restore()

    def _texture(self, p: QPainter, rect: QRect, hot: bool, lit: bool) -> None:
        """Лицо кнопки. Картинка из пака, если она есть, иначе рисуем сами.

        Состояния НЕ отдельные картинки: наведение и нажатие выводятся из
        обычного подсветкой и затемнением. Три независимые генерации не
        совпали бы по геометрии, и кнопка дёргалась бы под курсором.
        """
        art = art_pixmap("button") if self.cfg.get("art_frame", True) else None
        if art is not None:
            p.drawPixmap(rect, art)
            if hot:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(255, 255, 255, 26))
                p.drawRoundedRect(rect, R_BUTTON + 2, R_BUTTON + 2)
                p.setBrush(Qt.NoBrush)
            if lit:
                p.setBrush(Qt.NoBrush)
                p.setPen(QPen(ACCENT, 2))
                p.drawRoundedRect(QRectF(rect).adjusted(1, 1, -1, -1),
                                  R_BUTTON + 2, R_BUTTON + 2)
            return

        grad = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        top = QColor(255, 255, 255, 30 if hot else 18)
        bottom = QColor(0, 0, 0, 46 if hot else 60)
        grad.setColorAt(0.0, top)
        grad.setColorAt(0.45, QColor(255, 255, 255, 6 if hot else 3))
        grad.setColorAt(1.0, bottom)
        p.setPen(Qt.NoPen)
        p.setBrush(SORTBG if hot else TRACK)
        p.drawRoundedRect(rect, R_BUTTON + 2, R_BUTTON + 2)
        p.setBrush(grad)
        p.drawRoundedRect(rect, R_BUTTON + 2, R_BUTTON + 2)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(ACCENT if lit else (EDGE_LIT if hot else EDGE_DARK), 1))
        p.drawRoundedRect(QRectF(rect).adjusted(0.5, 0.5, -0.5, -0.5),
                          R_BUTTON + 2, R_BUTTON + 2)
        p.setPen(QPen(QColor(255, 255, 255, 34 if hot else 20), 1))
        p.drawLine(rect.x() + 5, rect.y() + 1, rect.right() - 5, rect.y() + 1)

    def _shape(self, p: QPainter, name: str, rect: QRect, colour: QColor,
               k: float) -> None:
        """Одна фигура набора. Все они заданы в клетке ±10, k — её масштаб.

        Масштаб приходит снаружи, а не считается из rect: у вкладки
        прямоугольник выше значка, а размер значков настраивается в
        настройках. Заливка — вертикальный градиент: ровное пятно на
        тёмном фоне выглядит наклейкой, а перепад читается как металл, из
        которого сделана рамка вокруг.
        """
        cx, cy = rect.center().x(), rect.center().y()

        def pt(dx, dy):
            return QPoint(int(round(cx + dx * k)), int(round(cy + dy * k)))

        def pf(dx, dy):
            return QPointF(cx + dx * k, cy + dy * k)

        def rf(x0, y0, x1, y1):
            return QRectF(pf(x0, y0), pf(x1, y1))

        def body():
            # Градиент растягивается на КЛЕТКУ значка, а не на rect: у
            # вкладки прямоугольник почти по размеру фигуры, у кнопки в
            # полтора раза больше, и от общего rect перепад на вкладке
            # выходил заметно круче, чем на кнопке рядом.
            g = QLinearGradient(0, cy - 10 * k, 0, cy + 10 * k)
            g.setColorAt(0.0, QColor(colour).lighter(124))
            g.setColorAt(1.0, QColor(colour).darker(134))
            return g

        dark = QColor(6, 8, 12, 200)
        p.setPen(Qt.NoPen)
        p.setBrush(body())

        if name == "sword":
            # Наклон 22°, а не 45°: под 45° клинок с гардой читается как «X».
            p.save()
            p.translate(cx, cy)
            p.rotate(22)
            p.translate(-cx, -cy)
            p.drawPolygon(QPolygon([pt(0, -10.4), pt(2.9, -7.0), pt(2.9, 2.2),
                                    pt(-2.9, 2.2), pt(-2.9, -7.0)]))
            p.drawRoundedRect(rf(-8.6, 2.2, 8.6, 4.8), 1.1 * k, 1.1 * k)
            p.drawRect(rf(-1.7, 4.8, 1.7, 8.0))
            p.drawEllipse(rf(-2.8, 7.6, 2.8, 10.6))
            p.setBrush(QColor(255, 255, 255, 95))      # блик по грани клинка
            p.drawRect(rf(-0.8, -8.0, 0.5, 1.4))
            p.restore()
        elif name == "cross":
            # Крест с расширяющимися концами: прямой медицинский выглядит
            # аптечным, расширенный читается как знак жизни в жанре.
            arm, w0, w1 = 9.8, 2.5, 4.6
            pts = [(-w1, -arm), (w1, -arm), (w0, -w0), (arm, -w1), (arm, w1),
                   (w0, w0), (w1, arm), (-w1, arm), (-w0, w0), (-arm, w1),
                   (-arm, -w1), (-w0, -w0)]
            path = QPainterPath()
            path.moveTo(pf(*pts[0]))
            for q in pts[1:]:
                path.lineTo(pf(*q))
            path.closeSubpath()
            p.drawPath(path)
            p.setBrush(QColor(255, 255, 255, 75))
            p.drawRect(rf(-1.1, -8.4, 0.6, -2.4))
        elif name == "shield":
            # Каплевидный щит с умбоном. Вертикальной прорези нет: она
            # читалась как буква «I».
            path = QPainterPath()
            path.moveTo(pf(0, -9.6))
            path.lineTo(pf(8.4, -6.4))
            path.cubicTo(pf(8.4, 2.4), pf(5.2, 7.0), pf(0, 10.0))
            path.cubicTo(pf(-5.2, 7.0), pf(-8.4, 2.4), pf(-8.4, -6.4))
            path.closeSubpath()
            p.drawPath(path)
            p.setBrush(dark)
            p.drawEllipse(rf(-2.7, -3.6, 2.7, 1.8))
        elif name == "chest":
            # Сундук с плоской крышкой. Полукруглая читалась как почтовый
            # ящик, а вертикальные оковки на 27 px дробили силуэт до фонаря.
            p.drawRoundedRect(rf(-9.4, -0.8, 9.4, 8.4), 1.2 * k, 1.2 * k)
            path = QPainterPath()
            path.moveTo(pf(-9.4, -0.4))
            path.arcTo(rf(-9.4, -6.0, 9.4, 5.2), 180, -180)
            path.closeSubpath()
            p.drawPath(path)
            p.setBrush(dark)
            p.drawRect(rf(-9.4, -1.3, 9.4, 0.3))
            p.setBrush(body())
            p.drawRoundedRect(rf(-2.6, -2.6, 2.6, 3.4), 0.8 * k, 0.8 * k)
            p.setBrush(dark)
            p.drawEllipse(rf(-1.1, -1.0, 1.1, 1.2))
        elif name == "stream":
            # Экран и две дуги вещания. Камера уже занята снимком окна,
            # и две камеры рядом путали бы.
            p.drawRoundedRect(rf(-10.4, -7.6, 10.4, 5.6), 1.6 * k, 1.6 * k)
            p.setBrush(dark)
            p.drawRoundedRect(rf(-8.4, -5.6, 8.4, 3.6), 1.0 * k, 1.0 * k)
            p.setBrush(body())
            p.drawRect(rf(-4.6, 5.6, 4.6, 7.2))
            p.drawRoundedRect(rf(-7.4, 7.2, 7.4, 9.4), 1.0 * k, 1.0 * k)
            pen = QPen(colour, max(1.0, 1.7 * k))
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            for r0 in (3.2, 5.8):
                p.drawArc(rf(-r0, -r0 - 1.0, r0, r0 - 1.0).toRect(), 40 * 16, 100 * 16)
            p.setPen(Qt.NoPen)
            p.setBrush(body())
        elif name == "about":
            # Кольцо и буква «i». Знак вопроса читался как «помощь по
            # игре», а тут именно сведения о программе.
            r, th = 10.2, 2.1
            path = QPainterPath()
            path.addEllipse(rf(-r, -r, r, r))
            path.addEllipse(rf(-r + th, -r + th, r - th, r - th))
            p.drawPath(path)
            p.drawEllipse(rf(-1.5, -6.2, 1.5, -3.2))
            p.drawRoundedRect(rf(-1.5, -1.4, 1.5, 6.6), 0.8 * k, 0.8 * k)
        elif name == "boss":
            # Мишень: два кольца и точка. Череп читался как «убить», а нам
            # нужно «считать только эту цель».
            for r, th in ((9.6, 2.2), (5.2, 2.0)):
                path = QPainterPath()
                path.addEllipse(rf(-r, -r, r, r))
                path.addEllipse(rf(-r + th, -r + th, r - th, r - th))
                p.drawPath(path)
            p.drawEllipse(rf(-1.9, -1.9, 1.9, 1.9))
            for dx, dy in ((0, -12.6), (0, 12.6), (-12.6, 0), (12.6, 0)):
                p.drawRect(rf(dx - 0.9 if dx else -0.9, dy - 2.4 if dy else -0.9,
                              dx + 0.9 if dx else 0.9, dy + 2.4 if dy else 0.9))
        elif name == "wing":
            # Три пера веером и импульс под ними: тот же смысл, что у
            # логотипа, но в силуэте, который читается на 14 пикселях.
            for i, (dx, dy, ln) in enumerate(((-1.5, -8.4, 9.2),
                                              (-4.0, -5.0, 11.4),
                                              (-6.2, -1.4, 12.6))):
                path = QPainterPath()
                path.moveTo(pf(dx, dy))
                path.quadTo(pf(dx + ln * 0.55, dy + 1.6),
                            pf(dx + ln, dy + 5.0))
                path.quadTo(pf(dx + ln * 0.5, dy + 4.2),
                            pf(dx, dy + 3.4))
                path.closeSubpath()
                p.drawPath(path)
            p.setBrush(QColor(111, 216, 255))
            p.drawRect(rf(-7.0, 6.6, 6.4, 8.0))
        elif name == "scroll":
            # Свиток: журнал прошлых заходов. Взят вместо часов и календаря
            # намеренно — часы читаются как «таймер», календарь как «дата»,
            # а нужен именно список записей.
            p.drawRoundedRect(rf(-7.2, -8.6, 7.2, 8.6), 1.4 * k, 1.4 * k)
            p.setBrush(QColor(colour).darker(150))
            for i in range(3):                       # строки записей
                top = -4.6 + i * 3.6
                p.drawRoundedRect(rf(-4.4, top, 4.4, top + 1.5), 0.7 * k, 0.7 * k)
            p.setBrush(body())
            # Валики сверху и снизу: без них прямоугольник читается как лист.
            p.drawRoundedRect(rf(-9.2, -10.4, 9.2, -7.8), 1.3 * k, 1.3 * k)
            p.drawRoundedRect(rf(-9.2, 7.8, 9.2, 10.4), 1.3 * k, 1.3 * k)
        elif name == "play":
            path = QPainterPath()
            path.moveTo(pf(-6.6, -10.2))
            path.lineTo(pf(10.0, 0))
            path.lineTo(pf(-6.6, 10.2))
            path.closeSubpath()
            p.drawPath(path)
        elif name == "pause":
            p.drawRoundedRect(rf(-7.6, -9.2, -2.4, 9.2), 1.4 * k, 1.4 * k)
            p.drawRoundedRect(rf(2.4, -9.2, 7.6, 9.2), 1.4 * k, 1.4 * k)
        elif name == "clear":
            # Кольцо на 288° сплошной толщины плюс настоящий треугольный
            # наконечник. Дуга на 280° с двумя чёрточками читалась как «C».
            r, th, gap = 7.7, 3.3, 118.0
            outer = rf(-r - th / 2, -r - th / 2, r + th / 2, r + th / 2)
            inner = rf(-r + th / 2, -r + th / 2, r - th / 2, r - th / 2)
            path = QPainterPath()
            path.arcMoveTo(outer, gap)
            path.arcTo(outer, gap, -288)
            path.arcTo(inner, gap - 288, 288)
            path.closeSubpath()
            p.drawPath(path)
            a = math.radians(gap)
            hx, hy = math.cos(a) * r, -math.sin(a) * r
            p.drawPolygon(QPolygon([pt(hx - 4.0, hy - 1.4),
                                    pt(hx + 4.0, hy - 1.4),
                                    pt(hx, hy + 5.0)]))
        elif name == "chat":
            # Не «две карточки», а речевое облако: кнопка копирует сводку
            # В ИГРОВОЙ ЧАТ, и облако говорит об этом прямо.
            p.drawRoundedRect(rf(-9.6, -8.4, 9.6, 4.6), 2.4 * k, 2.4 * k)
            p.drawPolygon(QPolygon([pt(-5.6, 3.4), pt(-1.0, 3.4),
                                    pt(-6.2, 9.6)]))
            p.setBrush(dark)
            for i, wd in enumerate((6.4, 4.4)):
                p.drawRoundedRect(rf(-wd, -5.4 + i * 3.5, wd, -3.7 + i * 3.5),
                                  0.7 * k, 0.7 * k)
        elif name == "shot":
            p.drawRoundedRect(rf(-4.6, -9.0, 1.0, -6.8), 0.9 * k, 0.9 * k)
            p.drawRoundedRect(rf(-9.4, -6.9, 9.4, 8.0), 2.0 * k, 2.0 * k)
            p.setBrush(dark)
            p.drawEllipse(rf(-5.2, -3.9, 5.2, 6.5))
            p.setBrush(body())
            p.drawEllipse(rf(-2.9, -1.6, 2.9, 4.2))
            p.setBrush(QColor(255, 255, 255, 130))
            p.drawEllipse(rf(-2.1, -1.0, -0.5, 0.6))
        elif name == "settings":
            # Шесть ПРЯМОУГОЛЬНЫХ зубцов вместо восьми острых: прежняя
            # фигура с радиусами 8.6/6.2 давала мелкую насечку и читалась
            # как солнце, а не как шестерня.
            teeth, ro, ri, half = 6, 10.0, 6.4, 0.34
            step = 2 * math.pi / teeth
            path = QPainterPath()
            for i in range(teeth):
                a0 = step * i
                for j, (da, r) in enumerate(((-half, ri), (-half, ro),
                                             (half, ro), (half, ri))):
                    q = pf(math.cos(a0 + da) * r, math.sin(a0 + da) * r)
                    path.moveTo(q) if (i == 0 and j == 0) else path.lineTo(q)
                path.arcTo(rf(-ri, -ri, ri, ri),
                           -math.degrees(a0 + half),
                           -math.degrees(step - 2 * half))
            path.closeSubpath()
            p.drawPath(path)
            p.setBrush(dark)
            p.drawEllipse(rf(-3.5, -3.5, 3.5, 3.5))
        p.setBrush(Qt.NoBrush)

    def _big_action(self, p: QPainter, name: str, rect: QRect, colour: QColor,
                    paused: bool, k: float) -> None:
        """Пуск-пауза, сброс и копирование в чат — крупным вектором."""
        shape = {"play": "play" if paused else "pause", "copy": "chat"}.get(
            name, name)
        self._shape(p, shape, rect, colour, k)

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
        self._icon(p, name, rect, colour, bool(snap_.get("paused")))

    def _icon(self, p: QPainter, name: str, rect: QRect, colour: QColor,
              paused: bool = False) -> None:
        """Значки окна — примитивами: символов вроде ⚙ нет во многих шрифтах."""
        if name in TAB_GLYPH.values() or name in ("settings", "shot", "clear"):
            self._shape(p, name, rect, colour, rect.height() / 24.0)
            return
        if name in ("play", "copy"):
            self._big_action(p, name, rect, colour, paused,
                             rect.height() / 24.0)
            return
        cx, cy = rect.center().x() + 1, rect.center().y() + 1
        p.setPen(QPen(colour, 1.5))
        p.setBrush(Qt.NoBrush)
        if name == "menu":
            for dy in (-4, 0, 4):
                p.drawLine(cx - 6, cy + dy, cx + 6, cy + dy)
        elif name == "close":
            p.drawLine(cx - 5, cy - 5, cx + 5, cy + 5)
            p.drawLine(cx + 5, cy - 5, cx - 5, cy + 5)

    def _paint_stats(self, p: QPainter, w: int, snap_: dict, dpr: float) -> None:
        """Сводка за сессию под кнопками: опыт, кинара, АП, слава, убито.

        Раньше они делили подвал с таймером и итогом и вытеснялись оттуда
        при узком окне: в футере стоит `slots[:2]`, и третья величина просто
        не помещалась. Здесь у них фиксированное место, которое ничем не
        занимают.
        """
        top = self.HEAD_H + self.ACT_H
        p.fillRect(QRect(0, top, w, self.STATS_H), self._bg(L2, chrome=True))
        loot = snap_.get("loot", {})
        rows = tuple((key, label, loot.get(src, 0)) for key, label, src in STATS_ROWS)
        cols = getattr(self, "_stats_cols_now", self._stats_cols(w))
        per_col = -(-len(rows) // cols)          # округление вверх
        fm = QFontMetrics(self.f_stat)
        icon = self.STATS_ICON
        row_h = self.STATS_H // per_col
        col_w = (w - PAD) // cols
        for i, (key, label, value) in enumerate(rows):
            # Порядок чтения слева направо, сверху вниз — тот же, в котором
            # величины перечислены, независимо от числа столбцов.
            col, line = i % cols, i // cols
            y = top + line * row_h
            x0 = PAD + col * col_w
            base = y + (row_h + fm.ascent() - fm.descent()) // 2
            pm = ui_icon(key, icon, dpr)
            x = x0
            if pm is not None:
                p.drawPixmap(x, y + (row_h - icon) // 2, pm)
            else:
                # Пака нет — рисуем кружок, чтобы полоска не разъезжалась.
                p.setPen(QPen(INK_MUTE, 1))
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(QRect(x + 2, y + (row_h - icon) // 2 + 2,
                                    icon - 4, icon - 4))
            x += icon + GAP
            if getattr(self, "_stats_labels", True):
                self._txt(p, x, base, label, INK2, self.f_stat_cap)
            mant, suf = fmt_ui(value)
            right = x0 + col_w - (PAD if col == cols - 1 else GROUP)
            if suf:
                right = self._txt_right(p, right, base, suf, INK2,
                                        self.f_stat_cap)
            self._txt_right(p, right, base, mant, INK if value else INK_MUTE,
                            self.f_stat)
        p.fillRect(QRectF(0, top + self.STATS_H - 1, w, 1), RULE)

    def _measure_stats(self, w: int) -> None:
        """Высота полоски сводки зависит от того, в сколько столбцов она легла.

        Считается от ширины окна, поэтому пересчитывается при каждом
        изменении размера, а не один раз вместе со шрифтами.
        """
        # Ширина не изменилась — считать нечего.
        if getattr(self, "_stats_w", None) == w:
            return
        self._stats_w = w
        row_h = self.ICON + 6
        wide = self._stats_cols(max(1, w))
        # Высота окна тоже голосует: если после сводки таблице остаётся
        # меньше четырёх строк, уплотняем её в больше столбцов. Обвязка и так
        # съедает половину окна, и сводка не должна доедать остаток.
        other = (self.HEAD_H + self.ACT_H + self.COL_H + 1 + 1
                 + self.foot_h + 2 * self.frame_inset())
        free = max(0, self.height() - other)
        min_cell = self._stats_min_cell()
        for cols in range(wide, min(len(STATS_ROWS), 3) + 1):
            lines = -(-len(STATS_ROWS) // cols)
            if cols > 1 and (w - PAD) // cols < min_cell:
                # Дальше уплотнять нельзя: число полезет на иконку.
                cols -= 1
                lines = -(-len(STATS_ROWS) // cols)
                break
            if free - row_h * lines >= self.MIN_TABLE_ROWS * self.ROW_H:
                break
        # Подписи рисуем, только если они реально влезают в ячейку. Раньше
        # проверки не было вовсе, и при трёх столбцах в 400 px «XP» ложился
        # поверх «123.5k», а «Glory Points» обрезался на середине.
        self._stats_labels = self._stats_fits(w, cols)
        self._stats_cols_now = cols
        self.STATS_H = row_h * lines

    def _stats_cols(self, w: int) -> int:
        """Сколько столбцов у полоски сводки.

        Пять величин в один столбец — это 215 px высоты, треть окна. По
        ширине место есть почти всегда, поэтому раскладываем в два столбца,
        как только они помещаются: строка сводки это значок, подпись и
        число, и вдвое уже она читается так же.
        """
        if len(STATS_ROWS) < 3:
            return 1
        # Ширину ячейки меряем по самой длинной подписи и по правдоподобному
        # числу, а не берём на глаз: при мелком шрифте два столбца влезают
        # заметно раньше, при крупном — позже.
        cap = QFontMetrics(self.f_stat_cap)
        num = QFontMetrics(self.f_stat)
        label_w = max(cap.horizontalAdvance(lbl) for _k, lbl, _s in STATS_ROWS)
        need = (self.STATS_ICON + GAP + label_w + GROUP
                + num.horizontalAdvance("888,8") + cap.horizontalAdvance("M"))
        self._stats_need = need
        return 2 if w >= need * 2 + PAD * 2 + GROUP else 1

    def _stats_fits(self, w: int, cols: int) -> bool:
        """Влезает ли в ячейку СТРОКА С ПОДПИСЬЮ при таком числе столбцов.

        Проверка ширины была только для двух столбцов, а на три раскладка
        переходила по одной высоте. При 400 px ячейка выходила 130 px, и
        подписи ложились поверх чисел: «XP» на «123.5k», «Glory Points»
        обрезался на середине. Если не влезает — полоска рисуется без
        подписей: значок и число узнаются и без слов, а наложение нет.
        """
        if not getattr(self, "_stats_need", 0):
            self._stats_cols(w)
        return (w - PAD) // max(1, cols) >= self._stats_need

    def _stats_min_cell(self) -> int:
        """Ширина ячейки, ниже которой число ляжет на иконку.

        Подпись можно спрятать, а иконку и число — нет: при крупных
        иконках (64 px) и трёх столбцах число печаталось прямо поверх
        значка, и прочитать было нельзя ни то, ни другое.
        """
        num = QFontMetrics(self.f_stat)
        cap = QFontMetrics(self.f_stat_cap)
        return (self.STATS_ICON + GAP + num.horizontalAdvance("888,8")
                + cap.horizontalAdvance("M") + GAP)

    def _paint_colheads(self, p: QPainter, w: int) -> None:
        top = self.HEAD_H + self.ACT_H + self.STATS_H
        p.fillRect(QRect(0, top, w, self.COL_H), self._bg(L2, chrome=True))
        fm = QFontMetrics(self.f_caps)
        base = top + fm.ascent() + 3
        caps = CAPTIONS.get(self.cfg.get("metric", "damage"), CAPTIONS["damage"])
        p.setFont(self.f_caps)
        p.setPen(INK3)
        p.drawText(PAD + self.ICON + GAP + 4, base, "#")
        p.drawText(PAD + self.ICON + GAP + 24, base, "PLAYER")
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

        # Подсветка своей строки: рисованная полоса, если она есть в паке.
        # Кладётся ПОД полосу урона, чтобы та осталась читаемой.
        if r["is_self"] and not muted and self.cfg.get("art_panel", True):
            own = art_pixmap("rowself")
            if own is not None:
                p.drawPixmap(QRect(0, y, w, body_h), own)
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
            if self.cfg.get("metric") == "loot":
                self._txt_right(p, right, base, str(r["total"]),
                                INK3 if muted else INK, self.f_body)
                return
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
        # Потолка на длину списка нет намеренно. Он был (6 у скиллов, 12 у
        # добычи) и молча резал настоящие данные: на живом логе у своей же
        # строки 92 скилла урона, 81 хила и 382 вида добычи. Убрать его
        # ничего не стоит, потому что ниже рисуются только видимые строки,
        # а высота считается арифметикой — цена кадра не зависит от длины.
        loot_mode = self.cfg.get("metric") == "loot"
        sess_mode = self.cfg.get("metric") == "sessions"
        items = list(r.get("skills") or [])
        if not loot_mode and not sess_mode:
            auto = max(0, r["total"] - sum(v for _k, v in items))
            if auto > 0:
                items.append((AUTOATTACK, auto))
        if not items:
            return y
        top_v = max((v for _k, v in items), default=1) or 1
        _wash, rail, _t = class_triple(skilldb.COLOURS.get(r.get("cls", "")))
        wash = QColor(rail)
        wash.setAlpha(38)

        x0 = PAD + self.ICON + GAP
        icons_dir = cfgmod.skill_icons_dir(self.cfg)
        # Добыча — витрина: там смотрят, ЧТО выпало, и иконка важнее плотности.
        # Разбор по скиллам, наоборот, читают списком, и ему нужна компактность.
        step = self.LOOT_H
        size = self.LOOT_ICON
        f_name = self.f_body
        fm = QFontMetrics(f_name)
        fq = QFontMetrics(self.f_small)
        start = y
        top_edge = self.HEAD_H + self.ACT_H + self.STATS_H + self.COL_H + 1
        # Все строки списка одной высоты, поэтому видимый кусок вычисляется,
        # а не ищется перебором: цикл идёт по десятку строк на экране, а не
        # по всем четырёмстам. Именно это и позволяет обойтись без потолка.
        first = max(0, (top_edge - y) // step)
        last = min(len(items), (bottom - y) // step + 2)
        y += first * step
        for label, value in items[first:last]:
            p.fillRect(QRect(x0, y, int((w - x0 - PAD) * value / top_v),
                             step - 1), wash)
            xi = x0 + 4
            if loot_mode:
                icon = item_icon((r.get("ids") or {}).get(label, ""), size, dpr)
            elif sess_mode:
                # В сессии раскрываются участники, а не скиллы: значок —
                # эмблема класса того, кто в строке.
                icon = class_icon(cfgmod.icons_dir(self.cfg),
                                  (r.get("classes") or {}).get(label, ""),
                                  size, dpr)
            elif label == AUTOATTACK:
                # У автоатаки имени скилла в логе нет вовсе, поэтому искать
                # её среди иконок скиллов бессмысленно — берём отдельную.
                icon = ui_icon("autoattack", size, dpr)
            else:
                icon = skill_icon(icons_dir, label, size, dpr)
            if icon is not None:
                p.drawPixmap(xi, y + (step - size) // 2, icon)
                xi += size + GAP
            else:
                # Пустая рамка вместо картинки: без неё строки без иконки
                # съезжают влево и список выглядит рваным.
                p.setPen(QPen(HAIR, 1))
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(QRectF(xi + 0.5, y + (step - size) // 2 + 0.5,
                                         size - 1, size - 1), R_CHIP, R_CHIP)
                xi += size + GAP

            colour = INK2
            qual = ""
            if loot_mode:
                qual = (r.get("quality") or {}).get(label, "")
                colour = QColor(itemdb.QUALITY_COLOURS.get(qual, "#E9EEF3"))                     if qual else INK3

            if loot_mode:
                # Две строки: название и словом качество — иначе высокая
                # строка выглядит просто растянутой пустотой.
                qname = itemdb.QUALITY_NAMES.get(qual, "")
                if qname:
                    total_h = fm.height() + fq.height()
                    base = y + (step - total_h) // 2 + fm.ascent()
                    self._txt(p, xi, base,
                              fm.elidedText(label, Qt.ElideRight, int(w * 0.5)),
                              colour, f_name)
                    self._txt(p, xi, base + fq.height(), qname, INK_MUTE,
                              self.f_small)
                else:
                    base = y + (step + fm.ascent() - fm.descent()) // 2
                    self._txt(p, xi, base,
                              fm.elidedText(label, Qt.ElideRight, int(w * 0.5)),
                              colour, f_name)
                self._txt_right(p, w - PAD,
                                y + (step + fm.ascent() - fm.descent()) // 2,
                                f"x{value}", INK2, f_name)
            else:
                base = y + fm.ascent() + 2
                self._txt(p, xi, base,
                          fm.elidedText(label, Qt.ElideRight, int(w * 0.46)),
                          colour, f_name)
                right = self._txt_right(
                    p, w - PAD, base, f"{100.0 * value / (r['total'] or 1):.0f}%",
                    INK3, self.f_small) - GAP
                mant, suf = fmt_ui(value)
                self._txt_right(p, right, base, mant + suf, INK2, self.f_small)
            y += step
        # Возвращаем нижнюю границу ВСЕГО списка, а не отрисованной части:
        # по ней считается высота содержимого для прокрутки.
        y = start + len(items) * step

        buffs = [] if loot_mode else (r.get("buffs") or [])
        if buffs:
            y = self._paint_buffs(p, buffs, y, x0, w, bottom, top_edge, step,
                                  size, dpr, wash)

        p.fillRect(QRectF(snap(x0 - 4, dpr), start, snap(1, dpr), y - start), HAIR)
        return y

    def _paint_buffs(self, p: QPainter, buffs, y: int, x0: int, w: int,
                     bottom: int, top_edge: int, step: int, size: int,
                     dpr: float, wash) -> int:
        """Что игрок применял помимо урона: бафы, контроль, банки, свитки.

        Отдельным блоком, потому что это другая величина: у скиллов урон, а
        здесь число применений. Мешать их в один список — сравнивать
        несравнимое.

        Расходники видны только у себя: строки «X has used <предмет>» в
        клиенте нет вовсе, поэтому чужие банки и свитки не увидит никто.
        """
        fq = QFontMetrics(self.f_small)
        cap_h = fq.height() + 6
        if y + cap_h > top_edge and y < bottom:
            p.fillRect(QRectF(x0, y + cap_h - 1, w - x0 - PAD, 1), RULE)
            p.setFont(self.f_caps)
            p.setPen(INK3)
            p.drawText(x0 + 4, y + fq.ascent() + 2, "BUFFS & ITEMS")
        y += cap_h

        top_v = max((v for _k, v in buffs), default=1) or 1
        fm = QFontMetrics(self.f_body)
        first = max(0, (top_edge - y) // step)
        last = min(len(buffs), (bottom - y) // step + 2)
        yy = y + first * step
        for label, count in buffs[first:last]:
            p.fillRect(QRect(x0, yy, int((w - x0 - PAD) * count / top_v),
                             step - 1), wash)
            xi = x0 + 4
            icon = (skill_icon(cfgmod.skill_icons_dir(self.cfg), label, size, dpr)
                    or item_icon_named(label, size, dpr))
            if icon is not None:
                p.drawPixmap(xi, yy + (step - size) // 2, icon)
            else:
                p.setPen(QPen(HAIR, 1))
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(QRectF(xi + 0.5, yy + (step - size) // 2 + 0.5,
                                         size - 1, size - 1), R_CHIP, R_CHIP)
            xi += size + GAP
            base = yy + (step + fm.ascent() - fm.descent()) // 2
            self._txt(p, xi, base,
                      fm.elidedText(label, Qt.ElideRight, int(w * 0.5)),
                      INK2, self.f_body)
            self._txt_right(p, w - PAD, base, f"x{count}", INK3, self.f_body)
            yy += step
        return y + len(buffs) * step

    # -- пусто и подвал -----------------------------------------------------

    #: Насколько гасить подложку. Пока таблица пуста, картинку видно;
    #: как только пошли цифры, она уходит почти в фон — но НЕ исчезает:
    #: пропадающая на первом же ударе картинка выглядит поломкой.
    BACKDROP_VEIL_IDLE = 0.62
    BACKDROP_VEIL_BUSY = 0.86
    def _paint_idle_art(self, p: QPainter, w: int, top: int, bottom: int,
                        dpr: float, busy: bool = False) -> None:
        """Подложка таблицы: пейзаж под вуалью.

        Рисуется ВСЕГДА, а не только на пустой таблице. Разница лишь в
        силе вуали: в бою она гуще, чтобы цифры читались, но картинка
        остаётся на месте — иначе окно на первом же ударе будто меняет
        оформление.

        Персонажа здесь больше нет: вырезанный из игры силуэт поверх
        пейзажа выглядел наклейкой, а не оформлением. Пейзаж под вуалью
        работает фоном и не спорит ни с цифрами, ни с названием.
        """
        space_h = bottom - top
        space_w = w - 2 * PAD
        if space_h < 80 or space_w < 80:
            return                      # в узкую щель картинку не втискиваем

        # Пейзаж за спиной. Заполняет область целиком с обрезкой по краям,
        # а не вписывается: поля вокруг фона выглядели бы дырой. Сверху
        # кладём тёмную вуаль — без неё текст статуса и полосы таблицы
        # тонут в листве, а метр должен оставаться читаемым в первую очередь.
        back = art_pixmap("idle-bg")
        if back is not None and not back.isNull():
            scale = max(w / back.width(), space_h / back.height())
            bw, bh = max(1, int(back.width() * scale)), max(1, int(back.height() * scale))
            scaled_bg = back.scaled(int(bw * dpr), int(bh * dpr),
                                    Qt.KeepAspectRatioByExpanding,
                                    Qt.SmoothTransformation)
            scaled_bg.setDevicePixelRatio(dpr)
            p.save()
            p.setClipRect(QRect(0, top, w, space_h))
            p.drawPixmap((w - bw) // 2, top + (space_h - bh) // 2, scaled_bg)
            veil = QColor(L1)
            veil.setAlphaF(self.BACKDROP_VEIL_BUSY if busy
                           else self.BACKDROP_VEIL_IDLE)
            p.fillRect(QRect(0, top, w, space_h), veil)
            # Мягкое затухание к верхней кромке: резкий стык с заголовками
            # колонок читался как чужая картинка, вставленная поверх окна.
            grad = QLinearGradient(0, top, 0, top + min(60, space_h // 3))
            grad.setColorAt(0.0, self._bg(L1))
            fade = QColor(L1)
            fade.setAlpha(0)
            grad.setColorAt(1.0, fade)
            p.fillRect(QRect(0, top, w, min(60, space_h // 3)), grad)
            p.restore()


    def _paint_empty(self, p: QPainter, w: int, top: int, bottom: int,
                     snap_: dict) -> None:
        if self.cfg.get("metric") == "sessions":
            msg = ("No saved sessions yet.\n"
                   "A session starts with the first hit and is saved when you "
                   "press Reset or close the meter.")
        elif snap_.get("error"):
            msg = snap_["error"]
        elif snap_.get("boss_only") and not snap_.get("boss"):
            msg = "boss filter is on, but no main target yet"
        elif snap_.get("waiting"):
            msg = ("Waiting for Chat.log to appear.\n"
                   "Log in to the game — the file is created on login.")
        elif snap_.get("paused"):
            msg = "paused — press Start"
        else:
            msg = "waiting for combat…"
            age = snap_.get("log_age", -1)
            read = (snap_.get("stats") or {}).get("read", 0)
            # Файл, который не менялся часами, при нуле прочитанных строк
            # — это не «затишье в бою», а выключенный чат-лог или чужой
            # файл. Молчать об этом нельзя: картинка та же самая.
            if not read and age > 600:
                mins = int(age // 60)
                span = f"{mins // 60} h" if mins >= 120 else f"{mins} min"
                msg = (f"Chat.log has not changed for {span}.\n"
                       "Is the chat log turned on in the game launcher?")
        p.setFont(self.f_small)
        p.setPen(DANGER if snap_.get("error") else INK3)
        # Строка состояния держится у верхней кромки: ниже стоит название,
        # а ещё ниже — персонаж, и по центру они спорили бы друг с другом.
        p.drawText(QRect(PAD * 2, top + 10, w - PAD * 4, max(48, (bottom - top) // 3)),
                   Qt.AlignHCenter | Qt.TextWordWrap, msg)
        self._paint_wordmark(p, w, top, bottom)

    #: Подпись под названием в пустом окне. Метр рассчитан на английский
    #: клиент Origin, и честнее сказать это прямо, чем оставлять человека
    #: выяснять на своём сервере, почему таблица пустая.
    TAGLINE = "specially for Aion Origin"

    def _paint_wordmark(self, p: QPainter, w: int, top: int, bottom: int) -> None:
        """Название по центру пустой таблицы."""
        space = bottom - top
        if space < 120 or w < 220:
            return                       # в тесном окне место дороже
        big = QFont(self.f_title)
        big.setPointSize(max(13, self.f_title.pointSize() + 7))
        big.setWeight(QFont.Bold)
        big.setLetterSpacing(QFont.PercentageSpacing, 118)
        fm = QFontMetrics(big)
        fm_small = QFontMetrics(self.f_small)
        block = fm.height() + fm_small.height() + 4
        y = top + (space - block) // 2 + fm.ascent()

        name_w = fm.horizontalAdvance(APP_NAME)
        p.setFont(big)
        p.setPen(QColor(GOLD_DIM))
        p.drawText((w - name_w) // 2, y, APP_NAME)

        tag_w = fm_small.horizontalAdvance(self.TAGLINE)
        p.setFont(self.f_small)
        p.setPen(INK3)
        p.drawText((w - tag_w) // 2, y + fm_small.height() + 2, self.TAGLINE)

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

        # Открытая сессия обязана быть подписана: без этого человек через
        # минуту забудет, что смотрит прошлое, и решит, что метр перестал
        # считать. Рядом — выход обратно к живым данным.
        label = snap_.get("saved_label")
        if label:
            tag = fm.elidedText(label, Qt.ElideRight, max(80, int(w * 0.34)))
            self._txt(p, x, base, tag, ACCENT, self.f_small)
            x += fm.horizontalAdvance(tag) + GAP
            close = "×  live"
            cw = fm.horizontalAdvance(close)
            rect = QRect(x - 2, top + 2, cw + 6, self.FOOT_H - 4)
            self._hit.append(("btn:live", rect))
            hot = self._hot == "btn:live"
            self._txt(p, x, base, close, INK if hot else INK3, self.f_small)
            x += cw + GROUP

        # Включённый фильтр обязан быть виден в самом окне: иначе цифры
        # вдвое ниже привычных выглядят как поломка счётчика, а не как
        # «показан только босс». Имя цели тут же отвечает, кто такой босс.
        if snap_.get("boss_only") and snap_.get("boss"):
            # Если цель добили, показываем ещё и за сколько: скорость
            # убийства — то, чем меряются между собой, а из таблицы урона
            # её не видно.
            secs = snap_.get("boss_seconds")
            speed = f" · {secs // 60}:{secs % 60:02d}" if secs else ""
            tag = fm.elidedText(f"boss: {snap_['boss']}{speed}", Qt.ElideRight,
                                max(60, int(w * 0.42)))
            self._txt(p, x, base, tag, ACCENT, self.f_small)
            x += fm.horizontalAdvance(tag) + GROUP

        loot = snap_.get("loot", {})
        slots = []
        # Опыт и кинах уехали в полоску под кнопками — в подвале они бы
        # просто дублировались. Здесь остаётся то, чему наверху места нет.
        pairs = (("exp", "XP"), ("ap", "AP"), ("kinah", "Kinah"))
        if self.STATS_H:
            # Всё, что уехало в полоску, здесь дублировать незачем.
            pairs = (("deaths", "deaths"), ("pvp_kills", "PvP"))
        for key, label in pairs:
            value = loot.get("kinah_in" if key == "kinah" else key)
            if value:
                mant, suf = fmt_ui(value)
                slots.append((label, mant + suf))
        total_txt = fmt_ui(snap_.get("total", 0))
        total_w = (QFontMetrics(self.f_num).horizontalAdvance(total_txt[0])
                   + fm.horizontalAdvance(total_txt[1] + "TOTAL") + GROUP + GAP)
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

        # Версия по центру подвала. Рисуем ДО итога, но проверяем, что
        # она не налезает ни на левую группу, ни на правую: в узком окне
        # лучше её не показать, чем положить поверх цифр.
        ver = version_display()
        fm_v = QFontMetrics(self.f_small)
        ver_w = fm_v.horizontalAdvance(ver)
        ver_x = (w - ver_w) // 2
        right_edge = w - PAD - self.grip_reserve() - total_w
        if ver_x > x + GROUP and ver_x + ver_w < right_edge - GROUP:
            self._txt(p, ver_x, base, ver, INK_MUTE, self.f_small)

        right = w - PAD - self.grip_reserve()
        if total_txt[1]:
            right = self._txt_right(p, right, base, total_txt[1], INK2, self.f_small)
        right = self._txt_right(p, right, base, total_txt[0], INK, self.f_num) - GAP
        p.setFont(self.f_caps)
        p.setPen(INK3)
        cw = QFontMetrics(self.f_caps).horizontalAdvance("TOTAL")
        p.drawText(right - cw, base, "TOTAL")

    def _paint_frame(self, p: QPainter, w: int, h: int) -> None:
        """Рамка окна из ассет-пака, нарезанная на девять кусков.

        Углы рисуются в натуральную величину, прямые участки РАСТЯГИВАЮТСЯ,
        а не повторяются. Причина в самой картинке: замер показал отклонение
        поперечных срезов медианой 35 из 255 — это живописный шум, и при
        повторе он дал бы видимую рябь с периодом в один кусок. Растяжение
        шум размазывает.
        """
        pm = frame_pixmap()
        if pm is None:
            return
        man = assets.manifest()
        src_c = int(man.get("frame_corner", 49))
        src_b = int(man.get("frame_border", 27))
        sw, sh = pm.width(), pm.height()
        c, bd, _inset = self.frame_geometry()

        def blit(dx, dy, dw, dh, sx, sy, sww, shh):
            if dw > 0 and dh > 0:
                p.drawPixmap(QRect(dx, dy, dw, dh), pm, QRect(sx, sy, sww, shh))

        mid_w, mid_h = max(0, w - 2 * c), max(0, h - 2 * c)
        s_mid_w, s_mid_h = sw - 2 * src_c, sh - 2 * src_c
        # углы
        blit(0, 0, c, c, 0, 0, src_c, src_c)
        blit(w - c, 0, c, c, sw - src_c, 0, src_c, src_c)
        blit(0, h - c, c, c, 0, sh - src_c, src_c, src_c)
        blit(w - c, h - c, c, c, sw - src_c, sh - src_c, src_c, src_c)
        # прямые участки, растянутые
        blit(c, 0, mid_w, bd, src_c, 0, s_mid_w, src_b)
        blit(c, h - bd, mid_w, bd, src_c, sh - src_b, s_mid_w, src_b)
        blit(0, c, bd, mid_h, 0, src_c, src_b, s_mid_h)
        blit(w - bd, c, bd, mid_h, sw - src_b, src_c, src_b, s_mid_h)

    def _paint_grip(self, p: QPainter, w: int, h: int) -> None:
        """Уголок изменения размера, на собственной площадке.

        Прежние две тонкие нити лежали прямо на золотой накладке рамки и
        пропадали в ней — окно выглядело нерастягиваемым. Сдвинуть их
        внутрь тоже нельзя, там итоговая цифра. Поэтому у уголка своя
        подложка, а подвал уступает ей место (см. grip_reserve).
        """
        side = self.GRIP
        inset = self.frame_inset()
        x, y = w - inset - side - 2, h - inset - side - 2
        if inset:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(8, 11, 16, 190))
            p.drawRoundedRect(QRectF(x, y, side, side), 3, 3)
            p.setBrush(Qt.NoBrush)
        p.setPen(QPen(INK3 if inset else INK_MUTE, 1))
        step = max(3, side // 4)
        for i in range(1, 4):
            off = i * step
            p.drawLine(x + side - off - 2, y + side - 3, x + side - 3, y + side - off - 2)

    # -- мышь ---------------------------------------------------------------

    def frame_geometry(self) -> tuple[int, int, int]:
        """(угол, кант, отступ содержимого) в пикселях окна, либо нули.

        Отступ БОЛЬШЕ канта: угловая накладка шире прямого участка и
        заходит внутрь. Берём половину этого захода — оставшееся приходится
        на PAD, который у краёв и так пустой, зато таблица не теряет
        полсантиметра ширины на всех четырёх сторонах.
        """
        if not self.cfg.get("art_frame", True) or frame_pixmap() is None:
            return 0, 0, 0
        man = assets.manifest()
        k = max(0.34, min(1.0, self.ICON / 74.0))
        corner = max(10, int(int(man.get("frame_corner", 49)) * k))
        border = max(3, int(int(man.get("frame_border", 27)) * k))
        return corner, border, border + (corner - border) // 2

    def frame_inset(self) -> int:
        return self.frame_geometry()[2]

    def _inset_point(self, pos):
        """Точка мыши в координатах СОДЕРЖИМОГО, а не окна.

        Отрисовка сдвинута внутрь на толщину рамки, а события мыши приходят
        от окна — без поправки клики промахивались бы ровно на эту величину.
        """
        point = pos.toPoint() if hasattr(pos, "toPoint") else pos
        if self.cfg.get("streamer"):
            # В режиме стримера ни рамки, ни полосы заголовка нет, и
            # поправлять координаты не на что.
            return point
        inset = self.frame_inset()
        # По вертикали вычитаем ещё и полосу заголовка: содержимое ниже неё,
        # а координаты кликов приходят от окна. Без этой поправки все
        # кнопки и вкладки промахивались бы ровно на её высоту.
        return point - QPoint(inset, inset + self.TITLE_H)

    def _hit_at(self, pos) -> str:
        point = self._inset_point(pos)
        for name, rect in self._hit:
            if rect.contains(point):
                return name
        for rect, row in self._row_rects:
            if rect.contains(point):
                return f"row:{row['name']}"
        return ""

    def _row_at(self, pos) -> dict | None:
        point = self._inset_point(pos)
        for rect, row in self._row_rects:
            if rect.contains(point):
                return row
        return None

    #: Минимум строк таблицы, ради которых полоска сводки уплотняется.
    MIN_TABLE_ROWS = 4

    def grip_reserve(self) -> int:
        """Сколько места справа в подвале держим под уголок."""
        return (self.GRIP + 6) if self.frame_inset() else 0

    def _in_grip(self, pos) -> bool:
        # Зона больше самого значка и растёт вместе с рамкой: попасть в
        # 16 пикселей вслепую, да ещё поверх орнамента, было тяжело.
        side = max(18, self.frame_inset() + 16)
        return pos.x() > self.width() - side and pos.y() > self.height() - side

    def mousePressEvent(self, e) -> None:
        if e.button() != Qt.LeftButton:
            return
        hit = self._hit_at(e.position())
        if hit.startswith(("metric:", "btn:", "col:")):
            self._activate(hit, e.globalPosition().toPoint())
            return
        row = self._row_at(e.position())
        if row is not None:
            if self.cfg.get("metric") == "sessions":
                # Строка сессии — не раскрывающийся список, а ссылка:
                # метр переключается на неё целиком.
                self.open_session(row["name"])
                return
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
            "shot": self.action_screenshot,
            "settings": lambda: self.on_settings and self.on_settings(),
            "about": lambda: self.on_about and self.on_about(),
            "stream": self.action_toggle_streamer,
            "stream_off": self.action_toggle_streamer,
            "boss": lambda: self._toggle_cfg("boss_only"),
            "live": self.close_session_view,
            "close": lambda: self.on_quit and self.on_quit(),
        }
        action = actions.get(value)
        if action:
            action()

    #: Какой хоткей относится к какой кнопке — его дописываем в заголовок
    #: подсказки: сочетание, о котором человек не знает, ему не поможет.
    HOTKEY_OF = {"btn:play": "pause", "btn:clear": "reset", "btn:copy": "copy"}

    def _tip_for(self, hit: str) -> tuple[str, str] | None:
        """Заголовок и пояснение для элемента под курсором."""
        if hit.startswith("drag:"):
            return None
        entry = HELP.get(hit)
        if entry is None:
            kind, _, value = hit.partition(":")
            if kind == "metric" and METRIC_TITLE.get(value):
                return (METRIC_TITLE[value], "")
            return None
        title, body = entry
        # Сочетание клавиш дописываем к заголовку: хоткей, о котором
        # человек не знает, ему не помогает.
        key = self.cfg.get("hotkeys", {}).get(self.HOTKEY_OF.get(hit, ""), "")
        return (f"{title}   {key}" if key else title, body)

    def _paint_hint(self, p: QPainter, w: int, h: int, dpr: float) -> None:
        """Подсказка НАД элементом, к которому она относится.

        Рисуем сами, а не через QToolTip: окно живёт без фокуса и с
        WS_EX_NOACTIVATE, и системная подсказка в нём появляется через раз,
        особенно поверх игры. Своя плашка ведёт себя предсказуемо и
        выглядит частью окна.
        """
        entry = self._tip_for(self._hot) if self._hot else None
        if entry is None or self._hot_rect is None:
            return
        title, body = entry
        fm_t = QFontMetrics(self.f_tab)
        fm_b = QFontMetrics(self.f_small)
        pad = 8
        tw = fm_t.horizontalAdvance(title)
        bw = fm_b.horizontalAdvance(body) if body else 0
        box_w = min(w - 2 * PAD, max(tw, bw) + 2 * pad)
        line_h = fm_t.height() + (fm_b.height() + 2 if body else 0)
        box_h = line_h + 2 * 6

        # Плашка встаёт НАД элементом и центрируется по нему: подсказка
        # должна быть рядом с тем, о чём говорит, а не в отдельном углу.
        rect = self._hot_rect
        x = max(PAD, min(rect.center().x() - box_w // 2, w - PAD - box_w))
        y = rect.top() - box_h - 6
        # Плашке разрешено заезжать на полосу вкладок и на заголовок: она
        # всплывающая, живёт долю секунды и должна стоять над тем, о чём
        # говорит. Ограничение одно — не вылезти за верхнюю кромку окна.
        # (Начало координат сдвинуто вниз на высоту заголовка, поэтому его
        # верх — это отрицательный y.)
        if y < -self.TITLE_H + 2:
            # Элемент сам стоит у самой кромки — тогда снизу.
            y = min(rect.bottom() + 6, h - box_h - 2)

        p.setPen(Qt.NoPen)
        p.setBrush(self._bg(L2, chrome=True))
        p.drawRoundedRect(QRectF(x, y, box_w, box_h), R_CHIP, R_CHIP)
        p.setPen(QPen(HAIR, 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(x + 0.5, y + 0.5, box_w - 1, box_h - 1),
                          R_CHIP, R_CHIP)

        base = y + 6 + fm_t.ascent()
        self._txt(p, x + pad, base,
                  fm_t.elidedText(title, Qt.ElideRight, box_w - 2 * pad),
                  INK, self.f_tab)
        if body:
            self._txt(p, x + pad, base + fm_b.height() + 2,
                      fm_b.elidedText(body, Qt.ElideRight, box_w - 2 * pad),
                      INK3, self.f_small)

    def mouseMoveEvent(self, e) -> None:
        hot = self._hit_at(e.position())
        if hot != self._hot:
            self._hot = hot
            self._hot_rect = next((r for n, r in self._hit if n == hot), None)
            self.update()
        if hot and not hot.startswith("drag:"):
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
            self._hot_rect = None
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

    def _menu_pos(self, menu: QMenu, at) -> QPoint:
        """Куда открыть меню, чтобы оно не легло на таблицу.

        По умолчанию Qt раскрывает меню от курсора вниз-вправо, а курсор в
        этот момент стоит на кнопке в шапке — то есть прямо над списком, и
        меню закрывает как раз те строки, ради которых окно и открыто.
        Уводим его ЗА окно: справа, если там есть место на экране, иначе
        слева, иначе — под нижнюю кромку. Внутрь окна возвращаемся только
        если человек зажал метр в угол экрана и снаружи места нет вовсе.
        """
        size = menu.sizeHint()
        frame = self.frameGeometry()
        screen = (self.screen() or QGuiApplication.primaryScreen())
        area = screen.availableGeometry()

        y = min(max(at.y() - 6, area.top()), area.bottom() - size.height())
        right, left = frame.right() + 2, frame.left() - size.width() - 2
        if right + size.width() <= area.right():
            return QPoint(right, y)
        if left >= area.left():
            return QPoint(left, y)
        below = frame.bottom() + 2
        if below + size.height() <= area.bottom():
            x = min(max(frame.left(), area.left()), area.right() - size.width())
            return QPoint(x, below)
        return QPoint(min(at.x(), area.right() - size.width()), y)

    def _show_menu(self, at) -> None:
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu{background:#161B23;color:#E9EEF3;"
            "border:1px solid rgba(255,255,255,0.09);border-radius:6px;padding:4px}"
            "QMenu::item{padding:6px 20px 6px 12px;border-radius:4px}"
            "QMenu::item:selected{background:rgba(255,255,255,0.08)}"
            "QMenu::separator{height:1px;background:rgba(255,255,255,0.06);margin:4px 6px}"
        )
        head = QAction(f"Wingbeat {__version__}", self, enabled=False)
        menu.addAction(head)
        stats = self.snapshot.get("stats", {})
        # Строка разбора нужна ИМЕННО когда прочитано ноль: раньше она
        # пряталась под условием read > 0, то есть исчезала в каждом
        # сломанном случае, ради которого её и добавляли.
        menu.addAction(QAction(
            f"parsed {stats.get('parsed', 0)} of {stats.get('read', 0)} lines",
            self, enabled=False))
        log_path = self.snapshot.get("log_path") or ""
        if log_path:
            fm = QFontMetrics(self.f_small)
            menu.addAction(QAction(fm.elidedText(log_path, Qt.ElideMiddle, 420),
                                   self, enabled=False))
        menu.addSeparator()
        # Фильтр по главной цели — первым: им пользуются в бою, а не раз в
        # месяц, как прозрачностью. Подпись несёт имя цели, чтобы было
        # видно, ЧТО именно метр считает боссом в этом бою.
        boss = self.snapshot.get("boss") or ""
        act = QAction(f"Boss damage only — {boss}" if boss else "Boss damage only",
                      self, checkable=True, checked=bool(self.cfg.get("boss_only")),
                      enabled=self.cfg.get("metric", "damage") == "damage")
        act.triggered.connect(lambda _c: self._toggle_cfg("boss_only"))
        menu.addAction(act)
        menu.addSeparator()
        for key, label in (("show_loot", "Show footer summary"),
                           ("click_through", "Click-through"),
                           ("transparent", "Transparent background"),
                           ("always_on_top", "Always on top")):
            act = QAction(label, self, checkable=True, checked=bool(self.cfg.get(key)))
            act.triggered.connect(lambda _c, k=key: self._toggle_cfg(k))
            menu.addAction(act)
        menu.addSeparator()
        act_stream = QAction("Streamer mode", self, checkable=True,
                             checked=bool(self.cfg.get("streamer")))
        act_stream.triggered.connect(lambda _c: self.action_toggle_streamer())
        menu.addAction(act_stream)
        menu.addAction("About Wingbeat",
                       lambda: self.on_about and self.on_about())
        menu.addAction("Hide window", self.action_toggle_hide)
        menu.addAction("Settings…", lambda: self.on_settings and self.on_settings())
        menu.addAction("Quit", lambda: self.on_quit and self.on_quit())
        menu.exec(self._menu_pos(menu, at))

    def _toggle_cfg(self, key: str) -> None:
        self.cfg[key] = not self.cfg.get(key)
        if key == "boss_only":
            self.selected = ""
            self._anim.clear()
            republish = getattr(self.engine, "republish", None)
            if republish is not None:
                republish()
            self._refresh()
        elif key == "transparent":
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
