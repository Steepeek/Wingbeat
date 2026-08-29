"""Связка: хвост лога -> парсер -> агрегатор. Работает в фоновом потоке."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from . import config as cfgmod
from .aggregate import Meter
from .parser import iter_records, parse
from .tailer import Tailer


class Engine:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.meter = Meter(cfg)
        self.tailer: Tailer | None = None
        self.encoding = "cp1251"
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._snapshot: dict = {"rows": [], "total": 0, "duration": 0, "active": False,
                                "stats": {}, "window": cfg["dps_window"], "rows_note": ""}
        self.error = ""
        self.read_lines = 0
        self.parsed = 0
        self.unknown = 0
        self._unknown_fh = None
        self._pending = ""     # незакрытая логическая запись между чтениями

    # -- управление --

    def start(self) -> bool:
        path = cfgmod.resolve_log_path(self.cfg)
        if not path or not Path(path).is_file():
            self.error = "Не найден Chat.log — укажите папку игры в настройках"
            return False
        try:
            self.tailer = Tailer(path, backfill_bytes=self.cfg["backfill_kb"] * 1024)
        except OSError as e:
            self.error = f"Не удалось открыть лог: {e}"
            return False

        enc = self.cfg.get("encoding", "auto")
        if enc == "auto":
            try:
                with open(path, "rb") as f:
                    f.seek(max(0, Path(path).stat().st_size - 65536))
                    enc = cfgmod.detect_encoding(f.read())
            except OSError:
                enc = "cp1251"
        self.encoding = enc

        self.error = ""
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="AionMeter-read", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        if self.tailer is not None:
            self.tailer.close()
        if self._unknown_fh is not None:
            self._unknown_fh.close()
            self._unknown_fh = None

    def reset(self) -> None:
        with self._lock:
            self.meter.reset()

    # -- фоновый цикл --

    def _loop(self) -> None:
        interval = max(0.05, self.cfg.get("poll_ms", 250) / 1000)
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as e:                      # noqa: BLE001 - поток не должен падать
                self.error = f"{type(e).__name__}: {e}"
            self._stop.wait(interval)

    def _tick(self) -> None:
        if self.tailer is None:
            return
        raw_lines = self.tailer.read()
        if raw_lines:
            self.read_lines += len(raw_lines)
            text_lines = [b.decode(self.encoding, "replace") for b in raw_lines]
            with self._lock:
                for ts, body in iter_records(text_lines):
                    ev = parse(ts, body)
                    if ev is None:
                        self.unknown += 1
                        self._log_unknown(body)
                    else:
                        self.parsed += 1
                        self.meter.feed(ev)
        with self._lock:
            snap = self.meter.snapshot()
        snap["stats"] = {
            "read": self.read_lines,
            "parsed": self.parsed,
            "unknown": self.unknown,
            "rotations": self.tailer.rotations if self.tailer else 0,
        }
        snap["error"] = self.error
        self._snapshot = snap

    def _log_unknown(self, body: str) -> None:
        """Нераспознанные строки — единственный способ заметить, что сервер
        сменил шаблоны, до того как метр начнёт молча показывать нули."""
        if self.unknown > 5000:
            return
        try:
            if self._unknown_fh is None:
                d = cfgmod.config_dir()
                d.mkdir(parents=True, exist_ok=True)
                self._unknown_fh = open(d / "unknown.log", "a", encoding="utf-8")
            self._unknown_fh.write(body + "\n")
            if self.unknown % 50 == 0:
                self._unknown_fh.flush()
        except OSError:
            self._unknown_fh = None

    # -- чтение снаружи --

    def snapshot(self) -> dict:
        return self._snapshot

    def health(self) -> str:
        total = self.parsed + self.unknown
        if not total:
            return "нет данных"
        return f"{self.parsed}/{total}"


def load_history(cfg: dict, on_progress=None) -> Meter:
    """Разбирает лог целиком — для проверки и офлайн-отчётов."""
    path = cfgmod.resolve_log_path(cfg)
    raw = Path(path).read_bytes()
    enc = cfg.get("encoding", "auto")
    if enc == "auto":
        enc = cfgmod.detect_encoding(raw[-65536:])
    meter = Meter(cfg)
    lines = raw.decode(enc, "replace").split("\r\n")
    n = 0
    for ts, body in iter_records(lines):
        ev = parse(ts, body)
        if ev is not None:
            meter.feed(ev)
        n += 1
        if on_progress and n % 20000 == 0:
            on_progress(n)
    return meter
