"""Связка: хвост лога -> парсер -> агрегатор. Работает в фоновом потоке."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import sys

from . import applog
from . import config as cfgmod
from . import sessions
from .aggregate import Meter
from .parser import RE_GLORY, RE_OWN_CHAT, iter_records, parse
from .tailer import Tailer
from .version import __version__
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
                                "stats": {}, "window": cfg["dps_window"],
                                "rows_note": "", "error": "", "paused": False}
        self.error = ""
        self.read_lines = 0
        self.parsed = 0
        self.unknown = 0
        self._unknown_fh = None
        self.paused = False
        #: Путь к файлу последней сохранённой сессии — для окна и для лога.
        self.saved_session = None
        #: Виды сбоев, о которых уже написали в лог: по одному разу на вид.
        self._logged: set[str] = set()
        #: Путь, которого пока нет: ждём, когда игра создаст файл.
        self._waiting = ""
        self._waiting_at = 0.0
        #: Когда снимок публиковался в последний раз (монотонные секунды).
        self._published_at = 0.0

    # -- управление --

    def start(self) -> bool:
        path = cfgmod.resolve_log_path(self.cfg)
        game_dir = self.cfg.get("game_dir", "")
        if not path and not game_dir:
            self.error = "No game folder set — open Settings"
            self._publish()          # иначе окно покажет «waiting for combat…»
            return False
        if not path:
            # Папка указана, а Chat.log в ней ещё нет. Это НЕ повод
            # сдаваться: обычный порядок у человека — сначала метр (он же
            # в автозапуске), потом игра, и файл появляется после входа.
            # Ждём именно ПАПКУ: пути к файлу тут ещё не существует, и
            # искать его надо заново на каждой попытке.
            self._waiting = game_dir
            self.error = ""
            self._start_thread()
            self._publish()
            return True
        return self._open(path)

    #: Сколько дочитывать у файла, которого ждали. Лог, появившийся при нас,
    #: весь целиком относится к текущему заходу, и начинать его чтение с
    #: конца значило бы выбросить начало боя. Потолок — чтобы случайно
    #: подсунутый старый файл на сотни мегабайт не читался минуту.
    WAIT_BACKFILL = 4 * 1024 * 1024

    #: Текст отказа. Английский, как и весь интерфейс.
    NOT_ORIGIN = ("This build reads Aion Origin clients only — "
                  "the combat lines of other servers differ.")

    def _origin_ok(self, path: str) -> bool:
        """Клиент опознан как Origin (или проверка выключена).

        Смотрим на папку игры, а не на путь к логу: лог — обычный текст,
        по нему сервер не определить. Признак — собственное шифрование
        клиентских паков, которого нет ни у одного другого сервера.
        """
        if not self.cfg.get("require_origin", True):
            return True
        game_dir = self.cfg.get("game_dir") or str(Path(path).parent)
        if cfgmod.is_origin_client(game_dir):
            return True
        # Лаунчер Origin знает свою папку. Но засчитываем это, ТОЛЬКО если
        # читаемый лог лежит внутри неё: иначе достаточно было бы иметь
        # лаунчер на диске, чтобы метр принял лог любого другого сервера.
        launcher = cfgmod.origin_launcher_dir()
        if not launcher or not cfgmod.is_origin_client(launcher):
            return False
        try:
            Path(path).resolve().relative_to(Path(launcher).resolve())
        except (ValueError, OSError):
            return False
        return True

    def _open(self, path: str, from_start: bool = False) -> bool:
        """Открыть лог и запустить чтение. Общая часть старта и ожидания."""
        if not self._origin_ok(path):
            self.error = self.NOT_ORIGIN
            applog.log.warning("клиент не опознан как Origin: %s",
                               self.cfg.get("game_dir") or path)
            self._publish()
            return False
        backfill = self.cfg["backfill_kb"] * 1024
        if from_start:
            try:
                backfill = max(backfill, min(Path(path).stat().st_size,
                                             self.WAIT_BACKFILL))
            except OSError:
                pass
        try:
            self.tailer = Tailer(path, backfill_bytes=backfill)
        except OSError as e:
            self.error = f"Cannot open the log: {e}"
            applog.log.warning("не удалось открыть %s: %s", path, e)
            self._publish()
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
        self._waiting = ""
        self._warm_assets()
        applog.log.info("reading %s (encoding %s, character %s)", path,
                        self.encoding, self.meter.self_name or "unknown")
        self._start_thread()
        return True

    def _warm_assets(self) -> None:
        """Прогреть тяжёлые таблицы ассетов в фоне.

        Обратный указатель «название предмета -> иконка» строится из двух
        распакованных таблиц и занимает около 120 мс. Раньше это случалось
        в момент отрисовки — при первом же применении зелья окно замирало.
        """
        def run() -> None:
            try:
                from . import assets
                assets.item_icon_by_name("")
            except Exception:                       # noqa: BLE001
                pass

        threading.Thread(target=run, name="AionMeter-assets", daemon=True).start()

    def _start_thread(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="AionMeter-read",
                                        daemon=True)
        self._thread.start()

    def _try_open_waiting(self) -> None:
        """Пока лога нет, раз в пару секунд проверяем, не появился ли он."""
        now = time.monotonic()
        if now - self._waiting_at < 2.0:
            return
        self._waiting_at = now
        path = cfgmod.resolve_log_path(self.cfg)
        if not path and self._waiting:
            # В _waiting лежит ПАПКА игры: файла ещё нет, и путь к нему
            # приходится собирать заново каждый раз.
            path = cfgmod.find_log_in(self._waiting)
        if path and Path(path).is_file():
            applog.log.info("Chat.log appeared: %s", path)
            self._open(path, from_start=True)

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
        """Кнопка «Очистить» — она же конец сессии.

        Сессия начинается с первого удара и живёт до этого момента, поэтому
        закрывать её больше негде: бои метр режет сам, а «сессия» — это то,
        что человек считает одним заходом, и знает об этом только он.
        """
        with self._lock:
            data = self.meter.export()
            self.meter.reset()
        if data:
            self.saved_session = sessions.save(data, self.cfg)
        # Пересобираем снимок сразу: иначе до следующего тика в окне висят
        # старые цифры и кнопка выглядит как ненажатая.
        self._publish()

    def republish(self) -> None:
        """Пересобрать снимок сейчас же — после смены настройки показа.

        Снимок строится раз в тик, и без этого переключатель в меню
        отзывался бы с задержкой в четверть секунды: нажал — ничего не
        произошло — нажал ещё раз.
        """
        self._publish()

    def close_session(self) -> None:
        """Сохранить незакрытую сессию, ничего не обнуляя. Для выхода."""
        with self._lock:
            data = self.meter.export()
        if data:
            self.saved_session = sessions.save(data, self.cfg)

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
        # Что именно читаем и насколько свежий файл. Без этого «старый
        # Chat.log из прошлого года» выглядел в окне ровно так же, как
        # рабочий метр в мирное время: пустая таблица и «waiting for
        # combat…», и понять разницу было неоткуда.
        snap["log_path"] = self.tailer.path if self.tailer else (
            self._waiting or cfgmod.resolve_log_path(self.cfg))
        snap["log_age"] = self._log_age()
        snap["waiting"] = bool(self._waiting)
        self._snapshot = snap

    def _log_age(self) -> float:
        """Сколько секунд назад файл лога менялся. -1, если неизвестно."""
        path = self.tailer.path if self.tailer else ""
        if not path:
            return -1.0
        try:
            return max(0.0, time.time() - Path(path).stat().st_mtime)
        except OSError:
            return -1.0

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
        glory = ""
        for ts, body in iter_records(lines):
            m = RE_GLORY.match(body)
            if m:
                # Именно ПОСЛЕДНЯЯ такая строка, а не первая. В хвосте лога
                # их столько, сколько было входов в игру, и первая — самая
                # старая: у кого два персонажа, метр подписывал своей
                # строкой того, кем играли утром, а удары текущего чара
                # уезжали отдельной строкой рядом с «You».
                glory = m["me"]
                own.clear()
                continue
            m = RE_OWN_CHAT.match(body)
            if m:
                own[m["me"]] += 1
        if glory:
            return glory
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
                # Три вещи обязаны случиться, и раньше не случалась ни одна
                # из двух последних: текст в окно, трейсбек в лог программы
                # и ПУБЛИКАЦИЯ снимка — упавший тик до неё не доходит,
                # поэтому окно продолжало показывать старые цифры как
                # живые, а лог оставался пустым.
                self.error = f"{type(e).__name__}: {e}"
                self._log_once("тик чтения не удался", str(e))
                try:
                    self._publish()
                except Exception:                       # noqa: BLE001
                    pass
            self._stop.wait(interval)

    def _tick(self) -> None:
        if self.tailer is None:
            if self._waiting:
                self._try_open_waiting()
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
                    # try на КАЖДУЮ запись, а не на порцию целиком: иначе
                    # одна битая строка уносила с собой остаток пачки —
                    # тех строк tailer уже не отдаст, они пропадали молча.
                    try:
                        ev = parse(ts, body)
                    except Exception:              # noqa: BLE001
                        self.unknown += 1
                        self._log_once("failed to parse a line", body)
                        continue
                    if ev is None:
                        self.unknown += 1
                        self._log_unknown(body)
                        continue
                    try:
                        self.meter.feed(ev)
                    except Exception:              # noqa: BLE001
                        # Считаем такую строку нераспознанной. Иначе она
                        # не попадала НИ В ОДИН счётчик, и health()
                        # показывал бы идеальные проценты при том, что
                        # в таблицу не доходит ничего.
                        self.unknown += 1
                        self._log_once("failed to record an event", body)
                        continue
                    self.parsed += 1
        # Красную надпись снимаем, только когда чтение снова принесло
        # строки: пустой тик ничего не доказывает, а раньше он стирал
        # сообщение о сбое ещё до того, как человек успевал его увидеть.
        if self.error and raw_lines:
            self.error = ""
        # Пересобирать снимок вхолостую незачем: замер показал 15,6 %
        # ядра на пустом логе, причём пауза почти не помогала. Публикуем,
        # когда пришли строки — и раз в секунду, чтобы тикал скользящий
        # DPS и гас признак «бой идёт».
        now = time.monotonic()
        if raw_lines or now - self._published_at >= 1.0:
            self._published_at = now
            self._publish()

    def _log_once(self, what: str, sample: str) -> None:
        """Записать сбой в лог программы один раз на каждый вид.

        Без ограничения сюда сыпался бы трейсбек на каждую строку, а лог
        программы ротируется по размеру — диагностика старта вымывалась бы
        за секунды.
        """
        key = f"{what}:{type(sys.exc_info()[1]).__name__}"
        if key in self._logged:
            return
        self._logged.add(key)
        applog.log.exception("%s: %.200s", what, sample)

    #: Файл нераспознанного не должен расти без предела: его присылают в
    #: сообщениях о проблеме, а мегабайты повторов там никому не нужны.
    UNKNOWN_MAX_BYTES = 1024 * 1024

    def _is_chat(self, body: str) -> bool:
        """Строка чата? Такие в файл нераспознанного не пишем.

        Это вопрос приватности, а не объёма. Файл человек прикладывает к
        сообщению о проблеме, и в нём не должно быть ни его собственных
        реплик, ни шёпота, ни разговоров согруппников. Замер на живом
        файле: 1087 строк группового и легионного чата, 689 собственных
        реплик, 99 упоминаний шёпота.
        """
        if not body:
            return True
        if body[0] == "[":                       # [3.LFG] …, [charname:…]
            return True
        if "Whisper" in body or body.startswith("Legion Message:"):
            return True
        head, sep, _rest = body.partition(": ")
        # «Ник: текст» — реплика без обёртки канала. У боевых строк
        # двоеточия в такой позиции не бывает.
        return bool(sep) and 1 <= len(head) <= 24 and " " not in head

    def _log_unknown(self, body: str) -> None:
        """Нераспознанные строки — единственный способ заметить, что сервер
        сменил шаблоны, до того как метр начнёт молча показывать нули."""
        if self.unknown > 5000 or self._is_chat(body):
            return
        try:
            if self._unknown_fh is None:
                d = cfgmod.config_dir()
                d.mkdir(parents=True, exist_ok=True)
                path = d / "unknown.log"
                # Файл, доросший до потолка, начинаем заново: интересны
                # свежие шаблоны, а не то, что не разобралось неделю назад.
                if path.exists() and path.stat().st_size > self.UNKNOWN_MAX_BYTES:
                    try:
                        path.replace(d / "unknown.prev.log")
                    except OSError:
                        pass
                self._unknown_fh = open(path, "a", encoding="utf-8")
                self._unknown_fh.write(
                    f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} "
                    f"AionMeter {__version__} {self.encoding} ---\n")
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
            return "no data"
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
