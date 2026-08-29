"""Окно настроек. Всё, что у разных людей отличается, вынесено сюда."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QFileDialog, QFormLayout, QGridLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QSlider, QSpinBox, QTabWidget, QVBoxLayout, QWidget)

from . import config as cfgmod

STYLE = """
QDialog, QWidget { background:#161b21; color:#dfe6e8; font-family:'Segoe UI'; font-size:13px; }
QGroupBox { border:1px solid #2a343c; border-radius:4px; margin-top:14px; padding-top:10px; }
QGroupBox::title { subcontrol-origin:margin; left:9px; padding:0 5px; color:#8a97a0; }
QLineEdit, QSpinBox, QComboBox { background:#101419; border:1px solid #2a343c;
    border-radius:3px; padding:5px 7px; selection-background-color:#3a5570; }
QLineEdit:focus, QSpinBox:focus, QComboBox:focus { border-color:#eda549; }
QPushButton { background:#222c35; border:1px solid #33414c; border-radius:3px; padding:6px 14px; }
QPushButton:hover { background:#2b3844; }
QPushButton:default { border-color:#eda549; }
QTabBar::tab { background:#1b222a; padding:7px 15px; border:1px solid #2a343c; border-bottom:none; }
QTabBar::tab:selected { background:#161b21; color:#eda549; }
QTabWidget::pane { border:1px solid #2a343c; top:-1px; }
QLabel[hint="1"] { color:#8a97a0; font-size:11px; }
QSlider::groove:horizontal { height:4px; background:#2a343c; border-radius:2px; }
QSlider::handle:horizontal { background:#eda549; width:13px; margin:-5px 0; border-radius:6px; }
"""

COLUMNS = [("dmg", "Урон"), ("dps", "DPS"), ("pct", "Доля %"),
           ("hits", "Ударов"), ("crit", "Крит %")]


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
        self.setWindowTitle("AionMeter — настройки")
        self.setStyleSheet(STYLE)
        self.setMinimumWidth(520)

        tabs = QTabWidget()
        tabs.addTab(self._tab_source(), "Игра")
        tabs.addTab(self._tab_calc(), "Расчёт")
        tabs.addTab(self._tab_view(), "Вид")
        tabs.addTab(self._tab_keys(), "Клавиши")

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("Сохранить")
        buttons.button(QDialogButtonBox.Cancel).setText("Отмена")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        root = QVBoxLayout(self)
        root.addWidget(tabs)
        root.addWidget(self.status_label())
        root.addWidget(buttons)

    # -- вкладки --

    def _tab_source(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setSpacing(9)

        self.ed_log = QLineEdit(cfgmod.resolve_log_path(self.cfg))
        browse = QPushButton("Обзор…")
        browse.clicked.connect(self._browse)
        self.btn_find = QPushButton("Найти сам")
        self.btn_find.clicked.connect(self._autodetect)
        row = QHBoxLayout()
        row.addWidget(self.ed_log, 1)
        row.addWidget(browse)
        row.addWidget(self.btn_find)
        holder = QWidget()
        holder.setLayout(row)
        form.addRow("Файл Chat.log", holder)
        form.addRow("", self._hint(
            "Обычно это <папка игры>\\Chat.log. Если файла нет — включите ведение "
            "чат-лога в лаунчере или настройках игры."))

        self.cb_enc = QComboBox()
        self.cb_enc.addItems(["auto", "cp1251", "utf-8", "cp1252"])
        self.cb_enc.setCurrentText(self.cfg.get("encoding", "auto"))
        form.addRow("Кодировка лога", self.cb_enc)

        self.ed_self = QLineEdit(self.cfg.get("self_name", ""))
        self.ed_self.setPlaceholderText("оставьте пустым — определится само")
        form.addRow("Свой ник", self.ed_self)
        form.addRow("", self._hint(
            "В боевых строках вы всегда «You» — ник нужен, чтобы подписать вашу "
            "строку в таблице. Определяется сам по строке про Glory Points, "
            "которая появляется при входе в игру. Если не определился — "
            "впишите вручную."))
        return w

    def _tab_calc(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setSpacing(9)

        self.sp_window = self._spin(3, 60, self.cfg["dps_window"], " с")
        form.addRow("Окно текущего DPS", self.sp_window)
        form.addRow("", self._hint(
            "5 с — дёргается, 30 с — запаздывает. 10 с обычно лучший компромисс. "
            "Из-за секундной точности лога погрешность примерно 1/окно."))

        self.sp_timeout = self._spin(3, 120, self.cfg["encounter_timeout"], " с")
        form.addRow("Конец боя после тишины", self.sp_timeout)

        self.sp_gap = self._spin(2, 60, self.cfg["active_gap"], " с")
        form.addRow("Разрыв активности", self.sp_gap)
        form.addRow("", self._hint(
            "Средний DPS считается по активному времени. Пауза в 2–3 секунды — "
            "это нормальная скорость атаки, поэтому ниже 5 с ставить не стоит."))

        self.ch_mobs = QCheckBox("Скрывать мобов и NPC")
        self.ch_mobs.setChecked(self.cfg.get("hide_mobs", True))
        self.ch_pets = QCheckBox("Урон питомцев приписывать владельцу")
        self.ch_pets.setChecked(self.cfg.get("merge_pets", True))
        form.addRow("", self.ch_mobs)
        form.addRow("", self.ch_pets)
        return w

    def _tab_view(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setSpacing(9)

        self.cb_metric = QComboBox()
        for key, label in (("damage", "Урон"), ("heal", "Хил"), ("taken", "Полученный урон")):
            self.cb_metric.addItem(label, key)
        idx = self.cb_metric.findData(self.cfg.get("metric", "damage"))
        self.cb_metric.setCurrentIndex(max(0, idx))
        form.addRow("Показывать", self.cb_metric)

        self.ch_loot = QCheckBox("Строка добычи внизу (опыт, AP, кинах, убийства)")
        self.ch_loot.setChecked(self.cfg.get("show_loot", True))
        form.addRow("", self.ch_loot)

        self.ch_transparent = QCheckBox("Прозрачный фон (режим оверлея)")
        self.ch_transparent.setChecked(self.cfg.get("transparent", False))
        form.addRow("", self.ch_transparent)
        form.addRow("", self._hint(
            "По умолчанию окно обычное, непрозрачное. Прозрачность нужна, "
            "только когда метр висит прямо поверх игры."))

        self.sl_opacity = QSlider(Qt.Horizontal)
        self.sl_opacity.setRange(30, 100)
        self.sl_opacity.setValue(int(self.cfg.get("opacity", 0.88) * 100))
        self.lbl_opacity = QLabel(f"{self.sl_opacity.value()}%")
        self.sl_opacity.valueChanged.connect(lambda v: self.lbl_opacity.setText(f"{v}%"))
        row = QHBoxLayout()
        row.addWidget(self.sl_opacity, 1)
        row.addWidget(self.lbl_opacity)
        holder = QWidget()
        holder.setLayout(row)
        form.addRow("Непрозрачность", holder)
        form.addRow("", self._hint("Действует только при включённом прозрачном фоне."))

        self.sp_font = self._spin(8, 22, self.cfg.get("font_size", 12), " px")
        form.addRow("Размер шрифта", self.sp_font)

        self.sp_rows = self._spin(3, 40, self.cfg.get("max_rows", 12), " строк")
        form.addRow("Максимум строк", self.sp_rows)

        self.ch_click = QCheckBox("Клик проходит насквозь")
        self.ch_click.setChecked(self.cfg.get("click_through", False))
        self.ch_top = QCheckBox("Поверх всех окон")
        self.ch_top.setChecked(self.cfg.get("always_on_top", True))
        form.addRow("", self.ch_click)
        form.addRow("", self.ch_top)
        form.addRow("", self._hint(
            "Оверлей не виден, если игра запущена в ЭКСКЛЮЗИВНОМ полноэкранном "
            "режиме. Нужен оконный полноэкранный (borderless)."))
        return w

    def _tab_keys(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setSpacing(9)
        binds = self.cfg.get("hotkeys", {})
        self.key_edits = {}
        for key, label in (("reset", "Очистить"), ("pause", "Старт / стоп"),
                           ("click_through", "Клик насквозь"),
                           ("hide", "Скрыть / показать"), ("copy", "Скопировать в буфер")):
            ed = QLineEdit(binds.get(key, ""))
            ed.setPlaceholderText("например Ctrl+Alt+R — пусто значит выключено")
            self.key_edits[key] = ed
            form.addRow(label, ed)
        form.addRow("", self._hint(
            "Обязателен модификатор (Ctrl, Alt, Shift), иначе клавишу перехватит игра. "
            "Если сочетание уже занято другой программой, оно просто не сработает — "
            "внизу окна будет видно, что зарегистрировать не удалось."))
        return w

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
            parts.append(f"разбор: {self.engine.health()}")
            if self.engine.encoding:
                parts.append(f"кодировка: {self.engine.encoding}")
            if self.engine.error:
                parts.append(self.engine.error)
            if getattr(self.engine, "tailer", None) and self.engine.tailer.rotations:
                parts.append(f"перечитываний лога: {self.engine.tailer.rotations}")
        lab = QLabel(" · ".join(parts) or "метр не запущен")
        lab.setProperty("hint", "1")
        lab.setWordWrap(True)
        return lab

    def _browse(self) -> None:
        start = self.ed_log.text() or str(Path.home())
        path, _ = QFileDialog.getOpenFileName(
            self, "Выберите Chat.log", start, "Chat.log (Chat.log);;Все файлы (*)")
        if path:
            self.ed_log.setText(path)

    def _autodetect(self) -> None:
        self.btn_find.setEnabled(False)
        self.btn_find.setText("Ищу…")
        self.finder = _FindLog(self)
        self.finder.found.connect(self._found)
        self.finder.start()

    def _found(self, path: str) -> None:
        self.btn_find.setEnabled(True)
        self.btn_find.setText("Найти сам")
        if path:
            self.ed_log.setText(path)
        else:
            self.btn_find.setText("Не нашёл")

    # -- сохранение --

    def apply_to(self, cfg: dict) -> None:
        log = self.ed_log.text().strip()
        cfg["log_path"] = log
        cfg["game_dir"] = str(Path(log).parent) if log else ""
        cfg["encoding"] = self.cb_enc.currentText()
        cfg["self_name"] = self.ed_self.text().strip()
        cfg["dps_window"] = self.sp_window.value()
        cfg["encounter_timeout"] = self.sp_timeout.value()
        cfg["active_gap"] = self.sp_gap.value()
        cfg["hide_mobs"] = self.ch_mobs.isChecked()
        cfg["merge_pets"] = self.ch_pets.isChecked()
        cfg["metric"] = self.cb_metric.currentData()
        cfg["transparent"] = self.ch_transparent.isChecked()
        cfg["show_loot"] = self.ch_loot.isChecked()
        cfg["columns"] = [k for k, ch in self.col_checks.items() if ch.isChecked()]
        cfg["opacity"] = self.sl_opacity.value() / 100
        cfg["font_size"] = self.sp_font.value()
        cfg["max_rows"] = self.sp_rows.value()
        cfg["click_through"] = self.ch_click.isChecked()
        cfg["always_on_top"] = self.ch_top.isChecked()
        cfg["hotkeys"] = {k: ed.text().strip() for k, ed in self.key_edits.items()}
