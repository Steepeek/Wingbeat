"""Связка: хвост лога -> парсер -> агрегатор. Работает в фоновом потоке."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from . import config as cfgmod
from .aggregate import Meter
from .parser import RE_GLORY, RE_OWN_CHAT, iter_records, parse
from .tailer import Tailer
from . import skilldb


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
        self.paused = False

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

        self.meter.skill_class = skilldb.ensure(self.cfg.get("game_dir", ""))

        if not self.cfg.get("self_name"):
            found = self._detect_self_name(path)
            if found:
                self.meter.self_name = found

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

    def set_paused(self, paused: bool) -> None:
        """Пауза. Строки продолжаем вычитывать, но не считаем — иначе после
        снятия паузы всё накопленное разом свалится в таблицу."""
        self.paused = paused
        self._publish()

    def reset(self) -> None:
        with self._lock:
            self.meter.reset()
        # Пересобираем снимок сразу: иначе до следующего тика в окне висят
        # старые цифры и кнопка выглядит как ненажатая.
        self._publish()

    def _publish(self) -> None:
        with self._lock:
            snap = self.meter.snapshot()
        snap["stats"] = {
            "read": self.read_lines,
            "parsed": self.parsed,
            "unknown": self.unknown,
            "rotations": self.tailer.rotations if self.tailer else 0,
        }
        snap["error"] = self.error
        snap["paused"] = self.paused
        self._snapshot = snap

    def _detect_self_name(self, path: str, tail_bytes: int = 4 * 1024 * 1024) -> str:
        """Свой ник из хвоста лога.

        В боевых строках персонаж всегда «You», поэтому ник берём оттуда, где
        он есть: собственная реплика в чате идёт БЕЗ обёртки [charname:]
        (у чужих она всегда есть), плюс строка про Glory Points при входе.
        Ждать, пока человек что-нибудь напишет, незачем — смотрим сразу.
        """
        try:
            with open(path, "rb") as f:
                size = f.seek(0, 2)
                f.seek(max(0, size - tail_bytes))
                chunk = f.read()
        except OSError:
            return ""
        lines = chunk.decode(self.encoding, "replace").split("\r\n")

        from collections import Counter
        own = Counter()
        for ts, body in iter_records(lines):
            m = RE_GLORY.match(body)
            if m:
                return m["me"]            # однозначно: строка при входе в игру
            m = RE_OWN_CHAT.match(body)
            if m:
                own[m["me"]] += 1
        # Реплика без обёртки [charname:] — признак слабый, у чужих такие
        # строки тоже бывают. Берём имя, только если оно явно преобладает.
        top = own.most_common(2)
        if top and top[0][1] >= 3 and (len(top) == 1 or top[0][1] >= 2 * top[1][1]):
            return top[0][0]
        return ""

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
        if raw_lines and self.paused:
            # позицию в файле держим актуальной, но события не считаем
            self.read_lines += len(raw_lines)
            raw_lines = []
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
        self._publish()

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
    meter.skill_class = skilldb.ensure(cfg.get("game_dir", ""))
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
