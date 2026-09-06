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

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from aionmeter import applog
from aionmeter import config as cfgmod
from aionmeter import updates
from aionmeter.engine import Engine
from aionmeter.overlay import Overlay, make_icon
from aionmeter.settings_dialog import SettingsDialog
from aionmeter.version import __version__


class App:
    def __init__(self) -> None:
        # Первый запуск определяем ДО load(): тот создаёт файл настроек.
        self.first_run = not cfgmod.config_path().exists()

        applog.setup()
        applog.install_excepthook()

        self.qt = QApplication(sys.argv)
        self.qt.setQuitOnLastWindowClosed(False)
        self.qt.setApplicationName("AionMeter")
        self.qt.setApplicationVersion(__version__)
        self.icon = make_icon()
        self.qt.setWindowIcon(self.icon)

        self.cfg = cfgmod.load()
        self.engine = Engine(self.cfg)
        self.overlay = Overlay(self.engine, self.cfg,
                               on_settings=self.open_settings, on_quit=self.quit)
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
        from aionmeter import assets
        where = cfgmod.resolve_log_path(self.cfg)
        if where:
            body = ("Клиент найден, читаю Chat.log.\n"
                    "Значок в трее — настройки, пауза, выход.")
        else:
            body = ("Клиент не нашёлся — укажите папку игры в настройках.\n"
                    "Значок метра в трее, рядом с часами.")
        if assets.root() is None:
            body += "\n\nАссет-пак не найден: иконок и названий не будет."
        self.tray.showMessage(f"AionMeter {__version__}", body,
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
        applog.log.info("доступна версия %s (у нас %s)", info["tag"], __version__)
        self.tray.showMessage(
            "AionMeter", f"Вышла версия {info['tag']} — "
            f"скачать можно на странице релизов.\nСейчас установлена {__version__}.",
            QSystemTrayIcon.Information, 10000)
        self._update_info = info

    def _check_updates_now(self) -> None:
        """Ручная проверка: в отличие от фоновой, отвечает и когда всё свежее."""
        def done(info: dict) -> None:
            QTimer.singleShot(0, lambda: self._show_update(info))

        def run() -> None:
            info = updates.fetch()
            if info is None:
                msg = "Не удалось проверить — нет связи с GitHub."
            elif updates.is_newer_than_current(info["tag"]):
                done(info)
                return
            else:
                msg = f"Установлена последняя версия ({__version__})."
            QTimer.singleShot(0, lambda: self.tray.showMessage(
                "AionMeter", msg, QSystemTrayIcon.Information, 6000))

        import threading
        threading.Thread(target=run, name="update-manual", daemon=True).start()

    def _open_log_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(applog.path().parent)))

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
        menu.addAction("Настройки…", self.open_settings)
        menu.addAction("Папка с логом программы", self._open_log_folder)
        self.act_update = menu.addAction("Проверить обновления",
                                         self._check_updates_now)
        menu.addAction("Выход", self.quit)
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
        self.tray.hide()
        self.qt.quit()

    def run(self) -> int:
        return self.qt.exec()


if __name__ == "__main__":
    raise SystemExit(App().run())
