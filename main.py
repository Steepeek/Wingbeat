"""AionMeter — счётчик урона для Aion, читающий Chat.log.

    py main.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# До создания QApplication. Freetype убирает цветную субпиксельную бахрому
# на тёмном фоне (замер: 771 цветной пиксель -> 0) и ускоряет отрисовку
# текста почти вдвое. Segoe UI Variable с ним несовместим, но обычный
# Segoe UI и так рисует табличные цифры.
os.environ.setdefault("QT_QPA_PLATFORM", "windows:fontengine=freetype")

sys.path.insert(0, str(Path(__file__).parent))

from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from aionmeter import config as cfgmod
from aionmeter.engine import Engine
from aionmeter.loot_window import LootWindow
from aionmeter.overlay import Overlay, make_icon
from aionmeter.settings_dialog import SettingsDialog


class App:
    def __init__(self) -> None:
        self.qt = QApplication(sys.argv)
        self.qt.setQuitOnLastWindowClosed(False)
        self.qt.setApplicationName("AionMeter")
        self.icon = make_icon()
        self.qt.setWindowIcon(self.icon)

        self.cfg = cfgmod.load()
        self.engine = Engine(self.cfg)
        self.loot = LootWindow(self.engine, self.cfg)
        self.overlay = Overlay(self.engine, self.cfg,
                               on_settings=self.open_settings, on_quit=self.quit,
                               on_loot=self.open_loot)
        self.overlay.ensure_on_screen()
        self.overlay.show()
        self.overlay.apply_window_flags()
        self.overlay.setup_hotkeys()

        self._build_tray()
        self._start_or_configure()
        self._warn_hotkeys()

    # -- трей нужен, потому что при включённом «клик насквозь»
    #    по самому оверлею кликнуть уже нельзя --

    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(self.icon, self.qt)
        self.tray.setToolTip("AionMeter")
        menu = QMenu()
        menu.addAction("Показать / скрыть", self.overlay.action_toggle_hide)
        menu.addAction("Старт / стоп", self.overlay.action_toggle_pause)
        menu.addAction("Очистить", self.overlay.action_clear)
        menu.addAction("Клик насквозь", self.overlay.action_toggle_click)
        menu.addSeparator()
        menu.addAction("Добыча…", self.open_loot)
        menu.addAction("Настройки…", self.open_settings)
        menu.addAction("Выход", self.quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: self.overlay.action_toggle_hide()
            if reason == QSystemTrayIcon.Trigger else None)
        self.tray.show()

    def open_loot(self) -> None:
        self.loot.engine = self.engine
        self.loot.show()
        self.loot.raise_()
        self.loot.activateWindow()
        if not hasattr(self, "_loot_timer"):
            from PySide6.QtCore import QTimer
            self._loot_timer = QTimer(self.qt)
            self._loot_timer.timeout.connect(
                lambda: self.loot.isVisible() and self.loot.update())
            self._loot_timer.start(1000)

    def _warn_hotkeys(self) -> None:
        """Занятое сочетание не срабатывает молча — про это надо сказать сразу,
        а не оставлять человека гадать, почему кнопка не работает."""
        failed = self.overlay.hotkeys.failed if self.overlay.hotkeys else []
        if failed:
            self.tray.showMessage(
                "AionMeter",
                "Сочетания уже заняты другой программой и работать не будут: "
                + ", ".join(failed)
                + ". Поменяйте их в настройках — или пользуйтесь кнопками в шапке.",
                QSystemTrayIcon.Information, 7000)

    # -- запуск --

    def _start_or_configure(self) -> None:
        if not cfgmod.resolve_log_path(self.cfg):
            found = cfgmod.autodetect_log()
            if found:
                self.cfg["log_path"] = found
                self.cfg["game_dir"] = str(Path(found).parent)
                cfgmod.save(self.cfg)
        if not self.engine.start():
            self.tray.showMessage("AionMeter", self.engine.error,
                                  QSystemTrayIcon.Warning, 5000)
            self.open_settings(first_run=True)

    def open_settings(self, first_run: bool = False) -> None:
        # На время настройки клик-сквозь мешает — временно снимаем
        was_click = self.cfg.get("click_through", False)
        if was_click:
            self.cfg["click_through"] = False
            self.overlay.apply_window_flags()

        dlg = SettingsDialog(self.cfg, self.engine)
        if dlg.exec():
            self.engine.stop()
            dlg.apply_to(self.cfg)
            cfgmod.save(self.cfg)
            self.overlay.apply_appearance()   # прозрачность меняет нативное окно

            self.engine = Engine(self.cfg)
            self.overlay.engine = self.engine
            if not self.engine.start():
                QMessageBox.warning(dlg, "AionMeter", self.engine.error)
            elif self.overlay.hotkeys and self.overlay.hotkeys.failed:
                self.tray.showMessage(
                    "AionMeter",
                    "Не удалось занять сочетания: "
                    + ", ".join(self.overlay.hotkeys.failed)
                    + ". Скорее всего они уже заняты другой программой.",
                    QSystemTrayIcon.Information, 6000)
        else:
            self.cfg["click_through"] = was_click
            self.overlay.apply_window_flags()

        if first_run and not cfgmod.resolve_log_path(self.cfg):
            self.tray.showMessage(
                "AionMeter", "Без пути к Chat.log метр работать не будет.",
                QSystemTrayIcon.Warning, 5000)

    def quit(self) -> None:
        self.overlay._store_geometry()
        cfgmod.save(self.cfg)
        if self.overlay.hotkeys:
            self.overlay.hotkeys.unregister_all()
        self.engine.stop()
        self.loot.close()
        self.tray.hide()
        self.qt.quit()

    def run(self) -> int:
        return self.qt.exec()


if __name__ == "__main__":
    raise SystemExit(App().run())
