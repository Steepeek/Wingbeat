"""Настройки: чтение, запись, значения по умолчанию, автопоиск игры."""

from __future__ import annotations

import json
import os
from pathlib import Path

APP_NAME = "Wingbeat"

#: Как каталог данных назывался до переименования программы. Там у людей
#: накопленные сессии, база предметов и настройки — при смене имени всё
#: это обязано переехать, а не осиротеть.
LEGACY_APP_NAMES = ("AionMeter",)

DEFAULTS: dict = {
    # --- источник данных ---
    "game_dir": "",              # корень клиента (там, где лежит Chat.log)
    "log_path": "",              # переопределение пути к логу; пусто = game_dir/Chat.log
    "encoding": "auto",          # auto | cp1251 | utf-8 | cp1252
    "self_name": "",             # свой ник; пусто = определить автоматически
    "show_own_nick": False,      # подписывать свою строку ником вместо «You»
    # Свой класс, определённый в прошлый раз. Нужен на старте: пока он
    # неизвестен, разбор хила не может отличить мой хил от чужого.
    "self_class": "",
    # Считать только на клиенте Origin. Грамматика боевых строк выведена
    # из шаблонов этого клиента: на другом сервере цифры будут не
    # ошибочными, а правдоподобно неверными, и это хуже пустой таблицы.
    "require_origin": True,

    # --- расчёт ---
    "dps_window": 10,            # окно "текущего" DPS, секунд
    "encounter_timeout": 12,     # тишина, после которой бой считается законченным
    "active_gap": 8,             # разрыв, после которого игрок считается неактивным
    "kill_grace": 3,             # сколько секунд после смерти цели добираем DoT

    # --- что показывать ---
    "metric": "damage",          # damage | heal | taken | loot | sessions
    "mode": "session",           # session (копится до очистки) | encounter (текущий бой)
    # Считать только урон по ГЛАВНОЙ цели боя — по боссу, без аддов.
    # Отдельной вкладки для этого нет намеренно: это тот же урон, просто
    # отфильтрованный, и держать его во второй таблице значило бы
    # заставить человека сравнивать две вкладки глазами.
    "boss_only": False,
    "scope": "all",              # показываем всех, кто наносил урон
    "hide_mobs": True,
    "merge_pets": True,          # урон питомцев приписывать владельцу
    "count_reflect": False,      # считать урон щита-отражателя (числа фиктивны)
    # Клиент пишет одну и ту же строку и на дроп с моба, и на взятое
    # со склада или из почты. Отличаем по обстановке: настоящий дроп
    # падает в бою, склад и почта — в городе.
    "loot_in_combat": True,      # в добычу идёт только выпавшее в бою
    "loot_window": 25,           # сколько секунд после боя ещё считаем дропом
    "check_updates": True,       # смотреть на GitHub, вышла ли версия новее
    # Куда метр обращается за общими делами: обратная связь, позже —
    # рейтинг. Вынесено в настройки, чтобы при переезде сайта не
    # пересобирать программу всем, у кого она уже стоит.
    "api_url": "https://wingbeat.fun/api",
    # Полоса крупных кнопок и полоска опыта с кинарой настройками не были
    # и не будут: это часть окна, а не выбор. Выключаемая половина
    # интерфейса — это две программы вместо одной, и обе надо проверять.
    "action_size": 48,           # сторона кнопки действия, пикселей
    # Один размер на все иконки: эмблема класса, скилл, предмет,
    # опыт, кинара. От него же считаются высоты строк.
    "icon_size": 37,             # 20-64
    "art_frame": True,           # рисованная рамка окна вместо простого канта
    "art_panel": True,           # фактура металла под таблицей
    "columns": ["dmg", "dps", "pct", "hits"],
    "max_rows": 12,
    "icons_dir": "",             # папка с иконками классов; пусто = без иконок
    "skill_icons_dir": "",       # папка с иконками скиллов (см. tools/fetch_skill_icons.py)
    "show_loot": True,          # строка внизу: опыт, AP, кинах, убийства


    # --- внешний вид ---
    "transparent": False,        # сквозная прозрачность фона (для оверлея поверх игры)
    "opacity": 1.0,
    "font_size": 12,
    # Bahnschrift — это DIN 1451, шрифт немецких дорожных указателей:
    # прямые линии, узкие буквы, никакой мягкости. В узком окне он даёт
    # заметно больше символов на ту же ширину, чем Segoe UI. Если его в
    # системе нет (Windows 8 и старше), Qt сам возьмёт следующий из списка.
    "font_family": "Bahnschrift",
    # --- режим стримера ---
    # Окно превращается в чистые полосы урона на однотонном фоне, который
    # вырезается хромакеем в OBS. Всё остальное — шапка, кнопки, сводка,
    # подвал, рамка — прячется: в кадре должно остаться только то, ради
    # чего зритель смотрит.
    "streamer": False,
    "chroma_color": "#00B140",   # стандартный «зелёный экран», не чистый #00FF00
    "click_through": False,
    "always_on_top": True,
    # Обвязка окна (шапка, кнопки, сводка, заголовки, подвал) занимает
    # около 350 px при заводских размерах иконок. При прежних 340 таблице
    # оставалось меньше двух строк — метр открывался почти пустым.
    "window": {"x": 80, "y": 80, "w": 460, "h": 560},

    # --- хоткеи (пусто = выключен) ---
    # F-клавиши, а не буквы. На немецкой, польской и французской
    # раскладках AltGr посылается как Ctrl+Alt, поэтому Ctrl+Alt+C
    # глобально отбирал у человека ввод «ć» во ВСЕХ программах, пока метр
    # запущен. Регистрация при этом проходила успешно, так что и
    # предупреждения он не видел. F-клавиш AltGr не касается.
    "hotkeys": {
        "reset": "Ctrl+Shift+F1",
        "pause": "Ctrl+Shift+F2",
        "click_through": "Ctrl+Shift+F3",
        "hide": "Ctrl+Shift+F4",
        "copy": "Ctrl+Shift+F5",
        "streamer": "Ctrl+Shift+F6",
    },

    # --- сессии ---
    # Сессия начинается с первого удара и закрывается кнопкой «Очистить»
    # (или выходом из программы). Закрытая сессия ложится файлом в
    # %APPDATA%\Wingbeat\sessions и живёт там, пока не вытеснится новыми.
    "keep_sessions": 200,        # сколько последних сессий хранить на диске
    "save_sessions": True,       # писать ли их вообще

    "poll_ms": 250,
    # Ноль = метр открывается пустым и считает только то, что произошло
    # после запуска. Так и ожидают: увидеть при открытии чужой бой
    # получасовой давности — это выглядит поломкой, а не удобством.
    "backfill_kb": 0,            # сколько хвоста лога читать при старте, КБ
}


def config_dir() -> Path:
    """Каталог данных, при необходимости переехавший со старого имени.

    Результат намеренно не кэшируется: проверка стоит пары обращений к
    файловой системе, зато и тесты, подменяющие APPDATA, и переезд между
    запусками ведут себя предсказуемо. Повторный вызов уже ничего не
    двигает — после переноса новый каталог существует.
    """
    base = Path(os.environ.get("APPDATA") or str(Path.home()))
    new = base / APP_NAME
    return _migrate_legacy(base, new) or new


def _migrate_legacy(base: Path, new: Path) -> Path | None:
    """Перенести каталог данных со старого имени программы.

    Возвращает путь, ТОЛЬКО если перенести не удалось и работать надо по
    старому адресу: молча начать с чистого листа нельзя — человек решит,
    что пропали все его сессии.
    """
    if new.exists():
        return None
    for old_name in LEGACY_APP_NAMES:
        old = base / old_name
        if not old.is_dir():
            continue
        try:
            # Переименование каталога, а не копирование: мгновенно и не
            # требует места под вторую копию базы предметов.
            old.rename(new)
        except OSError:
            return old
        return None
    return None


def config_path() -> Path:
    return config_dir() / "config.json"


def load() -> dict:
    cfg = json.loads(json.dumps(DEFAULTS))  # глубокая копия
    p = config_path()
    if p.exists():
        try:
            # utf-8-sig, а не utf-8: Блокнот и PowerShell пишут UTF-8 с BOM,
            # и на обычном utf-8 разбор падает — настройки молча теряются,
            # программа откатывается на значения по умолчанию.
            saved = json.loads(p.read_text("utf-8-sig"))
        except (OSError, ValueError):
            saved = {}
        for k, v in saved.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
    return cfg


def save(cfg: dict) -> None:
    d = config_dir()
    d.mkdir(parents=True, exist_ok=True)
    tmp = config_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), "utf-8")
    tmp.replace(config_path())


# --- поиск игры -----------------------------------------------------------

_CANDIDATE_HINTS = ("aion", "origin", "gameforge", "innova", "ncsoft")


def icons_dir(cfg: dict) -> str:
    """Папка с иконками классов. Пустая настройка = папка по умолчанию.

    Так человеку не нужно ничего вписывать: утилита и установщик кладут
    файлы туда же, куда программа смотрит.
    """
    return _resolve_dir(cfg.get("icons_dir"), "icons")


def skill_icons_dir(cfg: dict) -> str:
    """Папка с иконками скиллов (её наполняет tools/fetch_skill_icons.py)."""
    return _resolve_dir(cfg.get("skill_icons_dir"), "skillicons")


def _resolve_dir(configured: str | None, default_name: str) -> str:
    if configured:
        return configured
    path = config_dir() / default_name
    return str(path) if path.is_dir() else ""


#: Где внутри папки игры может лежать Chat.log. Обычно он рядом с
#: aion.bin в корне, но у части сборок лежит в подпапке клиента, поэтому
#: смотрим и на уровень глубже — дальше идти незачем, там уже ресурсы.
_LOG_SUBDIRS = ("", "Chat", "logs", "Logs", "Bin32", "bin32", "Bin64", "bin64")


def find_log_in(game_dir: str) -> str:
    """Найти Chat.log внутри папки игры. Пусто, если его там нет.

    Человеку проще указать папку с игрой, чем файл: папку он знает, а про
    то, что метру нужен именно Chat.log, знать не обязан. Поэтому путь к
    файлу программа выводит сама.
    """
    if not game_dir:
        return ""
    root = Path(game_dir)
    if root.is_file():                      # указали сам файл — примем и его
        return str(root)
    for sub in _LOG_SUBDIRS:
        candidate = root / sub / "Chat.log" if sub else root / "Chat.log"
        if candidate.is_file():
            return str(candidate)
    # Ничего в известных местах — обходим на два уровня вглубь. Клиент Aion
    # это десятки тысяч файлов, поэтому вглубь не идём и по именам не гадаем.
    try:
        for depth in (root.glob("*/Chat.log"), root.glob("*/*/Chat.log")):
            for candidate in depth:
                if candidate.is_file():
                    return str(candidate)
    except OSError:
        pass
    return ""


#: Паки, по которым узнаётся клиент Origin. Проверяем несколько: набор
#: файлов у сборок отличается, а формат — нет.
_ORIGIN_PAKS = ("Data/Items/items.pak", "Data/skills/skills.pak",
                "Data/Npcs/npcs.pak", "Data/pc/PC.pak", "Data/Quest/Quest.pak")

#: Подпись собственного шифрования Origin. Ни один другой сервер такого
#: слоя не ставит: обычные паки Aion начинаются с «PK» (ZIP) или с
#: сигнатуры roxfan. Замер на живом клиенте: 28 паков из 3151 — с этой
#: магией, и это ровно те, что Origin шифрует своим ключом.
ORIGIN_MAGIC = b"OADTENC1"


def is_origin_client(game_dir: str) -> bool:
    """Похож ли клиент в этой папке на Origin.

    Проверка честная, но не «защита»: файлы можно подменить, и кто
    захочет — обойдёт. Смысл в другом. Грамматика боевых строк выведена
    из шаблонов ИМЕННО этого клиента, и на чужом сервере метр показывал
    бы не ошибку, а правдоподобные, но неверные цифры. Лучше сказать
    прямо, для чего он сделан.
    """
    if not game_dir:
        return False
    root = Path(game_dir)
    for rel in _ORIGIN_PAKS:
        pak = root / rel
        try:
            if pak.is_file():
                with open(pak, "rb") as f:
                    if f.read(len(ORIGIN_MAGIC)) == ORIGIN_MAGIC:
                        return True
        except OSError:
            continue
    return False


def origin_launcher_dir() -> str:
    """Папка клиента, записанная лаунчером Origin. Пусто, если его нет."""
    base = os.environ.get("LOCALAPPDATA") or ""
    path = Path(base) / "OriginAion" / "data" / "appsettings.json"
    try:
        data = json.loads(path.read_text("utf-8-sig"))
    except (OSError, ValueError):
        return ""
    return str(data.get("AionClientDirectoryPath") or "")


def resolve_log_path(cfg: dict) -> str:
    if cfg.get("log_path"):
        return cfg["log_path"]
    return find_log_in(cfg.get("game_dir", ""))


def autodetect_log() -> str:
    """Ищет Chat.log в типичных местах. Возвращает '' если не нашёл.

    Смотрим сначала туда, где уже прописан путь у существующих инструментов,
    потом перебираем диски. Глубина ограничена, чтобы не сканировать всё.
    """
    for path in _from_known_tools():
        if os.path.isfile(path):
            return path

    for root in _drive_roots():
        for depth_dir in _shallow_dirs(root, max_depth=4):
            name = os.path.basename(depth_dir).lower()
            if not any(h in name for h in _CANDIDATE_HINTS):
                continue
            candidate = os.path.join(depth_dir, "Chat.log")
            if os.path.isfile(candidate):
                return candidate
    return ""


def _from_known_tools():
    """Путь к игре, уже записанный другими программами."""
    appdata = os.environ.get("APPDATA", "")
    # Aion RainMeter хранит LogPath в settings.xml
    arm = Path(appdata) / "Aion Rainmeter" / "settings.xml"
    if arm.exists():
        try:
            import re
            text = arm.read_text("utf-8", errors="replace")
            for m in re.finditer(r"<LogPath>([^<]+)</LogPath>", text):
                p = m.group(1).strip()
                if p:
                    yield os.path.join(p, "Chat.log")
        except OSError:
            pass


def _drive_roots():
    if os.name != "nt":
        yield str(Path.home())
        return
    import string
    for letter in string.ascii_uppercase:
        root = f"{letter}:\\"
        if os.path.isdir(root):
            yield root


def _shallow_dirs(root: str, max_depth: int):
    """Обход в ширину с ограничением глубины и пропуском системных папок."""
    skip = {"windows", "$recycle.bin", "system volume information", "programdata",
            "appdata", "node_modules", "temp", "tmp"}
    frontier = [(root, 0)]
    while frontier:
        path, depth = frontier.pop(0)
        if depth >= max_depth:
            continue
        try:
            with os.scandir(path) as it:
                for entry in it:
                    if not entry.is_dir(follow_symlinks=False):
                        continue
                    if entry.name.lower() in skip or entry.name.startswith("."):
                        continue
                    yield entry.path
                    frontier.append((entry.path, depth + 1))
        except OSError:
            continue


# --- кодировка ------------------------------------------------------------

def system_ansi() -> str:
    """Кодовая страница Windows, в которой клиент пишет Chat.log.

    Игра пишет лог в CP_ACP — системной ANSI-странице, а не в utf-8 и не
    в жёстко заданной cp1251. У русской Windows это 1251, у немецкой 1252,
    у польской 1250. Спрашиваем систему, а не гадаем.
    """
    try:
        import ctypes
        cp = int(ctypes.windll.kernel32.GetACP())
    except Exception:                      # noqa: BLE001 - не Windows
        return "cp1251"
    # 65001 — это utf-8: как ANSI-страница он встречается на системах
    # с включённой галкой «Beta: UTF-8», и там лог тоже будет в utf-8.
    return "utf-8" if cp == 65001 else f"cp{cp}"


def detect_encoding(sample: bytes) -> str:
    """Определяет кодировку по куску лога.

    Тонкость, которая стоила молча заниженных цифр. Чистый ASCII валиден
    и как utf-8, и как любая однобайтовая страница, и раньше на таком
    куске возвращался utf-8. У свежего Chat.log хвост как раз чисто
    английский — а разделителем тысяч клиент пишет неразрывный пробел
    (0xA0). В utf-8 этот байт одиночным не бывает: «1<A0>220» после
    декодирования превращалось в «1?220», регулярка урона не совпадала,
    и ВСЕ удары от тысячи пропадали из счёта.

    Поэтому utf-8 объявляем только когда в пробе есть настоящие
    многобайтовые последовательности utf-8. Во всех остальных случаях
    берём системную ANSI-страницу — ту, в которой клиент и пишет.
    """
    fallback = system_ansi()
    if not sample:
        return fallback
    try:
        text = sample.decode("utf-8")
    except UnicodeDecodeError:
        return fallback
    # Декодировалось как utf-8. Если при этом не-ASCII символов нет вовсе,
    # проба ничего не доказала: это ASCII, и он валиден в любой странице.
    return "utf-8" if any(ch > "" for ch in text) else fallback
