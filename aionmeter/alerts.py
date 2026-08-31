"""Звуковые уведомления по событиям в логе.

Важная оговорка про «врага рядом»: такого события в Chat.log нет. Игра не
пишет в лог факт появления игрока чужой фракции в поле зрения — проверено
на живом логе, слова «Enemy» и «Espy» там встречаются только как НИКИ
игроков, а не как системные сообщения.

Что в логе есть на самом деле и годится как сигнал:

* **Враг ударил** — строка входящего урона, где атакующий не моб, а игрок
  (имя без пробела). На живом логе это 45 разных игроков и 555 ударов:
  самый честный признак «противник здесь и уже бьёт».
* **Открылся рифт** — «A one-way Rift into Asmodae has appeared.» 51 раз
  за сессию. В Aion это и есть штатный способ, которым враг попадает
  в локацию, то есть предупреждение заранее.
* **Своя смерть** — на случай, если отвлёкся.
* Плюс свои строки: любой текст, который человек впишет сам.

Звук: WAV идёт через winsound (он в стандартной библиотеке Python для
Windows), всё остальное — mp3, ogg — через MCI из winmm.dll, вызываемый
через ctypes. Новых зависимостей не появляется ни там, ни там.
"""

from __future__ import annotations

import ctypes
import itertools
import re
import threading
import time
from pathlib import Path

try:
    import winsound
except ImportError:                        # не Windows — просто молчим
    winsound = None

from .aggregate import is_player_name

#: Что умеем проигрывать. WAV идёт через winsound (быстро и без состояния),
#: остальное — через MCI: это тот же стандартный Windows API, доступный
#: через ctypes, никаких новых зависимостей ради mp3 не нужно.
AUDIO_EXT = (".wav", ".mp3", ".ogg", ".wma", ".m4a")
_mci_seq = itertools.count(1)

#: Встроенные правила. Порядок — порядок в настройках.
BUILTIN = (
    ("pvp", "Враг атакует вас или группу"),
    ("rift", "Открылся рифт (враг может зайти)"),
    ("death", "Вы погибли"),
    ("pvp_kill", "Кто-то убит в PvP рядом"),
)

RE_RIFT = re.compile(r"Rift into .{1,40} has appeared", re.IGNORECASE)

DEFAULTS = {
    "enabled": False,
    "sound": "",            # путь к .wav; пусто — системный сигнал
    "cooldown": 15,         # секунд между срабатываниями одного правила
    "duration": 3,          # сколько секунд играть; 0 — файл целиком
    "rules": {"pvp": True, "rift": True, "death": True, "pvp_kill": False},
    "custom": [],           # свои подстроки/регулярки
}


class Alerts:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self._last: dict[str, int] = {}
        self.fired: list[tuple[int, str]] = []
        self._custom: list[re.Pattern] = []
        self._custom_src: list[str] = []

    # -- настройки --

    @property
    def conf(self) -> dict:
        got = self.cfg.get("alerts") or {}
        merged = dict(DEFAULTS)
        merged.update(got)
        merged["rules"] = {**DEFAULTS["rules"], **(got.get("rules") or {})}
        return merged

    def _patterns(self, sources: list[str]) -> list[re.Pattern]:
        if sources != self._custom_src:
            self._custom_src = list(sources)
            self._custom = []
            for src in sources:
                if not src.strip():
                    continue
                try:
                    self._custom.append(re.compile(src, re.IGNORECASE))
                except re.error:
                    # Строка не разобралась как регулярка — ищем как есть
                    self._custom.append(re.compile(re.escape(src), re.IGNORECASE))
        return self._custom

    # -- проверка --

    def check(self, ts: int, body: str, ev, self_name: str = "") -> str:
        """Возвращает ключ сработавшего правила или ''."""
        conf = self.conf
        if not conf["enabled"]:
            return ""
        rules = conf["rules"]

        key = ""
        if ev is not None:
            if (rules.get("pvp") and ev.kind == "damage" and ev.incoming
                    and ev.actor and is_player_name(ev.actor)):
                key = "pvp"
            elif rules.get("death") and ev.kind == "death" and ev.target == "You":
                key = "death"
            elif rules.get("pvp_kill") and ev.kind == "pvp":
                key = "pvp_kill"
        if not key and rules.get("rift") and RE_RIFT.search(body):
            key = "rift"
        if not key:
            for pat in self._patterns(conf.get("custom") or []):
                if pat.search(body):
                    key = "custom"
                    break
        if not key:
            return ""

        # Кулдаун на правило: в бою входящий урон идёт очередью, и без него
        # сигнал превратится в непрерывный писк.
        last = self._last.get(key, 0)
        if ts - last < int(conf.get("cooldown", 8)):
            return ""
        self._last[key] = ts
        self.fired.append((ts, key))
        if len(self.fired) > 50:
            del self.fired[:-50]
        self.play(conf.get("sound", "") or default_sound(),
                  int(conf.get("duration", 3)))
        return key

    # -- звук --

    @staticmethod
    def play(path: str = "", duration: int = 3) -> None:
        """Проигрывает сигнал, не блокируя чтение лога."""
        threading.Thread(target=_play_blocking, args=(path, duration),
                         daemon=True).start()


def default_sound() -> str:
    """Звук из папки Sound рядом с программой, если он там лежит."""
    folder = Path(__file__).resolve().parent.parent / "Sound"
    if not folder.is_dir():
        return ""
    for f in sorted(folder.iterdir()):
        if f.suffix.lower() in AUDIO_EXT:
            return str(f)
    return ""


def _play_blocking(path: str, duration: int = 3) -> None:
    try:
        if not path:
            if winsound is not None:
                winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
            return
        if not Path(path).is_file():
            return
        if path.lower().endswith(".wav") and winsound is not None:
            winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
            return
        _play_mci(path, duration)
    except Exception:                       # noqa: BLE001 - звук не критичен
        pass


def _play_mci(path: str, duration: int = 3) -> None:
    """Проигрывание через Media Control Interface.

    winsound умеет только WAV, а сигнал обычно берут в mp3. MCI входит в
    состав Windows (winmm.dll) и вызывается через ctypes — новых
    зависимостей это не добавляет.
    """
    if not hasattr(ctypes, "windll"):
        return
    mci = ctypes.windll.winmm.mciSendStringW
    alias = f"aionmeter_alert_{next(_mci_seq)}"
    # Кавычки обязательны: в пути бывают пробелы
    if mci(f'open "{path}" alias {alias}', None, 0, None) != 0:
        return
    try:
        mci(f"set {alias} time format milliseconds", None, 0, None)
        length_ms = 0
        buf = ctypes.create_unicode_buffer(64)
        if mci(f"status {alias} length", buf, 64, None) == 0:
            try:
                length_ms = int(buf.value)
            except ValueError:
                length_ms = 0
        # Обрезаем длину: сигнал со стока легко тянется десять секунд, а
        # срабатывать может чаще — получается дрон вместо предупреждения.
        span_ms = duration * 1000 if duration > 0 else length_ms
        if length_ms:
            span_ms = min(span_ms or length_ms, length_ms)
        cmd = f"play {alias} from 0" + (f" to {span_ms}" if span_ms else "")
        if mci(cmd, None, 0, None) != 0:
            return
        # Флаг wait у mpegvideo не блокирует — ждём сами, поток фоновый
        time.sleep((span_ms or 1000) / 1000 + 0.1)
    finally:
        mci(f"close {alias}", None, 0, None)
