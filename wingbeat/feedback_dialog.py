"""Форма обратной связи: что сломалось, подробности, снимок экрана.

Сообщение уходит на сервер проекта, а тот уже передаёт его автору. Метр
не знает никаких ключей от мессенджера: программа расходится по чужим
машинам, и любой ключ внутри неё немедленно перестал бы быть ключом.
"""

from __future__ import annotations

import base64
import json
import platform
import threading
import urllib.error
import urllib.request
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QDialog, QFileDialog, QHBoxLayout, QLabel,
                               QLineEdit, QMessageBox, QPlainTextEdit,
                               QPushButton, QVBoxLayout)

from .version import __version__

#: Снимок экрана крупнее этого Telegram не примет, да и незачем.
MAX_SHOT = 3 * 1024 * 1024

STYLE = """
QDialog, QWidget { background:#161b21; color:#dfe6e8; font-family:'Bahnschrift','Segoe UI'; font-size:13px; }
QLabel[role="title"] { font-size:19px; font-weight:600; color:#eda549; letter-spacing:1px; }
QLabel[role="sub"] { color:#8a97a0; font-size:12px; }
QLabel[role="hint"] { color:#8a97a0; font-size:12px; }
QLineEdit, QPlainTextEdit { background:#10151a; border:1px solid #2a343c; border-radius:3px; padding:6px 8px; selection-background-color:#3a4a58; }
QLineEdit:focus, QPlainTextEdit:focus { border-color:#4a5a68; }
QPushButton { background:#222c35; border:1px solid #33414c; border-radius:3px; padding:6px 14px; }
QPushButton:hover { background:#2b3844; }
QPushButton:disabled { color:#5b6670; border-color:#232c34; }
QPushButton[role="send"] { background:#2d3b2a; border-color:#42583c; }
QPushButton[role="send"]:hover { background:#36472f; }
"""


class FeedbackDialog(QDialog):
    #: Ответ сервера приходит из фонового потока. Сигнал — единственный
    #: законный способ вернуться в главный: QTimer.singleShot, заведённый
    #: в обычном threading.Thread, не сработает никогда — у такого потока
    #: нет цикла событий Qt, и таймер просто некому обслужить.
    replied = Signal(bool, str)

    def __init__(self, cfg: dict, parent=None):
        super().__init__(parent)
        self.replied.connect(self._done)
        self.cfg = cfg
        self.shot_path: Path | None = None

        self.setWindowTitle("Send feedback")
        self.setStyleSheet(STYLE)
        self.setMinimumWidth(520)
        # Метр висит поверх всех окон — его собственные окна обязаны быть
        # выше, иначе открываются под ним и их не найти.
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(10)

        title = QLabel("Send feedback")
        title.setProperty("role", "title")
        root.addWidget(title)

        sub = QLabel("Goes straight to the author. Write in English or Russian.")
        sub.setProperty("role", "sub")
        root.addWidget(sub)

        root.addSpacing(6)
        root.addWidget(self._label("What went wrong"))
        self.subject = QLineEdit()
        self.subject.setPlaceholderText("Short summary — one line")
        self.subject.setMaxLength(300)
        root.addWidget(self.subject)

        root.addWidget(self._label("Details"))
        self.body = QPlainTextEdit()
        self.body.setPlaceholderText(
            "What you did, what you expected, what happened instead.\n"
            "The more precise, the faster it gets fixed.")
        self.body.setMinimumHeight(170)
        root.addWidget(self.body)

        shot_row = QHBoxLayout()
        self.attach_btn = QPushButton("Attach screenshot…")
        self.attach_btn.clicked.connect(self.pick_shot)
        shot_row.addWidget(self.attach_btn)
        self.shot_label = QLabel("no file attached")
        self.shot_label.setProperty("role", "hint")
        shot_row.addWidget(self.shot_label, 1)
        self.clear_btn = QPushButton("Remove")
        self.clear_btn.clicked.connect(self.clear_shot)
        self.clear_btn.setEnabled(False)
        shot_row.addWidget(self.clear_btn)
        root.addLayout(shot_row)

        self.status = QLabel("")
        self.status.setProperty("role", "hint")
        self.status.setWordWrap(True)
        root.addWidget(self.status)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(self.cancel_btn)
        self.send_btn = QPushButton("Send")
        self.send_btn.setProperty("role", "send")
        self.send_btn.setDefault(True)
        self.send_btn.clicked.connect(self.send)
        buttons.addWidget(self.send_btn)
        root.addLayout(buttons)

    # -- мелочи --

    def _label(self, text: str) -> QLabel:
        lab = QLabel(text)
        lab.setProperty("role", "sub")
        return lab

    def pick_shot(self) -> None:
        name, _ = QFileDialog.getOpenFileName(
            self, "Pick a screenshot", "",
            "Images (*.png *.jpg *.jpeg *.gif *.bmp);;All files (*)")
        if not name:
            return
        path = Path(name)
        size = path.stat().st_size
        if size > MAX_SHOT:
            QMessageBox.warning(
                self, "Too large",
                f"That file is {size / 1024 / 1024:.1f} MB. "
                f"The limit is {MAX_SHOT // 1024 // 1024} MB — "
                "crop it or save as JPEG.")
            return
        self.shot_path = path
        self.shot_label.setText(f"{path.name} · {size / 1024:.0f} KB")
        self.clear_btn.setEnabled(True)

    def clear_shot(self) -> None:
        self.shot_path = None
        self.shot_label.setText("no file attached")
        self.clear_btn.setEnabled(False)

    # -- отправка --

    def send(self) -> None:
        subject = self.subject.text().strip()
        body = self.body.toPlainText().strip()
        if not subject:
            self.status.setText("Fill in the first line — what went wrong.")
            self.subject.setFocus()
            return
        if not body:
            self.status.setText("Add some details, or there is nothing to act on.")
            self.body.setFocus()
            return

        shot = ""
        shot_name = ""
        if self.shot_path is not None:
            try:
                shot = base64.b64encode(self.shot_path.read_bytes()).decode()
                shot_name = self.shot_path.name
            except OSError as exc:
                self.status.setText(f"Could not read the screenshot: {exc}")
                return

        payload = {
            "title": subject,
            "body": body,
            "version": __version__,
            "reporter": (self.cfg.get("self_name") or "")[:32],
            "system": f"{platform.system()} {platform.release()}",
            "shot": shot,
            "shot_name": shot_name,
        }

        self._busy(True)
        self.status.setText("Sending…")

        def work() -> None:
            ok, message = post(self.cfg, payload)
            # Трогать окно из чужого потока нельзя, поэтому наружу уходит
            # только сигнал — Qt доставит его в главный поток сам.
            self.replied.emit(ok, message)

        threading.Thread(target=work, name="feedback", daemon=True).start()

    def _busy(self, busy: bool) -> None:
        for w in (self.send_btn, self.attach_btn, self.subject, self.body,
                  self.clear_btn):
            w.setEnabled(not busy)
        if not busy:
            self.clear_btn.setEnabled(self.shot_path is not None)

    def _done(self, ok: bool, message: str) -> None:
        if ok:
            QMessageBox.information(
                self, "Thank you",
                "Your message has been sent.\n\n"
                "If it was a bug, the log folder often has the missing half of "
                "the story — the About window has a button that opens it.")
            self.accept()
            return
        self._busy(False)
        self.status.setText(message)


def post(cfg: dict, payload: dict) -> tuple[bool, str]:
    """Отправка. Возвращает (успех, что сказать человеку)."""
    base = (cfg.get("api_url") or "https://wingbeat.fun/api").rstrip("/")
    req = urllib.request.Request(
        f"{base}/v1/feedback",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "User-Agent": f"Wingbeat/{__version__}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            json.loads(r.read().decode())
        return True, ""
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            return False, ("Too many messages from you in the last hour. "
                           "Try again later.")
        if exc.code == 503:
            return False, "Feedback is not set up on the server side yet."
        return False, f"The server refused the message (code {exc.code})."
    except urllib.error.URLError as exc:
        return False, f"No connection: {exc.reason}"
    except Exception as exc:                       # noqa: BLE001
        return False, f"Could not send: {exc}"
