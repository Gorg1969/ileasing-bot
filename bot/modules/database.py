# bot/modules/database.py
"""
SQLite-обёртка для бота-публикатора.
Хранит:
- published: что уже опубликовано (message_id для удаления)
- consultations: заявки с формы
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
            conn.execute("CREATE INDEX IF NOT EXISTS idx_published_at ON published(published_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_deleted ON published(deleted)")
            conn.commit()
            logger.info(f"✅ БД бота инициализирована: {self.db_path}")

    # ========== ПУБЛИКАЦИИ ==========

    def is_published(self, external_id: str) -> bool:
        """Проверяет, публиковали ли уже эту карточку."""
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
        """Записывает факт публикации."""
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
        """Сколько всего опубликовано (не удалено)."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as c FROM published WHERE deleted = 0"
            ).fetchone()
            return row["c"] if row else 0

    def get_oldest_published(self, limit: int) -> list:
        """Возвращает самые старые публикации для удаления."""
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT * FROM published
                WHERE deleted = 0
                ORDER BY published_at ASC
                LIMIT ?
            """, (limit,)).fetchall()
            return [dict(r) for r in rows]

    def mark_deleted(self, publication_id: int) -> bool:
        """Помечает публикацию как удалённую."""
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
        """Сохраняет заявку. Возвращает ID."""
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
        """Заглушка — оставлена для совместимости с вашим app.py."""
        pass

    def clear_user_data(self, user_id: int):
        """Заглушка — оставлена для совместимости с вашим app.py."""
        pass

    def get_publications_with_status(self, user_id: int, status: str) -> list:
        """Заглушка — оставлена для совместимости с вашим app.py."""
        return []

    def get_publications(self, user_id: int) -> list:
        """Заглушка — оставлена для совместимости с вашим app.py."""
        return []

    def get_ad_metadata(self, user_id: int, folder_name: str) -> dict:
        """Заглушка — оставлена для совместимости с вашим app.py."""
        return {}

    def update_publication_status(self, user_id: int, folder_name: str, status: str):
        """Заглушка — оставлена для совместимости с вашим app.py."""
        pass
