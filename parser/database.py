# parser/database.py 3
"""
SQLite-обёртка для парсера.
Хранит только listings (спарсенные карточки) + base64 изображения.
"""

import sqlite3
import os
import logging

logger = logging.getLogger(__name__)


class ParserDB:
    def __init__(self, db_path: str = "data/listings.db"):
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
                    images_path TEXT,
                    image_base64 TEXT,
                    category TEXT,
                    parsed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    status TEXT DEFAULT 'pending'
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_status ON listings(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_url ON listings(url)")
            conn.commit()

            # ✅ МИГРАЦИЯ: добавляем недостающие колонки в существующую БД
            self._migrate(conn)

            logger.info(f"✅ БД парсера инициализирована: {self.db_path}")

    def _migrate(self, conn):
        """Добавляет недостающие колонки в существующую таблицу listings."""
        try:
            cols = [r["name"] for r in conn.execute("PRAGMA table_info(listings)").fetchall()]
            logger.info(f"📋 Текущие колонки listings: {cols}")

            if "image_base64" not in cols:
                logger.info("🔧 Добавляю колонку image_base64...")
                conn.execute("ALTER TABLE listings ADD COLUMN image_base64 TEXT")
                conn.commit()
                logger.info("✅ Колонка image_base64 добавлена")
            else:
                logger.info("✅ Колонка image_base64 уже присутствует")

            if "images_path" not in cols:
                logger.info("🔧 Добавляю колонку images_path...")
                conn.execute("ALTER TABLE listings ADD COLUMN images_path TEXT")
                conn.commit()
                logger.info("✅ Колонка images_path добавлена")
        except Exception as e:
            logger.error(f"❌ Ошибка миграции: {e}")

    def listing_exists(self, url: str) -> bool:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT 1 FROM listings WHERE url = ? LIMIT 1", (url,)
            ).fetchone()
            return row is not None
        finally:
            conn.close()

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
        images_path: str = None,
        image_base64: str = None,
        category: str = None,
    ) -> bool:
        try:
            conn = self._connect()
            try:
                conn.execute("""
                    INSERT INTO listings (
                        external_id, url, title, price, price_value, leasing,
                        engine, transmission, power, volume, drive, seats,
                        image, images_path, image_base64, category, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
                """, (
                    external_id, url, title, price, price_value, leasing,
                    engine, transmission, power, volume, drive, seats,
                    image, images_path, image_base64, category,
                ))
                conn.commit()
                return True
            finally:
                conn.close()
        except sqlite3.IntegrityError:
            return False

    def count_pending(self) -> int:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT COUNT(*) as c FROM listings WHERE status = 'pending'"
            ).fetchone()
            return row["c"] if row else 0
        finally:
            conn.close()

    def stats(self) -> dict:
        conn = self._connect()
        try:
            total = conn.execute("SELECT COUNT(*) as c FROM listings").fetchone()["c"]
            pending = conn.execute(
                "SELECT COUNT(*) as c FROM listings WHERE status = 'pending'"
            ).fetchone()["c"]
            published = conn.execute(
                "SELECT COUNT(*) as c FROM listings WHERE status = 'published'"
            ).fetchone()["c"]
            return {"total": total, "pending": pending, "published": published}
        finally:
            conn.close()
