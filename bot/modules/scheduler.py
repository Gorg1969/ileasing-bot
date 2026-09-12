# bot/modules/scheduler.py
"""
Планировщик на APScheduler.

Задачи:
1. publish_random_post — публикует 1 пост из listings.db
2. cleanup_old_posts — удаляет старые посты при превышении лимита 2000
3. refresh_listings — скачивает свежую listings.db из ветки state
"""

import os
import random
import logging
import sqlite3
import time
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

import requests

logger = logging.getLogger(__name__)

# ========== НАСТРОЙКИ ==========
LISTINGS_URL = "https://raw.githubusercontent.com/Gorg1969/ileasing-bot/state/data/listings.db"
LISTINGS_PATH = "data/listings.db"

POSTS_PER_DAY = 20
MAX_POSTS_IN_CHANNEL = 2000
CHAT_ID = None  # Установим в app.py при инициализации

# Часы публикации (МСК): с 8 до 20
PUBLISH_HOURS = list(range(8, 20))  # 8,9,...,19

# ========== ГЛОБАЛЬНЫЕ ЗАВИСИМОСТИ ==========
_api = None
_db = None
_publisher = None
_description_gen = None


def init_scheduler(api, db, publisher, description_gen, chat_id: str):
    """Инициализация планировщика."""
    global _api, _db, _publisher, _description_gen, CHAT_ID
    _api = api
    _db = db
    _publisher = publisher
    _description_gen = description_gen
    CHAT_ID = chat_id
    logger.info(f"✅ Планировщик инициализирован (chat_id={chat_id})")


# ========== СКАЧИВАНИЕ listings.db ==========

def refresh_listings():
    """Скачивает свежую listings.db из ветки state."""
    try:
        r = requests.get(LISTINGS_URL, timeout=30)
        if r.status_code == 200:
            os.makedirs(os.path.dirname(LISTINGS_PATH), exist_ok=True)
            with open(LISTINGS_PATH, "wb") as f:
                f.write(r.content)
            logger.info(f"✅ listings.db обновлена ({len(r.content)} байт)")
            return True
        else:
            logger.error(f"❌ Не удалось скачать listings.db: HTTP {r.status_code}")
            return False
    except Exception as e:
        logger.error(f"❌ Ошибка скачивания listings.db: {e}")
        return False


# ========== ПУБЛИКАЦИЯ ==========

def get_random_pending_listing() -> dict:
    """Возвращает случайную карточку со статусом pending из listings.db."""
    if not os.path.exists(LISTINGS_PATH):
        logger.warning(f"⚠️ {LISTINGS_PATH} не найден")
        return None

    conn = sqlite3.connect(LISTINGS_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("""
            SELECT * FROM listings
            WHERE status = 'pending'
            ORDER BY RANDOM()
            LIMIT 1
        """).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def mark_listing_published(url: str):
    """Помечает карточку как опубликованную в listings.db."""
    conn = sqlite3.connect(LISTINGS_PATH, timeout=10)
    try:
        conn.execute(
            "UPDATE listings SET status = 'published' WHERE url = ?",
            (url,)
        )
        conn.commit()
    finally:
        conn.close()


def download_image(url: str) -> bytes:
    """Скачивает фото по URL."""
    try:
        r = requests.get(url, timeout=30, verify=False)
        if r.status_code == 200:
            return r.content
        logger.warning(f"⚠️ Фото {url}: HTTP {r.status_code}")
        return None
    except Exception as e:
        logger.error(f"❌ Ошибка скачивания фото {url}: {e}")
        return None


def publish_random_post():
    """Публикует один случайный пост из listings.db."""
    logger.info("📤 Запуск публикации...")

    # Обновляем listings.db
    refresh_listings()

    listing = get_random_pending_listing()
    if not listing:
        logger.warning("⚠️ Нет карточек со статусом pending")
        return

    logger.info(f"📦 Выбрана карточка: {listing['title']}")

    # Генерируем текст поста
    post_text = _description_gen.generate_post(listing)

    # Загружаем фото
    image_token = None
    if listing.get("image"):
        image_bytes = download_image(listing["image"])
        if image_bytes:
            image_token = _api.upload_file(image_bytes, "photo.jpg")

    # Публикуем
    if image_token:
        ok = _api.send_message_with_attachments(CHAT_ID, post_text, [image_token])
    else:
        ok = _api.send_message_to_chat(CHAT_ID, post_text)

    if ok:
        # Помечаем в listings.db как опубликованную
        mark_listing_published(listing["url"])

        # Записываем в published.db
        _db.add_publication(
            listing_id=listing["id"],
            external_id=listing.get("external_id") or listing["url"],
            url=listing["url"],
            title=listing["title"],
            message_id="",  # message_id мы получаем из вебхука позже
            chat_id=CHAT_ID,
        )
        logger.info(f"✅ Опубликовано: {listing['title']}")
    else:
        logger.error(f"❌ Не удалось опубликовать: {listing['title']}")


# ========== ОЧИСТКА СТАРЫХ ПОСТОВ ==========

def cleanup_old_posts():
    """Удаляет старые посты, если их больше MAX_POSTS_IN_CHANNEL."""
    total = _db.count_published()
    logger.info(f"📊 Всего постов в канале (по БД): {total}")

    if total <= MAX_POSTS_IN_CHANNEL:
        logger.info(f"✅ Лимит не превышен ({total}/{MAX_POSTS_IN_CHANNEL})")
        return

    excess = total - MAX_POSTS_IN_CHANNEL
    logger.info(f"🗑️ Нужно удалить: {excess} постов")

    old_posts = _db.get_oldest_published(limit=excess)

    for post in old_posts:
        message_id = post.get("message_id")
        if not message_id:
            logger.warning(f"⚠️ Нет message_id для {post.get('title')}")
            _db.mark_deleted(post["id"])
            continue

        # Пробуем удалить через API
        success = _api.delete_message(message_id)
        if success:
            _db.mark_deleted(post["id"])
            logger.info(f"🗑️ Удалён пост: {post.get('title')}")
        else:
            logger.error(f"❌ Не удалось удалить: {post.get('title')}")
            # Уведомляем админа
            try:
                _api.send_message(
                    post.get("chat_id") or CHAT_ID,
                    f"⚠️ **Не удалось удалить пост автоматически.**\n"
                    f"ID: `{message_id}`\n"
                    f"Название: {post.get('title')}\n"
                    f"Удалите вручную."
                )
            except Exception as e:
                logger.error(f"❌ Не удалось уведомить админа: {e}")

        time.sleep(0.6)  # Лимит 2 удаления в секунду


# ========== ЗАПУСК ==========

def start_scheduler():
    """Запускает планировщик с задачами."""
    scheduler = BackgroundScheduler(timezone="Europe/Moscow")

    # 1. Публикация — 20 раз в день, случайные минуты в часы 8-19
    # Берём по одной случайной минуте на каждый час
    random.seed()
    for hour in PUBLISH_HOURS:
        minute = random.randint(0, 59)
        scheduler.add_job(
            publish_random_post,
            CronTrigger(hour=hour, minute=minute),
            id=f"publish_{hour}",
            replace_existing=True,
        )
        logger.info(f"⏰ Публикация запланирована на {hour:02d}:{minute:02d} МСК")

    # 2. Обновление listings.db — раз в час
    scheduler.add_job(
        refresh_listings,
        CronTrigger(minute=0),  # каждый час в 00 минут
        id="refresh_listings",
        replace_existing=True,
    )

    # 3. Очистка старых постов — раз в день в 03:00 МСК
    scheduler.add_job(
        cleanup_old_posts,
        CronTrigger(hour=3, minute=0),
        id="cleanup_old_posts",
        replace_existing=True,
    )

    scheduler.start()
    logger.info("✅ Планировщик запущен")
    return scheduler
