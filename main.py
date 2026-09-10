"""Wingbeat — счётчик урона для Aion, читающий Chat.log.

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

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from wingbeat import applog
from wingbeat import config as cfgmod
from wingbeat import updates
from wingbeat.engine import Engine
from wingbeat.overlay import APP_NAME, Overlay, make_icon
from wingbeat.settings_dialog import SettingsDialog
from wingbeat.version import __version__, display as version_display


class App:
    def __init__(self) -> None:
        # Первый запуск определяем ДО load(): тот создаёт файл настроек.
        self.first_run = not cfgmod.config_path().exists()

        applog.setup()
        applog.install_excepthook()

        self.qt = QApplication(sys.argv)
        self.qt.setQuitOnLastWindowClosed(False)
        self.qt.setApplicationName(APP_NAME)
        self.qt.setApplicationVersion(__version__)
        self.icon = make_icon()
        self.qt.setWindowIcon(self.icon)

        self.cfg = cfgmod.load()
        self.engine = Engine(self.cfg)
        self.overlay = Overlay(self.engine, self.cfg,
                               on_settings=self.open_settings, on_quit=self.quit,
                               on_about=self.open_about)
        self.overlay.ensure_on_screen()
        self.overlay.show()
        self.overlay.apply_window_flags()
        self.overlay.setup_hotkeys()

        self._build_tray()
        self._start_or_configure()
        self._warn_hotkeys()
        applog.describe_environment(self.cfg)
        self._greet()
        self._check_updates()

    def _greet(self) -> None:
        """Первый запуск: сказать, что метр живёт в трее, и что найдено.

        Оверлей появляется без рамки и без кнопки в панели задач, поэтому
        человек, не увидевший подсказки, не догадается, где искать настройки.
        """
        if not self.first_run:
            return
        from wingbeat import assets
        where = cfgmod.resolve_log_path(self.cfg)
        if where:
            body = ("Game found, reading Chat.log.\n"
                    "The tray icon has settings, pause and exit.")
        else:
            body = ("Game not found — set the game folder in Settings.\n"
                    "The tray icon sits next to the clock.")
        if assets.root() is None:
            body += "\n\nAsset pack missing: no icons or item names."
        self.tray.showMessage(f"{APP_NAME} {version_display()}", body,
                              QSystemTrayIcon.Information, 9000)

    def _check_updates(self) -> None:
        if not self.cfg.get("check_updates", True):
            return

        def announce(info: dict) -> None:
            # Колбэк приходит из фонового потока: трогать Qt оттуда нельзя,
            # поэтому перебрасываем в главный через однократный таймер.
            QTimer.singleShot(0, lambda: self._show_update(info))

        updates.check_async(announce)

    def _show_update(self, info: dict) -> None:
        applog.log.info("update available: %s (running %s)", info["tag"], __version__)
        self.tray.showMessage(
            APP_NAME, f"Version {info['tag']} is out — download it on the "
            f"releases page.\nYou are running {__version__}.",
            QSystemTrayIcon.Information, 10000)
        self._update_info = info

    def _check_updates_now(self) -> None:
        """Ручная проверка: в отличие от фоновой, отвечает и когда всё свежее."""
        def done(info: dict) -> None:
            QTimer.singleShot(0, lambda: self._show_update(info))

        def run() -> None:
            info = updates.fetch()
            if info is None:
                msg = "Could not check — no connection to GitHub."
            elif updates.is_newer_than_current(info["tag"]):
                done(info)
                return
            else:
                msg = f"You are on the latest version ({__version__})."
            QTimer.singleShot(0, lambda: self.tray.showMessage(
                APP_NAME, msg, QSystemTrayIcon.Information, 6000))

        import threading
        threading.Thread(target=run, name="update-manual", daemon=True).start()

    def _open_log_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(applog.path().parent)))

    # -- трей нужен, потому что при включённом «клик насквозь»
    #    по самому оверлею кликнуть уже нельзя --

    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(self.icon, self.qt)
        self.tray.setToolTip(f"{APP_NAME} {version_display()}")
        menu = QMenu()
        menu.addAction("Show / hide", self.overlay.action_toggle_hide)
        menu.addAction("Start / pause", self.overlay.action_toggle_pause)
        menu.addAction("Reset", self.overlay.action_clear)
        menu.addAction("Click-through", self.overlay.action_toggle_click)
        menu.addAction("Streamer mode", self.overlay.action_toggle_streamer)
        menu.addSeparator()
        menu.addAction("Settings…", self.open_settings)
        menu.addAction("Open log folder", self._open_log_folder)
        menu.addAction("About Wingbeat", self.open_about)
        self.act_update = menu.addAction("Check for updates",
                                         self._check_updates_now)
        menu.addAction("Quit", self.quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: self.overlay.action_toggle_hide()
            if reason == QSystemTrayIcon.Trigger else None)
        self.tray.show()

    def _warn_hotkeys(self) -> None:
        """Занятое сочетание не срабатывает молча — про это надо сказать сразу,
        а не оставлять человека гадать, почему кнопка не работает."""
        failed = self.overlay.hotkeys.failed if self.overlay.hotkeys else []
        if failed:
            self.tray.showMessage(
                "Wingbeat",
                "These hotkeys are already taken by another program and will "
                "not work: " + ", ".join(failed)
                + ". Change them in Settings, or use the buttons in the window.",
                QSystemTrayIcon.Information, 7000)

    # -- запуск --

    def _start_or_configure(self) -> None:
        # Автопоиск — только когда папка НЕ указана. Раньше он запускался
        # всякий раз, когда лог не нашёлся, и молча переписывал в конфиге
        # папку, которую человек выбрал руками: указал свой сервер, вышел
        # из игры, запустил метр — и он уехал на другой клиент, найденный
        # на диске первым.
        if not self.cfg.get("game_dir") and not self.cfg.get("log_path"):
            found = cfgmod.autodetect_log()
            if found:
                self.cfg["log_path"] = ""
                self.cfg["game_dir"] = str(Path(found).parent)
                cfgmod.save(self.cfg)
                applog.log.info("игра найдена сама: %s", self.cfg["game_dir"])
        if not self.engine.start():
            self.tray.showMessage(APP_NAME, self.engine.error,
                                  QSystemTrayIcon.Warning, 5000)
            self.open_settings(first_run=True)

    def open_about(self) -> None:
        """Окно «О программе». Как и настройки — поверх метра."""
        from wingbeat.about_dialog import AboutDialog
        self.overlay.suspend_topmost(True)
        dlg = AboutDialog(parent=self.overlay)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()
        try:
            dlg.exec()
        finally:
            self.overlay.suspend_topmost(False)

    def open_settings(self, first_run: bool = False) -> None:
        # На время настройки клик-сквозь мешает — временно снимаем
        was_click = self.cfg.get("click_through", False)
        if was_click:
            self.cfg["click_through"] = False
            self.overlay.apply_window_flags()

        # Метр висит поверх всех окон и перекрывал бы собственное окно
        # настроек, а таймер раз в две секунды возвращал бы его наверх.
        # На время диалога отпускаем верхний слой и делаем метр родителем:
        # тогда окно настроек гарантированно оказывается над ним.
        self.overlay.suspend_topmost(True)
        dlg = SettingsDialog(self.cfg, self.engine, parent=self.overlay)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()
        try:
            accepted = dlg.exec()
        finally:
            self.overlay.suspend_topmost(False)
        if accepted:
            self.engine.close_session()
            self.engine.stop()
            dlg.apply_to(self.cfg)
            cfgmod.save(self.cfg)
            self.overlay.apply_appearance()   # прозрачность меняет нативное окно

            self.engine = Engine(self.cfg)
            self.overlay.engine = self.engine
            if not self.engine.start():
                QMessageBox.warning(dlg, APP_NAME, self.engine.error)
            elif self.overlay.hotkeys and self.overlay.hotkeys.failed:
                self.tray.showMessage(
                    "Wingbeat",
                    "Could not register hotkeys: "
                    + ", ".join(self.overlay.hotkeys.failed)
                    + ". They are most likely taken by another program.",
                    QSystemTrayIcon.Information, 6000)
        else:
            self.cfg["click_through"] = was_click
            self.overlay.apply_window_flags()

        if first_run and not cfgmod.resolve_log_path(self.cfg):
            self.tray.showMessage(
                APP_NAME, "Without a path to Chat.log the meter cannot run.",
                QSystemTrayIcon.Warning, 5000)

    def quit(self) -> None:
        self.overlay._store_geometry()
        cfgmod.save(self.cfg)
        if self.overlay.hotkeys:
            self.overlay.hotkeys.unregister_all()
        # Сессия закрывается кнопкой «Очистить», но выход — это тоже её
        # конец: иначе вечерний фарм пропадал бы весь целиком.
        self.engine.close_session()
        self.engine.stop()
        self.tray.hide()
        self.qt.quit()

    def run(self) -> int:
        return self.qt.exec()


if __name__ == "__main__":
    raise SystemExit(App().run())
