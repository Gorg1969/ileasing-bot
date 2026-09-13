# modules/scheduler.py
"""
Планировщик на APScheduler.
- publish_random_post — публикует 1 пост из pending_queue (до 3 фото)
- cleanup_old_posts — удаляет старые посты при превышении лимита
- refresh_listings — скачивает свежую listings.db из ветки state
- apply_schedule — пересоздаёт задания по заданной частоте
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

MAX_POSTS_IN_CHANNEL = 2000
CHAT_ID = None
PUBLISH_HOURS = list(range(8, 20))   # часы, в которые можно публиковать

_api = None
_db = None
_publisher = None
_description_gen = None
_scheduler: BackgroundScheduler = None


def init_scheduler(api, db, publisher, description_gen, chat_id: str):
    global _api, _db, _publisher, _description_gen, CHAT_ID
    _api = api
    _db = db
    _publisher = publisher
    _description_gen = description_gen
    CHAT_ID = chat_id
    logger.info(f"✅ Планировщик инициализирован (chat_id={chat_id})")


def refresh_listings():
    logger.info("=" * 60)
    logger.info("🔄 refresh_listings: НАЧАЛО")
    logger.info(f"🔄 URL: {LISTINGS_URL}")

    try:
        r = requests.get(LISTINGS_URL, timeout=30)
        logger.info(f"🔄 HTTP {r.status_code}, размер ответа: {len(r.content)} байт")

        if r.status_code != 200:
            logger.error(f"❌ Не удалось скачать listings.db: HTTP {r.status_code}")
            return False

        os.makedirs(os.path.dirname(LISTINGS_PATH), exist_ok=True)
        with open(LISTINGS_PATH, "wb") as f:
            f.write(r.content)
        logger.info(f"✅ listings.db сохранена локально ({len(r.content)} байт)")

        try:
            conn = sqlite3.connect(LISTINGS_PATH, timeout=10)
            cols = [row[1] for row in conn.execute("PRAGMA table_info(listings)").fetchall()]
            logger.info(f"📋 Колонки в скачанной БД: {cols}")
            has_images_b64 = "images_base64" in cols
            has_b64 = "image_base64" in cols
            logger.info(f"📋 images_base64: {has_images_b64}, image_base64: {has_b64}")

            if has_images_b64:
                with_b64 = conn.execute(
                    "SELECT COUNT(*) FROM listings WHERE status='pending' AND images_base64 IS NOT NULL"
                ).fetchone()[0]
            elif has_b64:
                with_b64 = conn.execute(
                    "SELECT COUNT(*) FROM listings WHERE status='pending' AND image_base64 IS NOT NULL"
                ).fetchone()[0]
            else:
                with_b64 = 0

            pending_total = conn.execute(
                "SELECT COUNT(*) FROM listings WHERE status='pending'"
            ).fetchone()[0]
            logger.info(f"📊 Pending всего: {pending_total}, из них с base64: {with_b64}")
            conn.close()
        except Exception as e:
            logger.error(f"❌ Ошибка проверки скачанной БД: {e}")

        if _db:
            logger.info("🔄 Синхронизация pending_queue...")
            added = _db.sync_pending_from_listings(LISTINGS_PATH)
            logger.info(f"✅ В pending_queue добавлено: {added}")
            logger.info(f"📊 Всего в pending_queue: {_db.count_pending_queue()}")
        else:
            logger.warning("⚠️ _db не инициализирован, синхронизация пропущена")

        logger.info("🔄 refresh_listings: КОНЕЦ (успех)")
        logger.info("=" * 60)
        return True

    except Exception as e:
        logger.error(f"❌ Ошибка refresh_listings: {e}")
        import traceback
        traceback.print_exc()
        logger.info("=" * 60)
        return False


def upload_image_to_max(image_base64: str) -> str:
    """Загружает ОДНО изображение в MAX API из base64. Возвращает токен."""
    if not image_base64:
        return None

    try:
        image_bytes = base64.b64decode(image_base64)
        logger.info(f"📤 Загрузка в MAX: {len(image_bytes)} байт")

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
        token_from_step1 = upload_data.get('token')

        if not upload_url:
            logger.error(f"❌ Нет url в ответе: {upload_data}")
            return None

        files = {'data': ('photo.jpg', image_bytes, 'image/jpeg')}
        upload_response = requests.post(
            upload_url,
            files=files,
            timeout=60,
            verify=False
        )

        if upload_response.status_code != 200:
            logger.error(f"❌ Ошибка загрузки: {upload_response.status_code}")
            return None

        token = token_from_step1
        if not token:
            try:
                upload_result = upload_response.json()
                if 'photos' in upload_result and isinstance(upload_result['photos'], dict):
                    for key, photo_data in upload_result['photos'].items():
                        if isinstance(photo_data, dict) and 'token' in photo_data:
                            token = photo_data['token']
                            break
                if not token and 'token' in upload_result:
                    token = upload_result['token']
            except ValueError:
                logger.error("❌ Невалидный JSON ответа шага 2")
                return None

        return token

    except Exception as e:
        logger.error(f"❌ Ошибка загрузки в MAX: {e}")
        return None


def upload_multiple_images(listing: dict) -> list:
    """Загружает до 3 изображений из base64 и возвращает список токенов."""
    tokens = []

    # Пробуем images_base64 (новый формат — массив)
    images_b64_json = listing.get("images_base64")
    if images_b64_json:
        try:
            images_list = json.loads(images_b64_json)
            if isinstance(images_list, list) and images_list:
                for idx, b64 in enumerate(images_list[:3]):
                    logger.info(f"📤 Загрузка фото {idx+1}/{len(images_list[:3])}")
                    token = upload_image_to_max(b64)
                    if token:
                        tokens.append(token)
                        logger.info(f"✅ Токен {idx+1} получен")
                    else:
                        logger.warning(f"⚠️ Не удалось загрузить фото {idx+1}")
                return tokens
        except Exception as e:
            logger.error(f"❌ Ошибка парсинга images_base64: {e}")

    # Fallback: одно фото
    single_b64 = listing.get("image_base64")
    if single_b64:
        logger.info("📤 Fallback: загрузка одного фото")
        token = upload_image_to_max(single_b64)
        if token:
            tokens.append(token)

    return tokens


def publish_random_post(force: bool = False):
    logger.info("=" * 60)
    logger.info(f"📤 publish_random_post: НАЧАЛО (force={force})")

    refresh_listings()

    listing = _db.get_random_pending()
    if not listing:
        logger.warning("⚠️ pending_queue пуста")
        logger.info("=" * 60)
        return

    logger.info(f"📦 Выбрана карточка: {listing.get('title')}")

    # ============ ЗАГРУЗКА ФОТО (до 3) ============
    logger.info("-" * 60)
    logger.info("🖼️ ЭТАП: ЗАГРУЗКА ФОТО (до 3)")
    logger.info("-" * 60)

    image_tokens = upload_multiple_images(listing)
    logger.info(f"🧪 Итог: получено токенов {len(image_tokens)}")
    logger.info("-" * 60)

    # ============ ТЕКСТ ============
    post_text = _description_gen.generate_post(listing)

    # ============ TEST_MODE / БОЕВОЙ ============
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
                logger.error("❌ TEST_MODE, но admin_id не задан")
                logger.info("=" * 60)
                return
        except Exception as e:
            logger.error(f"❌ Ошибка чтения admin_id: {e}")
            logger.info("=" * 60)
            return
    else:
        logger.info(f"🔴 БОЕВОЙ РЕЖИМ: публикация в {target_chat}")

    # ============ ПУБЛИКАЦИЯ ============
    if image_tokens:
        logger.info(f"📤 Отправка С ФОТО ({len(image_tokens)} шт.) в {target_chat}")
        ok = _api.send_message_with_attachments(target_chat, post_text, image_tokens)
    else:
        logger.info(f"📤 Отправка БЕЗ фото в {target_chat}")
        if test_mode:
            ok = _api.send_message(int(target_chat), post_text)
        else:
            ok = _api.send_message_to_chat(target_chat, post_text)

    logger.info(f"📤 Результат: {'✅ OK' if ok else '❌ FAIL'}")

    # ============ ПОСТ-ОБРАБОТКА ============
    if ok:
        try:
            _db.add_publication(
                listing_id=listing.get("listing_id") or listing.get("id") or 0,
                external_id=listing.get("external_id") or listing["url"],
                url=listing["url"],
                title=listing["title"],
                message_id="",
                chat_id=target_chat,
            )
        except Exception as e:
            logger.warning(f"⚠️ add_publication: {e}")

        try:
            _db.remove_from_pending(listing["external_id"])
            logger.info(f"✅ Удалено из pending_queue")
        except Exception as e:
            logger.error(f"❌ remove_from_pending: {e}")

        logger.info(f"✅ ОПУБЛИКОВАНО: {listing['title']}")
    else:
        logger.error(f"❌ НЕ УДАЛОСЬ ОПУБЛИКОВАТЬ: {listing['title']}")

    logger.info("=" * 60)
    logger.info("📤 publish_random_post: КОНЕЦ")
    logger.info("=" * 60)


def cleanup_old_posts():
    logger.info("🗑️ cleanup_old_posts: НАЧАЛО")
    total = _db.count_published()
    logger.info(f"📊 Всего постов: {total}")

    if total <= MAX_POSTS_IN_CHANNEL:
        logger.info(f"✅ Лимит не превышен ({total}/{MAX_POSTS_IN_CHANNEL})")
        return

    excess = total - MAX_POSTS_IN_CHANNEL
    old_posts = _db.get_oldest_published(limit=excess)

    for post in old_posts:
        message_id = post.get("message_id")
        if not message_id:
            _db.mark_deleted(post["id"])
            continue

        success = _api.delete_message(message_id)
        if success:
            _db.mark_deleted(post["id"])
        time.sleep(0.6)

    logger.info("🗑️ cleanup_old_posts: КОНЕЦ")


def apply_schedule(posts_per_day: int):
    """
    Пересоздаёт задания публикации согласно частоте.
    
    - posts_per_day = 1..12 → по одному посту в случайные часы из PUBLISH_HOURS
    - posts_per_day = 13..24 → по 2 поста в час (12 часов × 2 = 24)
    - posts_per_day = 25..36 → по 3 поста в час
    - и т.д.
    """
    global _scheduler
    if _scheduler is None:
        logger.error("❌ Планировщик не инициализирован")
        return

    # Удаляем старые задачи публикации
    for job in _scheduler.get_jobs():
        if job.id.startswith("publish_"):
            _scheduler.remove_job(job.id)

    posts_per_day = max(1, min(int(posts_per_day), 48))
    hours = PUBLISH_HOURS  # 8..19 (12 часов)
    num_hours = len(hours)

    # Сколько постов на каждый час
    per_hour = posts_per_day / num_hours
    total_scheduled = 0

    random.seed()
    for hour in hours:
        count = per_hour
        # Распределяем равномерно
        if per_hour < 1:
            # Меньше 1 на час — публикуем только в случайные часы
            if random.random() < per_hour:
                count = 1
            else:
                count = 0
        else:
            count = int(per_hour)
            if random.random() < (per_hour - count):
                count += 1

        # Ограничение — не больше 4 постов в час
        count = min(count, 4)

        minutes_used = set()
        for i in range(count):
            # Случайная уникальная минута в часе
            for _ in range(10):
                minute = random.randint(0, 59)
                if minute not in minutes_used:
                    break
            minutes_used.add(minute)

            _scheduler.add_job(
                publish_random_post,
                CronTrigger(hour=hour, minute=minute),
                id=f"publish_{hour}_{i}",
                replace_existing=True,
            )
            total_scheduled += 1
            logger.info(f"⏰ Публикация #{total_scheduled}: {hour:02d}:{minute:02d} МСК")

    logger.info(f"✅ Расписание обновлено: {total_scheduled} публикаций/день")


def start_scheduler():
    global _scheduler
    logger.info("⏰ start_scheduler: НАЧАЛО")

    _scheduler = BackgroundScheduler(timezone="Europe/Moscow")

    # Расписание по умолчанию
    posts_per_day = _db.get_posts_per_day() if _db else 20
    apply_schedule(posts_per_day)

    _scheduler.add_job(
        refresh_listings,
        CronTrigger(minute=0),
        id="refresh_listings",
        replace_existing=True,
    )
    logger.info("⏰ refresh_listings: каждый час в :00")

    _scheduler.add_job(
        cleanup_old_posts,
        CronTrigger(hour=3, minute=0),
        id="cleanup_old_posts",
        replace_existing=True,
    )
    logger.info("⏰ cleanup_old_posts: ежедневно в 03:00 МСК")

    _scheduler.start()
    logger.info("✅ Планировщик запущен")
    logger.info("=" * 60)
    return _scheduler
