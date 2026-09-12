# parser/parser.py
"""
Парсер каталога ileasing.ru на Playwright.

Логика:
1. Случайно выбирает подкатегорию из ALLOWED_CATEGORIES.
2. Обходит страницы каталога (?PAGEN_1=N), собирает карточки.
3. Фильтрует: цена >= MIN_PRICE, href ещё нет в БД.
4. Новые добавляет в listings со статусом pending.
5. Цель — 20 новых карточек за запуск. Лимит — 5 категорий.
"""

import re
import random
import logging
import asyncio
from typing import Optional
from urllib.parse import urljoin

from playwright.async_api import async_playwright, Page, TimeoutError as PWTimeoutError

logger = logging.getLogger(__name__)

BASE_URL = "https://www.ileasing.ru"
MIN_PRICE = 2_200_000
TARGET_NEW = 20
MAX_CATEGORIES = 5

ALLOWED_CATEGORIES = [
    "/catalog/car/sedan/",
    "/catalog/car/universal/",
    "/catalog/car/hetchbek/",
    "/catalog/car/krossover/",
    "/catalog/car/vnedorozhnik/",
    "/catalog/car/kupe/",
    "/catalog/car/liftbek/",
    "/catalog/car/miniven/",
    "/catalog/commercial-vehicles/furgon/",
    "/catalog/commercial-vehicles/bortovye/",
    "/catalog/commercial-vehicles/pikap/",
    "/catalog/commercial-vehicles/shassi/",
    "/catalog/freight-transport/gruzovye-avtomobili/",
    "/catalog/freight-transport/pritsepy-i-polupritsepy/",
    "/catalog/freight-transport/sedelnye-tyagachi/",
    "/catalog/bus/avtobusy/",
    "/catalog/bus/mikroavtobusy/",
    "/catalog/bus/vakhtovye-avtobusy/",
    "/catalog/agricultural-machinery/kombayny/",
    "/catalog/agricultural-machinery/traktory/",
    "/catalog/agricultural-machinery/borony/",
    "/catalog/agricultural-machinery/zhatki/",
    "/catalog/agricultural-machinery/kosilki/",
    "/catalog/agricultural-machinery/plugi/",
    "/catalog/agricultural-machinery/polivalnye-mashiny/",
    "/catalog/agricultural-machinery/posevnoe-oborudovanie/",
    "/catalog/agricultural-machinery/pr-selkhoztekhnika/",
    "/catalog/special-machinery/dorozhno-stroitelnaya-tekhnika/",
    "/catalog/special-machinery/kommunalnaya-tekhnika/",
    "/catalog/special-machinery/spetsializirovannaya-tekhnika/",
    "/catalog/special-machinery/lesozagotovitelnaya-tekhnika/",
    "/catalog/special-machinery/skladskaya-tekhnika/",
]


def parse_price(text: str) -> Optional[int]:
    """'от 3 243 427 ₽' -> 3243427."""
    if not text:
        return None
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else None


class ILeasingParser:
    def __init__(self, db, headless: bool = True):
        self.db = db
        self.headless = headless

    async def run(self) -> dict:
        stats = {
            "categories_tried": 0,
            "pages_visited": 0,
            "cards_seen": 0,
            "skipped_price": 0,
            "skipped_dup": 0,
            "added": 0,
        }

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=self.headless)
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/122.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1440, "height": 900},
            )
            page = await context.new_page()

            tried_categories = set()

            while stats["added"] < TARGET_NEW and stats["categories_tried"] < MAX_CATEGORIES:
                available = [c for c in ALLOWED_CATEGORIES if c not in tried_categories]
                if not available:
                    logger.info("Все категории исчерпаны")
                    break

                category = random.choice(available)
                tried_categories.add(category)
                stats["categories_tried"] += 1

                logger.info(f"🎲 Категория {stats['categories_tried']}/{MAX_CATEGORIES}: {category}")

                try:
                    await self._process_category(page, category, stats)
                except Exception as e:
                    logger.error(f"❌ Ошибка в категории {category}: {e}")
                    continue

            await browser.close()

        logger.info(f"📊 Итог парсинга: {stats}")
        return stats

    async def _process_category(self, page: Page, category: str, stats: dict):
        page_num = 1
        while stats["added"] < TARGET_NEW:
            url = f"{BASE_URL}{category}?PAGEN_1={page_num}"
            logger.info(f"   📄 Страница {page_num}: {url}")

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            except PWTimeoutError:
                logger.warning(f"   ⏱ Таймаут на {url}, пропускаем")
                return

            try:
                await page.wait_for_selector("a.l-catalog-item", timeout=10000)
            except PWTimeoutError:
                logger.info(f"   ⛔ Нет карточек на странице {page_num}, конец категории")
                return

            cards = await page.query_selector_all("a.l-catalog-item")
            if not cards:
                logger.info(f"   ⛔ Пустая страница {page_num}")
                return

            stats["pages_visited"] += 1

            for card in cards:
                if stats["added"] >= TARGET_NEW:
                    return

                data = await self._extract_card(card)
                if not data:
                    continue

                stats["cards_seen"] += 1

                if data["price_value"] is None or data["price_value"] < MIN_PRICE:
                    stats["skipped_price"] += 1
                    continue

                if self.db.listing_exists(data["href"]):
                    stats["skipped_dup"] += 1
                    continue

                self.db.add_listing(
                    external_id=data["external_id"],
                    url=data["href"],
                    title=data["title"],
                    price=data["price_text"],
                    price_value=data["price_value"],
                    leasing=data["leasing_text"],
                    engine=data["props"].get("Двигатель"),
                    transmission=data["props"].get("Коробка передач"),
                    power=data["props"].get("Мощность, л.с."),
                    volume=data["props"].get("Объём двигателя"),
                    drive=data["props"].get("Привод"),
                    seats=data["props"].get("Количество мест"),
                    image=data["image"],
                    category=category,
                )
                stats["added"] += 1
                logger.info(f"   ✅ [{stats['added']}/{TARGET_NEW}] {data['title']} — {data['price_text']}")

            page_num += 1

    async def _extract_card(self, card) -> Optional[dict]:
        try:
            href = await card.get_attribute("href")
            if not href:
                return None
            full_url = urljoin(BASE_URL, href)

            el_id = await card.get_attribute("id") or ""
            m = re.search(r"_(\d+)_", el_id)
            external_id = m.group(1) if m else href

            title_el = await card.query_selector(".l-catalog-item__name")
            title = (await title_el.inner_text()).strip() if title_el else ""

            price_el = await card.query_selector(".l-catalog-item__price-value")
            price_text = (await price_el.inner_text()).strip() if price_el else ""
            price_value = parse_price(price_text)

            leasing_el = await card.query_selector(".l-catalog-item__price-leasing")
            leasing_text = (await leasing_el.inner_text()).strip() if leasing_el else ""

            props = {}
            prop_items = await card.query_selector_all(".l-catalog-item__props-item")
            for item in prop_items:
                name_el = await item.query_selector(".l-catalog-item__props-name")
                value_el = await item.query_selector(".l-catalog-item__props-value")
                if name_el and value_el:
                    name = (await name_el.inner_text()).strip()
                    value = (await value_el.inner_text()).strip()
                    props[name] = value

            img_el = await card.query_selector(".l-catalog-item__image img")
            image = ""
            if img_el:
                src = await img_el.get_attribute("src")
                if src:
                    image = urljoin(BASE_URL, src)

            return {
                "href": full_url,
                "external_id": external_id,
                "title": title,
                "price_text": price_text,
                "price_value": price_value,
                "leasing_text": leasing_text,
                "props": props,
                "image": image,
            }
        except Exception as e:
            logger.warning(f"⚠️ Ошибка извлечения карточки: {e}")
            return None


async def run_parser(db, headless: bool = True) -> dict:
    parser = ILeasingParser(db, headless=headless)
    return await parser.run()


if __name__ == "__main__":
    from parser.database import ParserDB
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    db = ParserDB("data/listings.db")
    result = asyncio.run(run_parser(db, headless=False))
    print("\n📊 Статистика:", result)
    print("📦 В БД:", db.stats())
