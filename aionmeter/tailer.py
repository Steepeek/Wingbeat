"""Чтение растущего Chat.log, пока игра его пишет.

Три вещи, без которых это не работает на Windows:

1. FILE_SHARE_DELETE. Штатный open() его не выставляет, и пока метр держит
   хендл, лаунчер игры не может удалить или переименовать лог. Это
   единственный способ, которым метр может реально помешать игре.
2. Незавершённая последняя строка. Клиент делает flush построчно, но это не
   значит, что запись атомарна: прочитать можно половину строки. Хвост без
   \r\n наверх не отдаём никогда.
3. Ротация. Лог могут обрезать, переименовать или подменить симлинком на
   RAM-диск. Одного сравнения размера мало — сверяем ещё и identity файла.
"""

from __future__ import annotations

import io
import os

_IS_WINDOWS = os.name == "nt"

if _IS_WINDOWS:
    import ctypes
    import msvcrt
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.CreateFileW.restype = wintypes.HANDLE
    _k32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]

    _GENERIC_READ = 0x80000000
    _SHARE_ALL = 0x1 | 0x2 | 0x4          # READ | WRITE | DELETE
    _OPEN_EXISTING = 3
    _INVALID = ctypes.c_void_p(-1).value


def open_shared(path: str):
    """Открывает файл на чтение, не мешая игре писать и удалять его."""
    if not _IS_WINDOWS:
        return open(path, "rb", buffering=0)
    handle = _k32.CreateFileW(path, _GENERIC_READ, _SHARE_ALL, None, _OPEN_EXISTING, 0, None)
    if handle == _INVALID:
        raise ctypes.WinError(ctypes.get_last_error())
    fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    return io.open(fd, "rb", buffering=0)


class Tailer:
    """Отдаёт новые полные строки лога. Незавершённый хвост держит в буфере."""

    #: Сколько байт с начала файла помним как подпись содержимого.
    SIG_LEN = 256

    def __init__(self, path: str, backfill_bytes: int = 256 * 1024, from_start: bool = False):
        self.path = path
        self.backfill = backfill_bytes
        self.from_start = from_start
        self.f = None
        self.ident = None
        self.sig = b""
        self.buf = b""
        self.bytes_read = 0
        self.rotations = 0
        self.open()

    # -- жизненный цикл --

    def open(self) -> None:
        self.close()
        self.f = open_shared(self.path)
        st = os.fstat(self.f.fileno())
        # Только dev+ino. Время сюда класть нельзя: os.stat и os.fstat на
        # Windows возвращают st_ctime с разной точностью, и сравнение вечно
        # даёт "ротацию" — метр переоткрывает лог и не показывает ничего.
        self.ident = (st.st_dev, st.st_ino)
        self.sig = self.f.read(self.SIG_LEN)
        if self.from_start:
            self.f.seek(0)
        else:
            start = max(0, st.st_size - self.backfill)
            self.f.seek(start)
            if start:
                self.f.readline()   # первая строка заведомо обрезана — выбрасываем
        self.buf = b""

    def close(self) -> None:
        if self.f is not None:
            try:
                self.f.close()
            except OSError:
                pass
            self.f = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -- чтение --

    def _rotated(self) -> bool:
        """Файл подменили, пересоздали или переписали.

        Одной проверки размера мало: если лог обрезали и записали заново, а
        новая длина оказалась БОЛЬШЕ текущей позиции чтения (обычное дело при
        «очистить лог при запуске»), то size >= pos и подмена не видна —
        метр начнёт читать с середины чужого текста. Поэтому сверяем ещё и
        первые байты файла.
        """
        try:
            st = os.stat(self.path)
        except OSError:
            return False
        if (st.st_dev, st.st_ino) != self.ident:
            return True
        try:
            pos = self.f.tell()
            self.f.seek(0)
            head = self.f.read(self.SIG_LEN)
            self.f.seek(pos)
        except OSError:
            return False
        if head == self.sig:
            return False
        # Файл был короче подписи и просто дорос — это не подмена.
        if len(self.sig) < self.SIG_LEN and head.startswith(self.sig):
            self.sig = head
            return False
        return True

    def read(self) -> list[bytes]:
        """Возвращает список полных строк (без разделителей). Может быть пуст."""
        if self.f is None:
            try:
                self.open()
            except OSError:
                return []

        try:
            size = os.fstat(self.f.fileno()).st_size
            pos = self.f.tell()
        except OSError:
            self.close()
            return []

        if size < pos or self._rotated():
            # Лог обрезали, пересоздали или подменили. Начинаем сначала.
            self.rotations += 1
            self.from_start = True
            try:
                self.open()
            except OSError:
                self.close()
            return []

        if size == pos:
            return []

        chunk = self.f.read(size - pos)
        if not chunk:
            return []
        self.bytes_read += len(chunk)
        self.buf += chunk

        parts = self.buf.split(b"\r\n")
        self.buf = parts.pop()          # хвост без \r\n остаётся до следующего раза
        return parts
