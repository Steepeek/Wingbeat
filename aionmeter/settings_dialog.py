"""Окно настроек. Здесь только то, что у разных людей действительно разное.

Чего здесь намеренно НЕТ.

* Пути к файлу лога. Человек знает, где у него игра, и не обязан знать,
  что метру нужен именно Chat.log: он указывает папку, файл программа
  находит сама.
* Кодировки. Определяются по содержимому; выбор вручную нужен был ровно
  один раз — при отладке, и в руках человека он только ломает разбор.
* Базы классов. Это не выбор, а служебные данные: программа собирает их
  из установленного клиента сама и пересобирает, когда клиент сменился.
* Параметров расчёта (окно DPS, конец боя, разрыв активности) и раскладки
  таблицы. Это не вкус, а определения величин: если у двух людей окно DPS
  разное, их цифры несравнимы, а метр нужен именно для сравнения.

Значения этих настроек остаются в config.json со своими умолчаниями, и
править их руками по-прежнему можно — просто это не то, что стоит совать
каждому в окно.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox,
                               QFileDialog, QFormLayout, QGridLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QScrollArea, QSlider, QSpinBox, QTabWidget,
                               QVBoxLayout, QWidget)

from . import config as cfgmod
from . import sessions as sessmod
from .overlay import APP_NAME

STYLE = """
QDialog, QWidget { background:#161b21; color:#dfe6e8; font-family:'Segoe UI'; font-size:13px; }
QGroupBox { border:1px solid #2a343c; border-radius:4px; margin-top:12px; padding-top:8px; }
QGroupBox::title { subcontrol-origin:margin; left:9px; padding:0 5px; color:#8a97a0; }
QLineEdit, QSpinBox { background:#101419; border:1px solid #2a343c;
    border-radius:3px; padding:4px 7px; selection-background-color:#3a5570; }
QLineEdit:focus, QSpinBox:focus { border-color:#eda549; }
QPushButton { background:#222c35; border:1px solid #33414c; border-radius:3px; padding:5px 12px; }
QPushButton:hover { background:#2b3844; }
QPushButton:default { border-color:#eda549; }
QTabBar::tab { background:#1b222a; padding:6px 13px; border:1px solid #2a343c; border-bottom:none; }
QTabBar::tab:selected { background:#161b21; color:#eda549; }
QTabWidget::pane { border:1px solid #2a343c; top:-1px; }
QLabel[hint="1"] { color:#8a97a0; font-size:11px; }
QLabel[bad="1"] { color:#e07a5f; font-size:11px; }
QScrollArea { border:none; }
QSlider::groove:horizontal { height:4px; background:#2a343c; border-radius:2px; }
QSlider::handle:horizontal { background:#eda549; width:13px; margin:-5px 0; border-radius:6px; }
"""

#: Потолок высоты окна: ниже самого маленького ходового ноутбука.
MAX_H = 560


class _FindLog(QThread):
    """Автопоиск в фоне: обход дисков может занять несколько секунд."""
    found = Signal(str)

    def run(self) -> None:
        self.found.emit(cfgmod.autodetect_log())


class SettingsDialog(QDialog):
    def __init__(self, cfg: dict, engine=None, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.engine = engine
        self.finder: _FindLog | None = None
        self.setWindowTitle(f"{APP_NAME} — Settings")
        self.setStyleSheet(STYLE)
        self.setMinimumWidth(520)
        self.setMaximumHeight(MAX_H)
        # Метр висит поверх всех окон, поэтому собственное окно настроек
        # обязано быть выше него: иначе оно открывается ПОД метром, и его
        # не видно. Сам метр на это время верхний слой отпускает — см.
        # Overlay.suspend_topmost.
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)

        tabs = QTabWidget()
        tabs.addTab(self._scroll(self._tab_game()), "Game")
        tabs.addTab(self._scroll(self._tab_window()), "Window")
        tabs.addTab(self._scroll(self._tab_sessions()), "Sessions")
        tabs.addTab(self._scroll(self._tab_keys()), "Hotkeys")

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("Save")
        buttons.button(QDialogButtonBox.Cancel).setText("Cancel")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        root = QVBoxLayout(self)
        root.setSpacing(8)
        root.addWidget(tabs)
        root.addWidget(self.status_label())
        root.addWidget(buttons)
        self.resize(self.width(), min(MAX_H, self.sizeHint().height()))

    # -- каркас --

    def _scroll(self, inner: QWidget) -> QScrollArea:
        area = QScrollArea()
        area.setWidget(inner)
        area.setWidgetResizable(True)
        area.setFrameShape(QScrollArea.NoFrame)
        return area

    def _form(self) -> tuple[QWidget, QFormLayout]:
        w = QWidget()
        form = QFormLayout(w)
        form.setSpacing(6)
        form.setContentsMargins(10, 10, 10, 10)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        return w, form

    def _row(self, *widgets: QWidget) -> QWidget:
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        for i, widget in enumerate(widgets):
            row.addWidget(widget, 1 if i == 0 else 0)
        return holder

    # -- вкладки --

    def _tab_game(self) -> QWidget:
        w, form = self._form()

        self.ed_dir = QLineEdit(self._current_dir())
        self.ed_dir.setPlaceholderText(r"for example E:\Game\Aion\Origin")
        self.ed_dir.setToolTip(
            "The folder the game is installed in. Wingbeat finds Chat.log inside it.")
        self.ed_dir.textChanged.connect(self._refresh_log_line)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse_dir)
        self.btn_find = QPushButton("Find it")
        self.btn_find.clicked.connect(self._autodetect)
        form.addRow("Game folder", self._row(self.ed_dir, browse, self.btn_find))

        self.lbl_log = QLabel()
        self.lbl_log.setWordWrap(True)
        form.addRow("", self.lbl_log)
        self._refresh_log_line()

        self.ed_self = QLineEdit(self.cfg.get("self_name", ""))
        self.ed_self.setPlaceholderText("leave empty — detected automatically")
        self.ed_self.setToolTip(
            "Combat lines always call you \"You\", so the name is only used "
            "to label your row. Detected automatically when you log in.")
        form.addRow("Character name", self.ed_self)

        self.ch_own_nick = QCheckBox("Label my row with the character name instead of \"You\"")
        self.ch_own_nick.setChecked(bool(self.cfg.get("show_own_nick")))
        form.addRow("", self.ch_own_nick)

        form.addRow("", self._hint(
            "Log encoding, the class database and the calculation settings "
            "configure themselves from your own game install. If something in "
            "the log is not recognised, the status line below shows it."))
        return w

    def _tab_window(self) -> QWidget:
        w, form = self._form()

        self.ch_ontop = QCheckBox("Always on top")
        self.ch_ontop.setChecked(self.cfg.get("always_on_top", True))
        self.ch_transparent = QCheckBox("Transparent background")
        self.ch_transparent.setChecked(self.cfg.get("transparent", False))
        self.ch_click = QCheckBox("Click-through")
        self.ch_click.setChecked(self.cfg.get("click_through", False))
        self.ch_loot = QCheckBox("Loot line at the bottom")
        self.ch_loot.setChecked(self.cfg.get("show_loot", True))
        self.ch_loot.setToolTip("XP, AP, kinah and kills in the window footer.")

        box = QGroupBox()
        grid = QGridLayout(box)
        grid.setContentsMargins(8, 6, 8, 6)
        grid.setSpacing(4)
        for i, ch in enumerate((self.ch_ontop, self.ch_transparent,
                                self.ch_click, self.ch_loot)):
            grid.addWidget(ch, i // 2, i % 2)
        form.addRow(box)

        self.sl_opacity = QSlider(Qt.Horizontal)
        self.sl_opacity.setRange(30, 100)
        self.sl_opacity.setValue(int(self.cfg.get("opacity", 1.0) * 100))
        self.lbl_opacity = QLabel(f"{self.sl_opacity.value()}%")
        self.sl_opacity.valueChanged.connect(
            lambda v: self.lbl_opacity.setText(f"{v}%"))
        form.addRow("Opacity", self._row(self.sl_opacity, self.lbl_opacity))
        form.addRow("", self._hint(
            "Opacity only applies with a transparent background. Nothing "
            "draws over EXCLUSIVE fullscreen — the game must run in windowed "
            "fullscreen (borderless)."))
        return w

    def _tab_sessions(self) -> QWidget:
        w, form = self._form()

        self.ch_sessions = QCheckBox("Save sessions to disk")
        self.ch_sessions.setChecked(self.cfg.get("save_sessions", True))
        form.addRow("", self.ch_sessions)

        self.sp_keep = self._spin(0, 2000, int(self.cfg.get("keep_sessions", 200)), " files")
        self.sp_keep.setSpecialValueText("keep all")
        self.sp_keep.setToolTip("Older files beyond this count are deleted automatically.")
        form.addRow("Keep last", self.sp_keep)

        try:
            have = len(list(sessmod.sessions_dir().glob("session-*.json")))
        except OSError:
            have = 0
        btn_open = QPushButton("Open folder")
        btn_open.clicked.connect(self._open_sessions)
        form.addRow(f"Saved: {have}", btn_open)
        form.addRow("", self._hint(
            "A session starts with the first hit and closes when you press "
            "Reset or quit. Each one is a separate file you can send or "
            "delete by hand."))
        return w

    def _tab_keys(self) -> QWidget:
        w, form = self._form()
        binds = self.cfg.get("hotkeys", {})
        self.key_edits = {}
        for key, label in (("reset", "Reset"), ("pause", "Start / pause"),
                           ("click_through", "Click-through"),
                           ("hide", "Show / hide"),
                           ("copy", "Copy to clipboard"),
                           ("streamer", "Streamer mode")):
            ed = QLineEdit(binds.get(key, ""))
            ed.setPlaceholderText("for example Ctrl+Shift+F1 — empty means off")
            self.key_edits[key] = ed
            form.addRow(label, ed)
        form.addRow("", self._hint(
            "A modifier is required, otherwise the game swallows the key. A "
            "combination already taken by another program simply will not "
            "fire — the status line below shows that."))
        return w

    # -- папка игры --

    def _current_dir(self) -> str:
        """Что показать в поле: сохранённую папку или папку найденного лога."""
        if self.cfg.get("game_dir"):
            return self.cfg["game_dir"]
        log = self.cfg.get("log_path") or ""
        return str(Path(log).parent) if log else ""

    def _refresh_log_line(self) -> None:
        """Строка под полем: какой именно файл будет читаться."""
        found = cfgmod.find_log_in(self.ed_dir.text().strip())
        if found:
            try:
                size = Path(found).stat().st_size / (1024 * 1024)
                self.lbl_log.setText(f"Chat.log found: {found} ({size:.0f} MB)")
            except OSError:
                self.lbl_log.setText(f"Chat.log found: {found}")
            self.lbl_log.setProperty("bad", "0")
            self.lbl_log.setProperty("hint", "1")
        elif self.ed_dir.text().strip():
            self.lbl_log.setText(
                "No Chat.log in this folder. Turn the chat log on in the "
                "game launcher and log in — the file appears by itself.")
            self.lbl_log.setProperty("hint", "0")
            self.lbl_log.setProperty("bad", "1")
        else:
            self.lbl_log.setText("Point to the game folder — or press \"Find it\".")
            self.lbl_log.setProperty("bad", "0")
            self.lbl_log.setProperty("hint", "1")
        self.lbl_log.setStyleSheet("")      # перечитать свойство в стиле

    def _browse_dir(self) -> None:
        start = self.ed_dir.text() or str(Path.home())
        path = QFileDialog.getExistingDirectory(self, "Game folder", start)
        if path:
            self.ed_dir.setText(path)

    def _autodetect(self) -> None:
        self.btn_find.setEnabled(False)
        self.btn_find.setText("Searching…")
        self.finder = _FindLog(self)
        self.finder.found.connect(self._found)
        self.finder.start()

    def _found(self, path: str) -> None:
        self.btn_find.setEnabled(True)
        self.btn_find.setText("Find it")
        if path:
            # Автопоиск возвращает путь к файлу, а в поле у нас папка.
            self.ed_dir.setText(str(Path(path).parent))
        else:
            self.btn_find.setText("Not found")

    # -- мелочи --

    def _spin(self, lo: int, hi: int, val: int, suffix: str) -> QSpinBox:
        s = QSpinBox()
        s.setRange(lo, hi)
        s.setValue(val)
        s.setSuffix(suffix)
        return s

    def _hint(self, text: str) -> QLabel:
        lab = QLabel(text)
        lab.setProperty("hint", "1")
        lab.setWordWrap(True)
        return lab

    def status_label(self) -> QLabel:
        parts = []
        if self.engine is not None:
            parts.append(f"parsed: {self.engine.health()}")
            if self.engine.encoding:
                parts.append(f"encoding: {self.engine.encoding}")
            if self.engine.error:
                parts.append(self.engine.error)
            if getattr(self.engine, "tailer", None) and self.engine.tailer.rotations:
                parts.append(f"log re-reads: {self.engine.tailer.rotations}")
        lab = QLabel(" · ".join(parts) or "not running")
        lab.setProperty("hint", "1")
        lab.setWordWrap(True)
        return lab

    def _open_sessions(self) -> None:
        d = sessmod.sessions_dir()
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(d)))

    # -- сохранение --

    def apply_to(self, cfg: dict) -> None:
        """Только то, что есть в окне. Остальные ключи не трогаем —
        у них свои умолчания, и затирать их пустотой нельзя."""
        raw = self.ed_dir.text().strip()
        path = Path(raw) if raw else None
        if path is not None and path.is_file():
            # Указали сам файл — примем, но папку запомним тоже.
            cfg["log_path"] = str(path)
            cfg["game_dir"] = str(path.parent)
        else:
            # Путь к файлу не храним: он выводится из папки, и лишняя копия
            # рассинхронизируется при первом же переезде клиента.
            cfg["log_path"] = ""
            cfg["game_dir"] = raw
        cfg["self_name"] = self.ed_self.text().strip()
        cfg["show_own_nick"] = self.ch_own_nick.isChecked()
        cfg["always_on_top"] = self.ch_ontop.isChecked()
        cfg["transparent"] = self.ch_transparent.isChecked()
        cfg["click_through"] = self.ch_click.isChecked()
        cfg["show_loot"] = self.ch_loot.isChecked()
        cfg["opacity"] = self.sl_opacity.value() / 100
        cfg["save_sessions"] = self.ch_sessions.isChecked()
        cfg["keep_sessions"] = self.sp_keep.value()
        cfg["hotkeys"] = {k: ed.text().strip() for k, ed in self.key_edits.items()}
