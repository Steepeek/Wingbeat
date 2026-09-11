"""Окно «О программе».

Зачем оно есть. Метр раздаётся файлом, без установщика из магазина и без
страницы, куда можно посмотреть: человек, которому его прислали в чате,
не знает ни кто автор, ни для какого сервера он сделан, ни куда писать,
если что-то не так. Всё это должно быть в самой программе, в одном месте
и в один клик.

Ссылки и форма обратной связи пока пустые: репозиторий ещё не опубликован.
Как только адреса появятся, их достаточно вписать в LINKS — окно само
покажет ровно те строки, у которых адрес заполнен.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFrame, QHBoxLayout,
                               QLabel, QPushButton, QVBoxLayout, QWidget)

from . import applog
from . import assets
from . import sessions as sessmod
from .version import display as version_display

#: Внешние адреса. Пустая строка = кнопку не показываем.
LINKS = (
    ("GitHub", "https://github.com/Steepeek/Wingbeat"),
    ("Website", "https://wingbeat.fun"),
    ("Discord", ""),
    ("Feedback", ""),
)

STYLE = """
QDialog, QWidget { background:#161b21; color:#dfe6e8; font-family:'Bahnschrift','Segoe UI'; font-size:13px; }
QLabel[role="title"] { font-size:23px; font-weight:600; color:#eda549; letter-spacing:1px; }
QLabel[role="sub"] { color:#8a97a0; font-size:12px; }
QLabel[role="body"] { color:#dfe6e8; font-size:13px; }
QFrame[role="rule"] { background:#2a343c; max-height:1px; border:none; }
QPushButton { background:#222c35; border:1px solid #33414c; border-radius:3px; padding:6px 14px; }
QPushButton:hover { background:#2b3844; }
QPushButton:disabled { color:#5b6670; border-color:#232c34; }
"""


class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("About Wingbeat")
        self.setStyleSheet(STYLE)
        self.setMinimumWidth(460)
        # Метр висит поверх всех окон, поэтому его собственные окна обязаны
        # быть выше — иначе они открываются под ним, и их не видно.
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(12)

        root.addLayout(self._header())
        root.addWidget(self._rule())

        root.addWidget(self._label(
            "Created by Steepeek specifically for Aion Origin.", "body"))
        root.addWidget(self._label(
            "Wingbeat reads the Chat.log file your client already writes and "
            "shows damage, healing, damage taken, loot and saved sessions on "
            "top of the game. It does not read game memory, inject anything "
            "into the client or modify any game file.", "sub"))
        root.addWidget(self._label(
            "It is built for Origin and refuses to run on other servers: the "
            "combat lines there differ, and wrong numbers are worse than none.",
            "sub"))

        links = self._links()
        if links is not None:
            root.addWidget(self._rule())
            root.addWidget(links)

        root.addWidget(self._rule())
        root.addLayout(self._folders())

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.button(QDialogButtonBox.Close).setText("Close")
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    # -- части окна --

    def _header(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(14)
        icon = QLabel()
        path = assets.ui_icon_path("appicon")
        if path:
            pm = QPixmap(str(path))
            if not pm.isNull():
                icon.setPixmap(pm.scaled(64, 64, Qt.KeepAspectRatio,
                                         Qt.SmoothTransformation))
        row.addWidget(icon, 0, Qt.AlignTop)

        text = QVBoxLayout()
        text.setSpacing(2)
        text.addWidget(self._label("Wingbeat", "title"))
        text.addWidget(self._label(f"version {version_display()}", "sub"))
        row.addLayout(text, 1)
        return row

    def _links(self) -> QWidget | None:
        """Кнопки внешних адресов. Пока их нет — раздела тоже нет."""
        live = [(name, url) for name, url in LINKS if url]
        if not live:
            return None
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        for name, url in live:
            btn = QPushButton(name)
            btn.clicked.connect(lambda _c, u=url: QDesktopServices.openUrl(QUrl(u)))
            row.addWidget(btn)
        row.addStretch(1)
        return holder

    def _folders(self) -> QHBoxLayout:
        """Папки, которые просят приложить к сообщению о проблеме."""
        row = QHBoxLayout()
        row.setSpacing(8)
        fb_btn = QPushButton("Send feedback")
        fb_btn.clicked.connect(self.open_feedback)
        log_btn = QPushButton("Open log folder")
        log_btn.clicked.connect(
            lambda: self._open(applog.path().parent))
        sess_btn = QPushButton("Open sessions folder")
        sess_btn.clicked.connect(lambda: self._open(sessmod.sessions_dir()))
        row.addWidget(fb_btn)
        row.addWidget(log_btn)
        row.addWidget(sess_btn)
        row.addStretch(1)
        return row

    def open_feedback(self) -> None:
        """Форма обратной связи поверх этого окна."""
        from .config import load as load_cfg
        from .feedback_dialog import FeedbackDialog

        dlg = FeedbackDialog(load_cfg(), parent=self)
        dlg.exec()

    # -- мелочи --

    def _label(self, text: str, role: str) -> QLabel:
        lab = QLabel(text)
        lab.setProperty("role", role)
        lab.setWordWrap(True)
        lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
        return lab

    def _rule(self) -> QFrame:
        line = QFrame()
        line.setProperty("role", "rule")
        line.setFrameShape(QFrame.HLine)
        line.setFixedHeight(1)
        return line

    def _open(self, path: Path) -> None:
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
