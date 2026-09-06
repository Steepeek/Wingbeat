"""Сборка в dist/AionMeter: py build.py

Осознанные решения:

* --onedir, а не --onefile. Одиночный самораспаковывающийся exe от
  неизвестного издателя — типовая эвристика антивирусов, ложные срабатывания
  на нём в разы чаще. Папка проходит тише.
* --noupx. Связка «UPX + неподписанный exe» отправляет в карантин почти
  гарантированно.
* Выкинуты неиспользуемые модули Qt: без них папка меньше примерно вдвое.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from aionmeter.version import __version__

EXCLUDE = [
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebChannel",
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.Qt3DCore",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtCharts",
    "PySide6.QtDataVisualization", "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets",
    "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtDesigner", "PySide6.QtHelp",
    "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtSerialPort",
    "PySide6.QtBluetooth", "PySide6.QtPositioning", "PySide6.QtSensors",
    "PySide6.QtNetwork", "PySide6.QtSvg", "PySide6.QtPrintSupport",
    "tkinter", "unittest", "pydoc", "doctest", "xml", "pdb",
]

#: email и http РАНЬШЕ были в списке исключений — и это молча ломало urllib:
#: он тянет http.client и email.message для заголовков. В замороженной
#: сборке проверка обновлений падала бы с ImportError, а не с внятной
#: ошибкой. Не возвращать их сюда.

#: Что кладём рядом с exe. assets собирается из клиента отдельным
#: инструментом и в git не хранится, поэтому его отсутствие — не ошибка
#: сборки, а повод громко предупредить: без него у игрока не будет иконок.
EXTRA_FILES = ("README.md", "LICENSE")
EXTRA_DIRS = ("assets",)


def main() -> int:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("Нужен PyInstaller:  py -m pip install pyinstaller")
        return 1

    for stale in ("build", "dist"):
        shutil.rmtree(ROOT / stale, ignore_errors=True)

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onedir", "--windowed", "--noupx",
        "--name", "AionMeter",
        "--icon", str(ROOT / "docs" / "aionmeter.ico"),
        "--distpath", str(ROOT / "dist"),
        "--workpath", str(ROOT / "build"),
        "--specpath", str(ROOT / "build"),
    ]
    for module in EXCLUDE:
        cmd += ["--exclude-module", module]
    cmd.append(str(ROOT / "main.py"))

    print(" ".join(cmd), "\n")
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode:
        return result.returncode

    out = ROOT / "dist" / "AionMeter"
    for extra in EXTRA_FILES:
        shutil.copy(ROOT / extra, out / extra)

    for folder in EXTRA_DIRS:
        src = ROOT / folder
        if src.is_dir():
            shutil.copytree(src, out / folder, dirs_exist_ok=True)
            n = sum(1 for _ in (out / folder).rglob("*"))
            print(f"  {folder}: скопировано {n} файлов")
        else:
            print(f"\n  ВНИМАНИЕ: папки {folder} нет.")
            print("  Сборка выйдет БЕЗ иконок и названий предметов — у игрока")
            print("  будут буквенные фишки классов и номера вместо названий.")
            print("  Собрать: py tools/private/extract_assets.py --game <клиент>")

    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    print(f"\nГотово: {out}  ({size / 1024 / 1024:.0f} МБ), версия {__version__}")
    print("Раздавать: заархивировать эту папку целиком.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
