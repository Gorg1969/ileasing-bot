# modules/scheduler.py
"""
Планировщик на APScheduler.
- publish_random_post — публикует 1 пост из listings.db (можно вызвать вручную)
- cleanup_old_posts — удаляет старые посты при превышении лимита
- refresh_listings — скачивает свежую listings.db из ветки state
"""

import os
import random
import logging
import sqlite3
import time
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

import requests

logger = logging.getLogger(__name__)

LISTINGS_URL = "https://raw.githubusercontent.com/Gorg1969/ileasing-bot/state/data/listings.db"
LISTINGS_PATH = "data/listings.db"

POSTS_PER_DAY = 20
MAX_POSTS_IN_CHANNEL = 2000
CHAT_ID = None
PUBLISH_HOURS = list(range(8, 20))

_api = None
_db = None
_publisher = None
_description_gen = None


def init_scheduler(api, db, publisher, description_gen, chat_id: str):
    global _api, _db, _publisher, _description_gen, CHAT_ID
    _api = api
    _db = db
    _publisher = publisher
    _description_gen = description_gen
    CHAT_ID = chat_id
    logger.info(f"✅ Планировщик инициализирован (chat_id={chat_id})")


def refresh_listings():
    try:
        r = requests.get(LISTINGS_URL, timeout=30)
        if r.status_code == 200:
            os.makedirs(os.path.dirname(LISTINGS_PATH), exist_ok=True)
            with open(LISTINGS_PATH, "wb") as f:
                f.write(r.content)
            logger.info(f"✅ listings.db обновлена ({len(r.content)} байт)")
            return True
        logger.error(f"❌ Не удалось скачать listings.db: HTTP {r.status_code}")
        return False
    except Exception as e:
        logger.error(f"❌ Ошибка скачивания listings.db: {e}")
        return False


def get_random_pending_listing() -> dict:
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
    try:
        r = requests.get(url, timeout=30, verify=False)
        if r.status_code == 200:
            return r.content
        logger.warning(f"⚠️ Фото {url}: HTTP {r.status_code}")
        return None
    except Exception as e:
        logger.error(f"❌ Ошибка скачивания фото {url}: {e}")
        return None


def publish_random_post(force: bool = False):
    """
    Публикует один случайный пост из listings.db.
    force=True — ручной запуск (игнорирует проверки).
    """
    logger.info(f"📤 Запуск публикации (force={force})...")

    refresh_listings()

    listing = get_random_pending_listing()
    if not listing:
        logger.warning("⚠️ Нет карточек со статусом pending")
        return

    logger.info(f"📦 Выбрана карточка: {listing['title']}")

    post_text = _description_gen.generate_post(listing)

    image_token = None
    if listing.get("image"):
        image_bytes = download_image(listing["image"])
        if image_bytes:
            image_token = _api.upload_file(image_bytes, "photo.jpg")

    # ============ TEST_MODE ============
    test_mode = os.environ.get("TEST_MODE", "false").lower() == "true"
    target_chat = CHAT_ID

    if test_mode:
        # Читаем admin_id из файла
        admin_file = os.path.join(os.environ.get("DATA_DIR", "/app/data"), "admin_id.txt")
        try:
            if os.path.exists(admin_file):
                with open(admin_file) as f:
                    target_chat = f.read().strip()
                logger.info(f"🧪 TEST_MODE: публикация в личку {target_chat}")
            else:
                logger.error("❌ TEST_MODE включён, но admin_id не задан")
                return
        except Exception as e:
            logger.error(f"❌ Ошибка чтения admin_id: {e}")
            return

    # ============ ПУБЛИКАЦИЯ ============
    if image_token:
        ok = _api.send_message_with_attachments(target_chat, post_text, [image_token])
    else:
        # Для лички используем send_message, для канала — send_message_to_chat
        if test_mode:
            ok = _api.send_message(int(target_chat), post_text)
        else:
            ok = _api.send_message_to_chat(target_chat, post_text)

    if ok:
        mark_listing_published(listing["url"])

        _db.add_publication(
            listing_id=listing["id"],
            external_id=listing.get("external_id") or listing["url"],
            url=listing["url"],
            title=listing["title"],
            message_id="",
            chat_id=target_chat,
        )
        logger.info(f"✅ Опубликовано: {listing['title']}")
    else:
        logger.error(f"❌ Не удалось опубликовать: {listing['title']}")


def cleanup_old_posts():
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
            _db.mark_deleted(post["id"])
            continue

        success = _api.delete_message(message_id)
        if success:
            _db.mark_deleted(post["id"])
        else:
            try:
                admin_file = os.path.join(os.environ.get("DATA_DIR", "/app/data"), "admin_id.txt")
                if os.path.exists(admin_file):
                    with open(admin_file) as f:
                        admin_id = f.read().strip()
                    _api.send_message(
                        int(admin_id),
                        f"⚠️ **Не удалось удалить пост автоматически.**\n"
                        f"ID: `{message_id}`\n"
                        f"Название: {post.get('title')}\n"
                        f"Удалите вручную."
                    )
            except Exception as e:
                logger.error(f"❌ Не удалось уведомить админа: {e}")

        time.sleep(0.6)


def start_scheduler():
    scheduler = BackgroundScheduler(timezone="Europe/Moscow")

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

    scheduler.add_job(
        refresh_listings,
        CronTrigger(minute=0),
        id="refresh_listings",
        replace_existing=True,
    )

    scheduler.add_job(
        cleanup_old_posts,
        CronTrigger(hour=3, minute=0),
        id="cleanup_old_posts",
        replace_existing=True,
    )

    scheduler.start()
    logger.info("✅ Планировщик запущен")
    return scheduler
