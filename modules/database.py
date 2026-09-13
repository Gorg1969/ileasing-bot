# bot/modules/database.py
"""
SQLite-обёртка для бота-публикатора.
Хранит:
- published: что уже опубликовано (message_id для удаления)
- consultations: заявки с формы
- pending_queue: очередь на публикацию с base64 фото
"""

import sqlite3
import os
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class Database:
    def __init__(self, db_path: str = "data/published.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_schema()

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_schema(self):
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS published (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    listing_id INTEGER,
                    external_id TEXT UNIQUE,
                    url TEXT,
                    title TEXT,
                    message_id TEXT,
                    chat_id TEXT,
                    published_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    deleted BOOLEAN DEFAULT 0
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS consultations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fio TEXT NOT NULL,
                    phone TEXT NOT NULL,
                    inn TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    sent BOOLEAN DEFAULT 0
                )
            """)
            # ✅ Новая таблица: очередь публикаций с base64
            conn.execute("""
                CREATE TABLE IF NOT EXISTS pending_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    listing_id INTEGER,
                    external_id TEXT UNIQUE,
                    url TEXT UNIQUE,
                    title TEXT,
                    price TEXT,
                    leasing TEXT,
                    engine TEXT,
                    transmission TEXT,
                    power TEXT,
                    volume TEXT,
                    drive TEXT,
                    seats TEXT,
                    image_base64 TEXT,
                    category TEXT,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_published_at ON published(published_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_deleted ON published(deleted)")
            conn.commit()
            logger.info(f"✅ БД бота инициализирована: {self.db_path}")

    # ========== ОЧЕРЕДЬ ПУБЛИКАЦИЙ ==========

    def sync_pending_from_listings(self, listings_path: str) -> int:
        """
        Копирует pending-карточки из listings.db в pending_queue.
        Возвращает количество добавленных.
        """
        if not os.path.exists(listings_path):
            logger.warning(f"⚠️ {listings_path} не найден")
            return 0

        src = sqlite3.connect(listings_path, timeout=10)
        src.row_factory = sqlite3.Row
        added = 0
        try:
            # Проверяем, есть ли колонка image_base64
            cols = [r["name"] for r in src.execute("PRAGMA table_info(listings)").fetchall()]
            has_base64 = "image_base64" in cols
            logger.info(f"📋 Колонки listings: {cols}")
            logger.info(f"📋 image_base64 присутствует: {has_base64}")

            if has_base64:
                rows = src.execute("""
                    SELECT * FROM listings
                    WHERE status = 'pending' AND image_base64 IS NOT NULL
                """).fetchall()
            else:
                rows = src.execute("""
                    SELECT * FROM listings
                    WHERE status = 'pending'
                """).fetchall()

            logger.info(f"📋 Найдено pending-записей: {len(rows)}")

            with self._connect() as conn:
                for row in rows:
                    r = dict(row)
                    try:
                        conn.execute("""
                            INSERT OR IGNORE INTO pending_queue (
                                listing_id, external_id, url, title, price, leasing,
                                engine, transmission, power, volume, drive, seats,
                                image_base64, category
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            r.get("id"),
                            r.get("external_id"),
                            r.get("url"),
                            r.get("title"),
                            r.get("price"),
                            r.get("leasing"),
                            r.get("engine"),
                            r.get("transmission"),
                            r.get("power"),
                            r.get("volume"),
                            r.get("drive"),
                            r.get("seats"),
                            r.get("image_base64"),
                            r.get("category"),
                        ))
                        if conn.total_changes > added:
                            added += 1
                    except Exception as e:
                        logger.warning(f"⚠️ Ошибка добавления {r.get('url')}: {e}")
                conn.commit()

            logger.info(f"✅ Синхронизировано в pending_queue: {added} новых")
            return added
        finally:
            src.close()

    def get_random_pending(self) -> dict:
        """Возвращает случайную карточку из очереди."""
        with self._connect() as conn:
            row = conn.execute("""
                SELECT * FROM pending_queue
                ORDER BY RANDOM()
                LIMIT 1
            """).fetchone()
            return dict(row) if row else None

    def remove_from_pending(self, external_id: str):
        """Удаляет карточку из очереди после публикации."""
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM pending_queue WHERE external_id = ?",
                (external_id,)
            )
            conn.commit()

    def count_pending_queue(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) as c FROM pending_queue").fetchone()
            return row["c"] if row else 0

    # ========== ПУБЛИКАЦИИ ==========

    def is_published(self, external_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM published WHERE external_id = ? LIMIT 1",
                (external_id,)
            ).fetchone()
            return row is not None

    def add_publication(
        self,
        listing_id: int,
        external_id: str,
        url: str,
        title: str,
        message_id: str,
        chat_id: str,
    ) -> bool:
        try:
            with self._connect() as conn:
                conn.execute("""
                    INSERT INTO published
                        (listing_id, external_id, url, title, message_id, chat_id)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (listing_id, external_id, url, title, message_id, chat_id))
                conn.commit()
                return True
        except sqlite3.IntegrityError:
            logger.warning(f"⚠️ Публикация {external_id} уже существует")
            return False

    def count_published(self) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as c FROM published WHERE deleted = 0"
            ).fetchone()
            return row["c"] if row else 0

    def get_oldest_published(self, limit: int) -> list:
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT * FROM published
                WHERE deleted = 0
                ORDER BY published_at ASC
                LIMIT ?
            """, (limit,)).fetchall()
            return [dict(r) for r in rows]

    def mark_deleted(self, publication_id: int) -> bool:
        with self._connect() as conn:
            conn.execute(
                "UPDATE published SET deleted = 1 WHERE id = ?",
                (publication_id,)
            )
            conn.commit()
            return True

    def stats(self) -> dict:
        with self._connect() as conn:
            total = conn.execute(
                "SELECT COUNT(*) as c FROM published WHERE deleted = 0"
            ).fetchone()["c"]
            return {"published_total": total}

    # ========== ЗАЯВКИ ==========

    def add_consultation(self, fio: str, phone: str, inn: str) -> int:
        with self._connect() as conn:
            cursor = conn.execute("""
                INSERT INTO consultations (fio, phone, inn)
                VALUES (?, ?, ?)
            """, (fio, phone, inn))
            conn.commit()
            return cursor.lastrowid

    def mark_consultation_sent(self, consultation_id: int) -> bool:
        with self._connect() as conn:
            conn.execute(
                "UPDATE consultations SET sent = 1 WHERE id = ?",
                (consultation_id,)
            )
            conn.commit()
            return True

    def get_consultations(self, limit: int = 100) -> list:
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT * FROM consultations
                ORDER BY created_at DESC
                LIMIT ?
            """, (limit,)).fetchall()
            return [dict(r) for r in rows]

    # ========== СЕРВИСНЫЕ ==========

    def fix_publication_times(self):
        pass

    def clear_user_data(self, user_id: int):
        pass

    def get_publications_with_status(self, user_id: int, status: str) -> list:
        return []

    def get_publications(self, user_id: int) -> list:
        return []

    def get_ad_metadata(self, user_id: int, folder_name: str) -> dict:
        return {}

    def update_publication_status(self, user_id: int, folder_name: str, status: str):
        pass
