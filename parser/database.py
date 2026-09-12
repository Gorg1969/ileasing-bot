# parser/database.py
"""
SQLite-обёртка для парсера.
Хранит только listings (спарсенные карточки).
Таблица published живёт в bot/modules/database.py.
"""

import sqlite3
import os
import json
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class ParserDB:
    def __init__(self, db_path: str = "data/listings.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_schema()

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self):
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS listings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    external_id TEXT UNIQUE,
                    url TEXT UNIQUE NOT NULL,
                    title TEXT,
                    price TEXT,
                    price_value INTEGER,
                    leasing TEXT,
                    engine TEXT,
                    transmission TEXT,
                    power TEXT,
                    volume TEXT,
                    drive TEXT,
                    seats TEXT,
                    image TEXT,
                    category TEXT,
                    parsed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    status TEXT DEFAULT 'pending'
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_status ON listings(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_url ON listings(url)")
            conn.commit()
            logger.info(f"✅ БД парсера инициализирована: {self.db_path}")

    def listing_exists(self, url: str) -> bool:
        """Проверяет, есть ли карточка с таким URL."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM listings WHERE url = ? LIMIT 1", (url,)
            ).fetchone()
            return row is not None

    def add_listing(
        self,
        external_id: str,
        url: str,
        title: str,
        price: str,
        price_value: int,
        leasing: str,
        engine: str = None,
        transmission: str = None,
        power: str = None,
        volume: str = None,
        drive: str = None,
        seats: str = None,
        image: str = None,
        category: str = None,
    ) -> bool:
        """Добавляет новую карточку. Возвращает True, если добавлена."""
        try:
            with self._connect() as conn:
                conn.execute("""
                    INSERT INTO listings (
                        external_id, url, title, price, price_value, leasing,
                        engine, transmission, power, volume, drive, seats,
                        image, category, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
                """, (
                    external_id, url, title, price, price_value, leasing,
                    engine, transmission, power, volume, drive, seats,
                    image, category,
                ))
                conn.commit()
                return True
        except sqlite3.IntegrityError:
            # Уже есть — не ошибка
            return False

    def count_pending(self) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as c FROM listings WHERE status = 'pending'"
            ).fetchone()
            return row["c"] if row else 0

    def get_pending(self, limit: int = 20) -> list:
        """Возвращает список pending-карточек (для отладки)."""
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT * FROM listings
                WHERE status = 'pending'
                ORDER BY parsed_at DESC
                LIMIT ?
            """, (limit,)).fetchall()
            return [dict(r) for r in rows]

    def stats(self) -> dict:
        with self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) as c FROM listings").fetchone()["c"]
            pending = conn.execute(
                "SELECT COUNT(*) as c FROM listings WHERE status = 'pending'"
            ).fetchone()["c"]
            return {"total": total, "pending": pending}
