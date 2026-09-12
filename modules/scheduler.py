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
    force
