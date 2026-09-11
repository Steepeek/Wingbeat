"""Сборка инсталлятора: py installer/make.py

Ожидает, что dist/Wingbeat уже собран (py build.py). Версия берётся из
wingbeat/version.py, чтобы номер в «Программах и компонентах», в имени
файла и в самой программе не разъезжались.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from wingbeat.version import __version__

HERE = ROOT / "installer"
DIST = ROOT / "dist" / "Wingbeat"

#: Где Inno Setup лежит по умолчанию. ISCC в PATH попадает редко.
#: Установка через winget кладёт его не в Program Files, а в профиль
#: пользователя — без этого пути сборка установщика на чистой машине
#: не находит компилятор, хотя он стоит.
CANDIDATES = (
    Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
    Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe",
)


def find_iscc() -> Path | None:
    found = shutil.which("iscc") or shutil.which("ISCC")
    if found:
        return Path(found)
    for path in CANDIDATES:
        if path.is_file():
            return path
    return None


def main() -> int:
    if not (DIST / "Wingbeat.exe").is_file():
        print("Сначала соберите программу:  py build.py")
        return 1
    if not (DIST / "assets").is_dir():
        print("ВНИМАНИЕ: в сборке нет папки assets — инсталлятор выйдет")
        print("без иконок и названий предметов.")

    iscc = find_iscc()
    if iscc is None:
        print("Не найден компилятор Inno Setup (ISCC.exe).")
        print("Скачать: https://jrsoftware.org/isdl.php")
        print("После установки запустите этот скрипт снова.")
        return 1

    (HERE / "out").mkdir(exist_ok=True)
    cmd = [str(iscc), f"/DMyAppVersion={__version__}", str(HERE / "Wingbeat.iss")]
    print(" ".join(cmd), "\n")
    result = subprocess.run(cmd, cwd=HERE)
    if result.returncode:
        return result.returncode

    out = HERE / "out" / f"WingbeatSetup-{__version__}.exe"
    if out.is_file():
        print(f"\nГотово: {out}  ({out.stat().st_size / 1024 / 1024:.0f} МБ)")

    archive = pack_zip()
    print(f"Готово: {archive}  ({archive.stat().st_size / 1024 / 1024:.0f} МБ)")
    print(f"Готово: {write_sums([out, archive])}")
    return 0


def pack_zip() -> Path:
    """Тот же набор файлов, что и в установщике, только распакованный.

    Раньше архив и контрольные суммы собирались руками, и однажды это
    стоило неверного выпуска: под тегом оказалась сборка, в которой не
    было половины последних правок. Теперь их делает тот же запуск и из
    той же папки dist, что и установщик, — разойтись они уже не могут.
    """
    archive = HERE / "out" / f"Wingbeat-{__version__}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for item in sorted(DIST.rglob("*")):
            if item.is_file():
                z.write(item, Path("Wingbeat") / item.relative_to(DIST))
    return archive


def write_sums(files: list[Path]) -> Path:
    """SHA-256 обоих файлов в формате, который понимает sha256sum -c."""
    lines = []
    for path in files:
        if not path.is_file():
            continue
        digest = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                digest.update(chunk)
        lines.append(f"{digest.hexdigest()}  {path.name}")
    sums = HERE / "out" / "SHA256SUMS.txt"
    sums.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return sums


if __name__ == "__main__":
    raise SystemExit(main())
