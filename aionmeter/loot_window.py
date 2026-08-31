"""Окно добычи: кто что залутал, кинах, броски кубика.

В логе предмет записан только номером: `[item:167000522;ver6;;;;]`. Таблица
названий лежит в зашифрованном `Data/Items/items.pak`, но те же самые номера
открытым текстом есть у эмуляторов Aion — оттуда их и берёт утилита
tools/fetch_item_names.py. На живом логе опознаётся 231 предмет из 234.

Если базу не собирали, окно показывает номера и не ломается.
"""

from __future__ import annotations

import re

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import QWidget

from . import itemdb
from .theme import (ACCENT, GAP, HAIR, INK, INK2, INK3, INK_MUTE, L1, L2, PAD,
                    RULE, fmt_ui)

RE_ITEM = re.compile(r"\[item:(\d+)")


class LootWindow(QWidget):
    """Отдельное окно, не оверлей: его открывают и читают, а не косятся на него."""

    def __init__(self, engine, cfg: dict, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.cfg = cfg
        self.setWindowTitle("AionMeter — добыча")
        self.setMinimumSize(360, 260)
        self.resize(460, 420)
        self._apply_font()

    def _apply_font(self) -> None:
        size = int(self.cfg.get("font_size", 12))
        self.f_body = QFont("Segoe UI", size)
        self.f_bold = QFont("Segoe UI", size, QFont.DemiBold)
        self.f_small = QFont("Segoe UI", max(7, size - 3))
        self.f_caps = QFont("Segoe UI", max(6, size - 4), QFont.DemiBold)
        self.f_caps.setCapitalization(QFont.AllUppercase)
        self.f_num = QFont("Segoe UI", max(7, size - 1))
        fm = QFontMetrics(self.f_body)
        self.ROW_H = fm.height() + 8
        self.HEAD_H = fm.height() + 10

    def _item(self, item: str) -> tuple[str, QColor]:
        """Название и цвет качества. Без базы — номер серым."""
        m = RE_ITEM.search(item)
        if not m:
            return item, INK2
        item_id = m.group(1)
        name, quality, _group = itemdb.lookup(item_id)
        if not name:
            return f"предмет {item_id}", INK3
        return name, QColor(itemdb.QUALITY_COLOURS.get(quality, "#E9EEF3"))

    def showEvent(self, e) -> None:
        self._apply_font()
        super().showEvent(e)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, L1)

        snap = self.engine.snapshot()
        items: dict[str, dict] = snap.get("items", {}) or {}
        loot = snap.get("loot", {}) or {}
        rolls = snap.get("rolls", []) or []

        # шапка
        p.fillRect(QRect(0, 0, w, self.HEAD_H), L2)
        fm = QFontMetrics(self.f_bold)
        p.setFont(self.f_bold)
        p.setPen(INK)
        p.drawText(PAD, (self.HEAD_H + fm.ascent() - fm.descent()) // 2, "Добыча за сессию")
        line = []
        for key, label in (("kinah_in", "кинах"), ("ap", "AP"), ("exp", "опыт")):
            if loot.get(key):
                mant, suf = fmt_ui(loot[key])
                line.append(f"{label} {mant}{suf}")
        p.setFont(self.f_small)
        p.setPen(INK3)
        p.drawText(QRect(0, 0, w - PAD, self.HEAD_H),
                   Qt.AlignRight | Qt.AlignVCenter, "   ".join(line))
        p.fillRect(QRect(0, self.HEAD_H, w, 1), RULE)

        y = self.HEAD_H + 1
        fm_small = QFontMetrics(self.f_small)

        if not items:
            p.setFont(self.f_small)
            p.setPen(INK3)
            p.drawText(QRect(PAD, y + 16, w - PAD * 2, 60),
                       Qt.AlignHCenter | Qt.TextWordWrap,
                       "пока никто ничего не подобрал")
            return

        if not itemdb.load():
            p.setFont(self.f_small)
            p.setPen(INK3)
            p.drawText(QRect(PAD, h - 22, w - PAD * 2, 18), Qt.AlignHCenter,
                       "названия предметов: py tools/fetch_item_names.py")

        # по игрокам, сверху тот, кто взял больше
        order = sorted(items.items(), key=lambda kv: -sum(kv[1].values()))
        for who, bag in order:
            if y + self.ROW_H > h - 4:
                break
            count = sum(bag.values())
            is_self = who == "You"
            if is_self:
                p.fillRect(QRect(0, y, 3, self.ROW_H), ACCENT)
            p.setFont(self.f_bold if is_self else self.f_body)
            p.setPen(INK)
            base = y + (self.ROW_H + QFontMetrics(self.f_body).ascent()
                        - QFontMetrics(self.f_body).descent()) // 2
            p.drawText(PAD + 6, base, who)
            p.setFont(self.f_num)
            p.setPen(INK2)
            p.drawText(QRect(0, y, w - PAD, self.ROW_H), Qt.AlignRight | Qt.AlignVCenter,
                       f"{count} шт.")
            y += self.ROW_H

            # предметы этого игрока
            p.setFont(self.f_small)
            for item, n in sorted(bag.items(), key=lambda kv: -kv[1])[:12]:
                if y + fm_small.height() + 3 > h - 4:
                    break
                label, colour = self._item(item)
                p.setPen(colour)
                p.drawText(PAD + 22, y + fm_small.ascent(),
                           fm_small.elidedText(label, Qt.ElideRight, int(w * 0.6)))
                if n > 1:
                    p.setPen(INK3)
                    p.drawText(QRect(0, y, w - PAD, fm_small.height() + 3),
                               Qt.AlignRight | Qt.AlignVCenter, f"x{n}")
                y += fm_small.height() + 3
            p.fillRect(QRect(PAD, y + 2, w - PAD * 2, 1), HAIR)
            y += 6

        if rolls and y + 40 < h:
            p.setFont(self.f_caps)
            p.setPen(INK3)
            p.drawText(PAD, y + fm_small.ascent() + 2, "БРОСКИ КУБИКА")
            y += fm_small.height() + 6
            p.setFont(self.f_small)
            for who, value in reversed(rolls[-8:]):
                if y + fm_small.height() > h - 4:
                    break
                p.setPen(INK2)
                p.drawText(PAD + 8, y + fm_small.ascent(), who)
                p.setPen(INK if value >= 90 else INK3)
                p.drawText(QRect(0, y, w - PAD, fm_small.height()),
                           Qt.AlignRight | Qt.AlignVCenter, str(value))
                y += fm_small.height() + 2
