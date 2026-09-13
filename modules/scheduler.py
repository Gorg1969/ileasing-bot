# modules/scheduler.py 3
"""
Планировщик на APScheduler.
- publish_random_post — публикует 1 пост из pending_queue (с base64 фото)
- cleanup_old_posts — удаляет старые посты при превышении лимита
- refresh_listings — скачивает свежую listings.db из ветки state и синхронизирует pending_queue
"""

import os
import io
import json
import base64
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
    """
    Скачивает listings.db из ветки state и синхронизирует pending_queue
    в БД бота (published.db).
    """
    try:
        r = requests.get(LISTINGS_URL, timeout=30)
        if r.status_code != 200:
            logger.error(f"❌ Не удалось скачать listings.db: HTTP {r.status_code}")
            return False

        os.makedirs(os.path.dirname(LISTINGS_PATH), exist_ok=True)
        with open(LISTINGS_PATH, "wb") as f:
            f.write(r.content)
        logger.info(f"✅ listings.db обновлена ({len(r.content)} байт)")

        # ✅ Синхронизируем pending_queue в БД бота
        if _db:
            added = _db.sync_pending_from_listings(LISTINGS_PATH)
            logger.info(f"✅ В pending_queue добавлено: {added}")
            logger.info(f"📊 Всего в pending_queue: {_db.count_pending_queue()}")

        return True
    except Exception as e:
        logger.error(f"❌ Ошибка скачивания listings.db: {e}")
        return False


def upload_image_to_max(image_base64: str) -> str:
    """Загружает изображение в MAX API из base64. Возвращает токен."""
    if not image_base64:
        return None

    try:
        image_bytes = base64.b64decode(image_base64)
        logger.info(f"📤 Загрузка в MAX: {len(image_bytes)} байт")

        # ШАГ 1: Получаем URL
        response = requests.post(
            f"{_api.base_url}/uploads",
            headers={"Authorization": _api.token},
            params={"type": "image"},
            timeout=30,
            verify=False
        )

        if response.status_code != 200:
            logger.error(f"❌ Ошибка /uploads: {response.status_code} - {response.text[:200]}")
            return None

        upload_data = response.json()
        upload_url = upload_data.get('url')

        if not upload_url:
            logger.error(f"❌ Нет url в ответе: {upload_data}")
            return None

        # ШАГ 2: Загружаем файл
        files = {'data': ('photo.jpg', image_bytes, 'image/jpeg')}
        upload_response = requests.post(
            upload_url,
            files=files,
            timeout=60,
            verify=False
        )

        if upload_response.status_code != 200:
            logger.error(f"❌ Ошибка загрузки: {upload_response.status_code} - {upload_response.text[:200]}")
            return None

        upload_result = upload_response.json()
        logger.info(f"📤 Ответ шага 2: {str(upload_result)[:300]}")

        # Извлекаем токен
        token = None
        if 'photos' in upload_result and isinstance(upload_result['photos'], dict):
            for key, photo_data in upload_result['photos'].items():
                if isinstance(photo_data, dict) and 'token' in photo_data:
                    token = photo_data['token']
                    break

        if not token and 'token' in upload_result:
            token = upload_result['token']

        if token:
            logger.info(f"✅ Токен получен: {token[:30]}...")
        else:
            logger.error(f"❌ Токен не найден: {upload_result}")

        return token

    except Exception as e:
        logger.error(f"❌ Ошибка загрузки в MAX: {e}")
        import traceback
        traceback.print_exc()
        return None


def publish_random_post(force: bool = False):
    """Публикует один случайный пост из pending_queue."""
    logger.info(f"📤 Запуск публикации (force={force})...")

    # Обновляем очередь
    refresh_listings()

    # Берём из pending_queue
    listing = _db.get_random_pending()
    if not listing:
        logger.warning("⚠️ pending_queue пуста — нет карточек для публикации")
        return

    logger.info(f"📦 Выбрана карточка: {listing['title']}")

    # ============ ФОТО ИЗ BASE64 ============
    image_token = None
    image_base64 = listing.get("image_base64")

    if image_base64:
        logger.info(f"🖼️ base64 найден: {len(image_base64)} символов")
        image_token = upload_image_to_max(image_base64)
        logger.info(f"🎫 Токен после upload: {image_token!r}")
    else:
        logger.warning("⚠️ В очереди нет base64 для этой карточки")

    logger.info(f"🧪 Итог: image_token={'ЕСТЬ' if image_token else 'НЕТ'}")
    # ==========================================

    post_text = _description_gen.generate_post(listing)

    # ============ TEST_MODE ============
    test_mode = os.environ.get("TEST_MODE", "false").lower() == "true"
    target_chat = CHAT_ID

    if test_mode:
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
        logger.info(f"📤 Отправка с фото в {target_chat}")
        ok = _api.send_message_with_attachments(target_chat, post_text, [image_token])
    else:
        logger.info(f"📤 Отправка БЕЗ фото в {target_chat}")
        if test_mode:
            ok = _api.send_message(int(target_chat), post_text)
        else:
            ok = _api.send_message_to_chat(target_chat, post_text)

    if ok:
        # ✅ Удаляем из очереди (base64 уходит вместе с записью)
        _db.remove_from_pending(listing["external_id"])

        # Запись для будущего удаления из канала
        _db.add_publication(
            listing_id=listing.get("listing_id") or 0,
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
