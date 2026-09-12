# bot/modules/description_gen.py
"""
Генерация поста для канала MAX из характеристик карточки.
Шаблон собирается из полей listings.
"""

import logging

logger = logging.getLogger(__name__)

# Ссылка на страницу с формой консультации
CONSULTATION_URL = "https://maxbot.bothost.tech/consultation"


def generate_post(listing: dict) -> str:
    """
    Собирает текст поста из карточки listings.
    
    listing — dict со полями:
        title, price, leasing, engine, transmission,
        power, volume, drive, seats, url
    """
    title = listing.get("title") or "Без названия"
    price = listing.get("price") or "—"
    leasing = listing.get("leasing") or ""
    engine = listing.get("engine") or "—"
    transmission = listing.get("transmission") or "—"
    power = listing.get("power") or "—"
    volume = listing.get("volume") or "—"
    drive = listing.get("drive") or "—"

    lines = []
    lines.append(f"**{title}**")
    lines.append("")
    lines.append(f"**Цена: {price}**")
    if leasing:
        lines.append(f"Лизинг: {leasing}")
    lines.append("")
    lines.append(f"🔧 Двигатель: {engine}")
    lines.append(f"⚙️ КПП: {transmission}")
    lines.append(f"🐎 Мощность: {power} л.с.")
    lines.append(f"📦 Объём: {volume} л")
    lines.append(f"🚗 Привод: {drive}")
    lines.append("")
    lines.append(f"**Записаться на [КОНСУЛЬТАЦИЮ]({CONSULTATION_URL})**")

    return "\n".join(lines)


def generate_post_short(listing: dict) -> str:
    """Короткая версия — только название и цена."""
    title = listing.get("title") or "Без названия"
    price = listing.get("price") or "—"
    return f"**{title}**\n\nЦена: **{price}**"
