# modules/scheduler.py 4
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

        # Проверяем, что скачали валидный SQLite и есть ли колонка
        try:
            conn = sqlite3.connect(LISTINGS_PATH, timeout=10)
            cols = [row[1] for row in conn.execute("PRAGMA table_info(listings)").fetchall()]
            logger.info(f"📋 Колонки в скачанной БД: {cols}")
            has_b64 = "image_base64" in cols
            logger.info(f"📋 image_base64 присутствует: {has_b64}")
            
            if has_b64:
                with_b64 = conn.execute(
                    "SELECT COUNT(*) FROM listings WHERE status='pending' AND image_base64 IS NOT NULL"
                ).fetchone()[0]
                pending_total = conn.execute(
                    "SELECT COUNT(*) FROM listings WHERE status='pending'"
                ).fetchone()[0]
                logger.info(f"📊 Pending всего: {pending_total}, из них с base64: {with_b64}")
            conn.close()
        except Exception as e:
            logger.error(f"❌ Ошибка проверки скачанной БД: {e}")

        # ✅ Синхронизируем pending_queue в БД бота
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
    """
    Загружает изображение в MAX API из base64. Возвращает токен.
    
    Согласно документации MAX :
    - ШАГ 1: POST /uploads?type=image → получаем url (и, возможно, token)
    - ШАГ 2: POST upload_url → загружаем файл
    - Токен для изображений приходит либо на шаге 1, либо на шаге 2
    """
    logger.info("=" * 60)
    logger.info("📤 upload_image_to_max: НАЧАЛО")
    
    if not image_base64:
        logger.warning("⚠️ upload_image_to_max: пустой base64")
        logger.info("=" * 60)
        return None

    try:
        # Декодируем base64
        logger.info(f"📤 base64 длина: {len(image_base64)} символов")
        image_bytes = base64.b64decode(image_base64)
        logger.info(f"📤 Декодировано: {len(image_bytes)} байт")
        
        # Проверяем сигнатуру
        sig = image_bytes[:12]
        logger.info(f"📤 Первые 12 байт (hex): {sig.hex()}")
        
        if sig[:3] == b'\xff\xd8\xff':
            logger.info("📤 Формат: JPEG ✅")
        elif sig[:8] == b'\x89PNG\r\n\x1a\n':
            logger.info("📤 Формат: PNG ✅")
        elif sig[:4] == b'RIFF' and sig[8:12] == b'WEBP':
            logger.error("📤 Формат: WebP ❌ (MAX не поддерживает!)")
            logger.info("=" * 60)
            return None
        elif sig[:3] == b'GIF':
            logger.info("📤 Формат: GIF ✅")
        else:
            logger.warning(f"📤 Формат неизвестен, hex: {sig.hex()}")
        
        # Проверяем размер
        size_mb = len(image_bytes) / (1024 * 1024)
        logger.info(f"📤 Размер: {size_mb:.2f} МБ")
        if size_mb > 50:
            logger.error("📤 Размер > 50 МБ — MAX отклонит")
            logger.info("=" * 60)
            return None

        # ============ ШАГ 1: Получаем URL ============
        logger.info(f"📤 ШАГ 1: POST {_api.base_url}/uploads?type=image")
        response = requests.post(
            f"{_api.base_url}/uploads",
            headers={"Authorization": _api.token},
            params={"type": "image"},
            timeout=30,
            verify=False
        )
        logger.info(f"📤 ШАГ 1: HTTP {response.status_code}")
        logger.info(f"📤 ШАГ 1: тело ответа: {response.text[:500]}")
        
        if response.status_code != 200:
            logger.error(f"❌ ШАГ 1 провален: {response.status_code}")
            logger.info("=" * 60)
            return None
        
        try:
            upload_data = response.json()
        except ValueError:
            logger.error(f"❌ ШАГ 1: невалидный JSON")
            logger.info("=" * 60)
            return None
        
        upload_url = upload_data.get('url')
        token_from_step1 = upload_data.get('token')
        
        logger.info(f"📤 upload_url: {upload_url}")
        logger.info(f"📤 token из шага 1: {'ЕСТЬ (' + token_from_step1[:30] + '...)' if token_from_step1 else 'НЕТ'}")
        
        if not upload_url:
            logger.error(f"❌ ШАГ 1: нет url в ответе: {upload_data}")
            logger.info("=" * 60)
            return None

        # ============ ШАГ 2: Загружаем файл ============
        logger.info(f"📤 ШАГ 2: POST {upload_url}")
        files = {'data': ('photo.jpg', image_bytes, 'image/jpeg')}
        upload_response = requests.post(
            upload_url,
            files=files,
            timeout=60,
            verify=False
        )
        logger.info(f"📤 ШАГ 2: HTTP {upload_response.status_code}")
        logger.info(f"📤 ШАГ 2: тело ответа: {upload_response.text[:500]}")
        
        if upload_response.status_code != 200:
            logger.error(f"❌ ШАГ 2 провален: {upload_response.status_code}")
            logger.info("=" * 60)
            return None

        # ============ ШАГ 3: Извлекаем токен ============
        token = token_from_step1  # Если пришёл на шаге 1 — используем
        
        if not token:
            logger.info("📤 Токен не пришёл на шаге 1, ищем в шаге 2...")
            try:
                upload_result = upload_response.json()
                logger.info(f"📤 JSON шага 2: {str(upload_result)[:500]}")
                
                # Структура photos
                if 'photos' in upload_result and isinstance(upload_result['photos'], dict):
                    for key, photo_data in upload_result['photos'].items():
                        if isinstance(photo_data, dict) and 'token' in photo_data:
                            token = photo_data['token']
                            logger.info(f"✅ Токен найден в photos[{key}]: {token[:40]}...")
                            break
                
                # Fallback: token на верхнем уровне
                if not token and 'token' in upload_result:
                    token = upload_result['token']
                    logger.info(f"✅ Токен найден на верхнем уровне: {token[:40]}...")
                    
            except ValueError as e:
                logger.error(f"❌ Ошибка парсинга JSON шага 2: {e}")
                logger.info("=" * 60)
                return None

        if not token:
            logger.error("❌ Токен НЕ НАЙДЕН ни в шаге 1, ни в шаге 2")
            logger.info("=" * 60)
            return None
        
        logger.info(f"✅ upload_image_to_max: токен получен ({token[:40]}...)")
        logger.info("=" * 60)
        return token

    except Exception as e:
        logger.error(f"❌ upload_image_to_max: исключение: {e}")
        import traceback
        traceback.print_exc()
        logger.info("=" * 60)
        return None


def publish_random_post(force: bool = False):
    """
    Публикует один случайный пост из pending_queue.
    Использует base64 фото из БД бота → загружает в MAX → публикует.
    """
    logger.info("=" * 60)
    logger.info(f"📤 publish_random_post: НАЧАЛО (force={force})")
    
    # Обновляем очередь
    refresh_listings()

    # Берём из pending_queue
    listing = _db.get_random_pending()
    if not listing:
        logger.warning("⚠️ pending_queue пуста — нет карточек для публикации")
        logger.info("=" * 60)
        return

    logger.info(f"📦 Выбрана карточка:")
    logger.info(f"   ID: {listing.get('id')}")
    logger.info(f"   external_id: {listing.get('external_id')}")
    logger.info(f"   title: {listing.get('title')}")
    logger.info(f"   url: {listing.get('url')}")
    logger.info(f"   price: {listing.get('price')}")

    # ============ ФОТО ИЗ BASE64 ============
    logger.info("-" * 60)
    logger.info("🖼️ ЭТАП: ЗАГРУЗКА ФОТО")
    logger.info("-" * 60)
    
    image_token = None
    image_base64 = listing.get("image_base64")

    if image_base64:
        logger.info(f"🖼️ base64 найден в БД: {len(image_base64)} символов")
        image_token = upload_image_to_max(image_base64)
        logger.info(f"🎫 Результат upload: image_token={'ЕСТЬ (' + image_token[:30] + '...)' if image_token else 'НЕТ'}")
    else:
        logger.warning("⚠️ В очереди нет base64 для этой карточки")
        logger.warning("   Возможные причины:")
        logger.warning("   1. Парсер не запускался с новым кодом")
        logger.warning("   2. В listings.db нет колонки image_base64")
        logger.warning("   3. Карточка добавлена до обновления парсера")

    logger.info(f"🧪 ИТОГ ФОТО: image_token={'ЕСТЬ' if image_token else 'НЕТ'}")
    logger.info("-" * 60)

    # ============ ГЕНЕРАЦИЯ ТЕКСТА ============
    logger.info("📝 ЭТАП: ГЕНЕРАЦИЯ ТЕКСТА")
    post_text = _description_gen.generate_post(listing)
    logger.info(f"📝 Текст поста ({len(post_text)} символов):")
    logger.info(f"---\n{post_text}\n---")

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
                logger.info("=" * 60)
                return
        except Exception as e:
            logger.error(f"❌ Ошибка чтения admin_id: {e}")
            logger.info("=" * 60)
            return
    else:
        logger.info(f"🔴 БОЕВОЙ РЕЖИМ: публикация в канал {target_chat}")

    # ============ ПУБЛИКАЦИЯ ============
    logger.info("-" * 60)
    logger.info("📤 ЭТАП: ОТПРАВКА В MAX")
    logger.info("-" * 60)
    
    if image_token:
        logger.info(f"📤 Отправка С ФОТО в {target_chat}")
        logger.info(f"📤 attachments: [{{type: image, payload: {{token: {image_token[:30]}...}}}}]")
        ok = _api.send_message_with_attachments(target_chat, post_text, [image_token])
    else:
        logger.info(f"📤 Отправка БЕЗ фото в {target_chat}")
        if test_mode:
            ok = _api.send_message(int(target_chat), post_text)
        else:
            ok = _api.send_message_to_chat(target_chat, post_text)

    logger.info(f"📤 Результат отправки: {'✅ OK' if ok else '❌ FAIL'}")

    # ============ ПОСТ-ОБРАБОТКА ============
    if ok:
        logger.info("-" * 60)
        logger.info("💾 ЭТАП: СОХРАНЕНИЕ В БД")
        logger.info("-" * 60)
        
        # Сначала добавляем в published
        try:
            _db.add_publication(
                listing_id=listing.get("listing_id") or listing.get("id") or 0,
                external_id=listing.get("external_id") or listing["url"],
                url=listing["url"],
                title=listing["title"],
                message_id="",
                chat_id=target_chat,
            )
            logger.info("✅ Запись в published добавлена")
        except Exception as e:
            logger.warning(f"⚠️ Ошибка add_publication: {e}")

        # Потом удаляем из очереди
        try:
            _db.remove_from_pending(listing["external_id"])
            logger.info(f"✅ Удалено из pending_queue: {listing['external_id']}")
        except Exception as e:
            logger.error(f"❌ Ошибка remove_from_pending: {e}")
        
        logger.info(f"✅ ОПУБЛИКОВАНО: {listing['title']}")
    else:
        logger.error(f"❌ НЕ УДАЛОСЬ ОПУБЛИКОВАТЬ: {listing['title']}")
        logger.error(f"   image_token: {image_token}")
        logger.error(f"   target_chat: {target_chat}")

    logger.info("=" * 60)
    logger.info("📤 publish_random_post: КОНЕЦ")
    logger.info("=" * 60)


def cleanup_old_posts():
    logger.info("=" * 60)
    logger.info("🗑️ cleanup_old_posts: НАЧАЛО")
    
    total = _db.count_published()
    logger.info(f"📊 Всего постов в канале (по БД): {total}")

    if total <= MAX_POSTS_IN_CHANNEL:
        logger.info(f"✅ Лимит не превышен ({total}/{MAX_POSTS_IN_CHANNEL})")
        logger.info("=" * 60)
        return

    excess = total - MAX_POSTS_IN_CHANNEL
    logger.info(f"🗑️ Нужно удалить: {excess} постов")

    old_posts = _db.get_oldest_published(limit=excess)
    logger.info(f"🗑️ Получено записей для удаления: {len(old_posts)}")

    for i, post in enumerate(old_posts):
        logger.info(f"🗑️ [{i+1}/{len(old_posts)}] Пост: {post.get('title')}")
        message_id = post.get("message_id")
        
        if not message_id:
            logger.warning(f"   ⚠️ Нет message_id — помечаю как deleted")
            _db.mark_deleted(post["id"])
            continue

        logger.info(f"   📤 DELETE /messages?message_id={message_id}")
        success = _api.delete_message(message_id)
        
        if success:
            logger.info(f"   ✅ Удалено")
            _db.mark_deleted(post["id"])
        else:
            logger.error(f"   ❌ Не удалось удалить")
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

    logger.info("🗑️ cleanup_old_posts: КОНЕЦ")
    logger.info("=" * 60)


def start_scheduler():
    logger.info("=" * 60)
    logger.info("⏰ start_scheduler: НАЧАЛО")
    
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
    logger.info("⏰ refresh_listings: каждый час в :00")

    scheduler.add_job(
        cleanup_old_posts,
        CronTrigger(hour=3, minute=0),
        id="cleanup_old_posts",
        replace_existing=True,
    )
    logger.info("⏰ cleanup_old_posts: ежедневно в 03:00 МСК")

    scheduler.start()
    logger.info("✅ Планировщик запущен")
    logger.info("=" * 60)
    return scheduler
