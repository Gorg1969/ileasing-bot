# parser/parser.py
"""
Парсер каталога ileasing.ru на Playwright.

Логика:
1. Случайно выбирает подкатегорию из ALLOWED_CATEGORIES.
2. Обходит страницы каталога (?PAGEN_1=N), собирает карточки.
3. Фильтрует: цена >= MIN_PRICE, href ещё нет в БД.
4. Для новых карточек: заходит в карточку, скачивает ВСЕ фото через браузер.
5. Сохраняет: listings.db + data/images/{external_id}/*.jpg + base64 первого фото.
6. Цель — 20 новых карточек за запуск. Лимит — 5 категорий.
"""

import os
import re
import json
import random
import logging
import asyncio
import io
import base64
from typing import Optional
from urllib.parse import urljoin

from playwright.async_api import async_playwright, Page, TimeoutError as PWTimeoutError

logger = logging.getLogger(__name__)

BASE_URL = "https://www.ileasing.ru"
MIN_PRICE = 2_200_000
TARGET_NEW = 20
MAX_CATEGORIES = 5
IMAGES_DIR = "data/images"
MAX_IMAGES = 10

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
    if not text:
        return None
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else None


def convert_to_jpeg(image_bytes: bytes) -> Optional[bytes]:
    """Конвертирует любое изображение в JPEG (MAX не поддерживает WebP)."""
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(image_bytes))
        if img.mode in ('RGBA', 'P'):
            img = img.convert('RGB')
        output = io.BytesIO()
        img.save(output, format='JPEG', quality=85)
        return output.getvalue()
    except Exception as e:
        logger.warning(f"⚠️ Ошибка конвертации в JPEG: {e}")
        return None


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
            "images_downloaded": 0,
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
                ignore_https_errors=True,   # ✅ ИСПРАВЛЕНИЕ: игнорируем ошибки SSL (сертификат Минцифры)
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
                    await self._process_category(context, page, category, stats)
                except Exception as e:
                    logger.error(f"❌ Ошибка в категории {category}: {e}")
                    continue

            await browser.close()

        logger.info(f"📊 Итог парсинга: {stats}")
        return stats

    async def _process_category(self, context, page: Page, category: str, stats: dict):
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
                logger.info(f"   ⛔ Нет карточек на странице {page_num}")
                return

            cards = await page.query_selector_all("a.l-catalog-item")
            if not cards:
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

                # Скачиваем фото, получаем base64 первого
                images_count, first_base64 = await self._download_images_for_listing(
                    context, data["external_id"], data["href"]
                )
                stats["images_downloaded"] += images_count

                images_path = json.dumps([
                    f"data/images/{data['external_id']}/{i+1}.jpg"
                    for i in range(images_count)
                ])

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
                    images_path=images_path,
                    image_base64=first_base64,
                    category=category,
                )
                stats["added"] += 1
                logger.info(f"   ✅ [{stats['added']}/{TARGET_NEW}] {data['title']} — {data['price_text']} ({images_count} фото, base64={'ЕСТЬ' if first_base64 else 'НЕТ'})")

            page_num += 1

    async def _download_images_for_listing(self, context, external_id: str, card_url: str):
        """
        Открывает карточку товара, собирает все фото из галереи,
        конвертирует в JPEG, сохраняет в файлы и возвращает base64 первого.
        
        Returns:
            (downloaded_count: int, first_image_base64: str | None)
        """
        save_dir = os.path.join(IMAGES_DIR, external_id)
        os.makedirs(save_dir, exist_ok=True)

        page = await context.new_page()
        try:
            logger.info(f"   📸 Открываю карточку: {card_url}")
            await page.goto(card_url, wait_until="domcontentloaded", timeout=30000)

            try:
                await page.wait_for_selector("a.l-catalog-card__gallery-item", timeout=10000)
            except PWTimeoutError:
                logger.warning(f"   ⚠️ Галерея не найдена в карточке")
                return 0, None

            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(2000)

            photo_urls = await page.evaluate("""
                () => {
                    const urls = new Set();
                    document.querySelectorAll('a.l-catalog-card__gallery-item img, [data-fancybox="gallery-card"] img').forEach(img => {
                        if (img.src && !img.src.includes('data:image')) {
                            urls.add(img.src);
                        }
                    });
                    return Array.from(urls);
                }
            """)

            logger.info(f"   📸 Найдено {len(photo_urls)} фото в галерее")

            downloaded = 0
            first_base64 = None
            
            for i, url in enumerate(photo_urls[:MAX_IMAGES]):
                try:
                    full_url = urljoin(BASE_URL, url)
                    # ✅ ИСПРАВЛЕНИЕ: увеличен таймаут до 30 сек (фото медленно отдаются)
                    response = await context.request.get(full_url, timeout=30000)
                    if response.status == 200:
                        content = await response.body()
                        if not content:
                            continue

                        sig = content[:12]
                        if sig[:3] == b'\xff\xd8\xff':
                            jpeg_bytes = content
                        elif sig[:8] == b'\x89PNG\r\n\x1a\n':
                            jpeg_bytes = convert_to_jpeg(content)
                        elif sig[:4] == b'RIFF' and sig[8:12] == b'WEBP':
                            jpeg_bytes = convert_to_jpeg(content)
                        else:
                            jpeg_bytes = convert_to_jpeg(content)

                        if jpeg_bytes:
                            filepath = os.path.join(save_dir, f"{i+1}.jpg")
                            with open(filepath, "wb") as f:
                                f.write(jpeg_bytes)
                            downloaded += 1
                            
                            # ✅ Сохраняем base64 первого фото
                            if first_base64 is None:
                                first_base64 = base64.b64encode(jpeg_bytes).decode('ascii')
                                logger.info(f"   ✅ Фото {i+1}: сохранено ({len(jpeg_bytes)} байт), base64 первого готов")
                except Exception as e:
                    logger.warning(f"   ⚠️ Ошибка скачивания фото {i+1}: {e}")

            return downloaded, first_base64

        except Exception as e:
            logger.error(f"   ❌ Ошибка в карточке {card_url}: {e}")
            return 0, None
        finally:
            await page.close()

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
    headless = os.environ.get("HEADLESS", "true").lower() != "false"
    logger.info(f"🚀 Запуск парсера (headless={headless})")
    db = ParserDB("data/listings.db")
    result = asyncio.run(run_parser(db, headless=headless))
    print("\n📊 Статистика:", result)
    print("📦 В БД:", db.stats())
