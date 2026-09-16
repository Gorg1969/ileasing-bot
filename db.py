# db.py
# ============================================================
# SQLite для проекта 2
# ============================================================

import sqlite3
import logging
from datetime import datetime
from typing import Optional, List, Dict

logger = logging.getLogger(__name__)


class BotDB:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        conn = self._connect()
        c = conn.cursor()

        # === Источники (группы/каналы MAX) ===
        c.execute('''
            CREATE TABLE IF NOT EXISTS sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT UNIQUE NOT NULL,
                title TEXT,
                enabled INTEGER DEFAULT 1,
                last_parsed_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # === Посты ===
        c.execute('''
            CREATE TABLE IF NOT EXISTS parsed_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_chat_id TEXT NOT NULL,
                source_post_id TEXT NOT NULL,
                original_text TEXT,
                paraphrased_text TEXT,
                media_path TEXT,
                media_count INTEGER DEFAULT 0,
                status TEXT DEFAULT 'pending',
                error TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                published_at TIMESTAMP,
                UNIQUE(source_chat_id, source_post_id)
            )
        ''')

        # === Журнал публикаций (для отчёта «Опубликовано сегодня») ===
        c.execute('''
            CREATE TABLE IF NOT EXISTS publications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id INTEGER,
                channel_id TEXT,
                max_post_url TEXT,
                title TEXT,
                status TEXT DEFAULT 'success',
                error TEXT,
                published_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # === Настройки ===
        c.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        conn.commit()
        conn.close()
        logger.info(f'✅ БД инициализирована: {self.db_path}')

    # --------------------------------------------------------
    # ИСТОЧНИКИ
    # --------------------------------------------------------

    def add_source(self, chat_id: str, title: str = '') -> bool:
        try:
            conn = self._connect()
            c = conn.cursor()
            c.execute('''
                INSERT OR IGNORE INTO sources (chat_id, title, enabled)
                VALUES (?, ?, 1)
            ''', (chat_id, title))
            conn.commit()
            inserted = c.rowcount > 0
            conn.close()
            return inserted
        except Exception as e:
            logger.error(f'❌ add_source: {e}')
            return False

    def get_sources(self, only_enabled: bool = False) -> List[Dict]:
        conn = self._connect()
        c = conn.cursor()
        if only_enabled:
            c.execute('SELECT * FROM sources WHERE enabled = 1 ORDER BY id')
        else:
            c.execute('SELECT * FROM sources ORDER BY id')
        rows = [dict(r) for r in c.fetchall()]
        conn.close()
        return rows

    def get_active_sources(self) -> List[Dict]:
        return self.get_sources(only_enabled=True)

    def set_source_enabled(self, chat_id: str, enabled: bool):
        conn = self._connect()
        c = conn.cursor()
        c.execute('UPDATE sources SET enabled = ? WHERE chat_id = ?',
                  (1 if enabled else 0, chat_id))
        conn.commit()
        conn.close()

    def delete_source(self, chat_id: str):
        conn = self._connect()
        c = conn.cursor()
        c.execute('DELETE FROM sources WHERE chat_id = ?', (chat_id,))
        conn.commit()
        conn.close()

    def update_source_last_parsed(self, chat_id: str):
        conn = self._connect()
        c = conn.cursor()
        c.execute('''
            UPDATE sources SET last_parsed_at = CURRENT_TIMESTAMP
            WHERE chat_id = ?
        ''', (chat_id,))
        conn.commit()
        conn.close()

    # --------------------------------------------------------
    # ПОСТЫ
    # --------------------------------------------------------

    def is_post_parsed(self, chat_id: str, post_id: str) -> bool:
        conn = self._connect()
        c = conn.cursor()
        c.execute('''
            SELECT 1 FROM parsed_posts
            WHERE source_chat_id = ? AND source_post_id = ?
            LIMIT 1
        ''', (chat_id, post_id))
        found = c.fetchone() is not None
        conn.close()
        return found

    def add_parsed_post(self, data: Dict) -> bool:
        try:
            conn = self._connect()
            c = conn.cursor()
            c.execute('''
                INSERT OR IGNORE INTO parsed_posts
                (source_chat_id, source_post_id, original_text,
                 paraphrased_text, media_path, media_count, status)
                VALUES (?, ?, ?, ?, ?, ?, 'pending')
            ''', (
                data.get('source_chat_id'),
                data.get('source_post_id'),
                data.get('original_text'),
                data.get('paraphrased_text'),
                data.get('media_path'),
                data.get('media_count', 0),
            ))
            conn.commit()
            inserted = c.rowcount > 0
            conn.close()
            return inserted
        except Exception as e:
            logger.error(f'❌ add_parsed_post: {e}')
            return False

    def get_pending_posts(self, limit: int = 10) -> List[Dict]:
        conn = self._connect()
        c = conn.cursor()
        c.execute('''
            SELECT * FROM parsed_posts
            WHERE status = 'pending'
            ORDER BY created_at ASC
            LIMIT ?
        ''', (limit,))
        rows = [dict(r) for r in c.fetchall()]
        conn.close()
        return rows

    def get_post_by_id(self, post_id: int) -> Optional[Dict]:
        conn = self._connect()
        c = conn.cursor()
        c.execute('SELECT * FROM parsed_posts WHERE id = ?', (post_id,))
        row = c.fetchone()
        conn.close()
        return dict(row) if row else None

    def mark_post_paraphrased(self, post_id: int, paraphrased_text: str):
        conn = self._connect()
        c = conn.cursor()
        c.execute('''
            UPDATE parsed_posts
            SET paraphrased_text = ?, status = 'paraphrased'
            WHERE id = ?
        ''', (paraphrased_text, post_id))
        conn.commit()
        conn.close()

    def mark_post_published(self, post_id: int, post_url: str = None):
        conn = self._connect()
        c = conn.cursor()
        c.execute('''
            UPDATE parsed_posts
            SET status = 'published', published_at = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (post_id,))
        conn.commit()
        conn.close()

    def mark_post_failed(self, post_id: int, error: str):
        conn = self._connect()
        c = conn.cursor()
        c.execute('''
            UPDATE parsed_posts
            SET status = 'failed', error = ?
            WHERE id = ?
        ''', (error, post_id))
        conn.commit()
        conn.close()

    def count_by_status(self) -> Dict:
        conn = self._connect()
        c = conn.cursor()
        c.execute('SELECT status, COUNT(*) FROM parsed_posts GROUP BY status')
        result = dict(c.fetchall())
        conn.close()
        return result

    def count_today_publications(self) -> int:
        conn = self._connect()
        c = conn.cursor()
        c.execute('''
            SELECT COUNT(*) FROM publications
            WHERE DATE(published_at) = DATE('now', 'localtime')
              AND status = 'success'
        ''')
        n = c.fetchone()[0]
        conn.close()
        return n

    # --------------------------------------------------------
    # ПУБЛИКАЦИИ
    # --------------------------------------------------------

    def add_publication(self, data: Dict) -> int:
        conn = self._connect()
        c = conn.cursor()
        c.execute('''
            INSERT INTO publications
            (post_id, channel_id, max_post_url, title, status, error)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            data.get('post_id'),
            data.get('channel_id'),
            data.get('max_post_url'),
            data.get('title'),
            data.get('status', 'success'),
            data.get('error'),
        ))
        pub_id = c.lastrowid
        conn.commit()
        conn.close()
        return pub_id

    def get_publications_today_full(self) -> List[Dict]:
        import pytz
        moscow_tz = pytz.timezone('Europe/Moscow')

        conn = self._connect()
        c = conn.cursor()
        c.execute('''
            SELECT p.id, p.published_at, p.max_post_url, p.channel_id,
                   p.title, pp.source_chat_id, pp.source_post_id
            FROM publications p
            LEFT JOIN parsed_posts pp ON pp.id = p.post_id
            WHERE DATE(p.published_at) = DATE('now', 'localtime')
            ORDER BY p.published_at ASC
        ''')
        rows = c.fetchall()
        conn.close()

        result = []
        for row in rows:
            pub_dt = row[1]
            if isinstance(pub_dt, str):
                try:
                    pub_dt = datetime.fromisoformat(pub_dt)
                except Exception:
                    pub_dt = datetime.now()
            if pub_dt is None:
                pub_dt = datetime.now()
            if hasattr(pub_dt, 'tzinfo') and pub_dt.tzinfo is None:
                pub_dt = moscow_tz.localize(pub_dt)

            result.append({
                'id': row[0],
                'date': pub_dt.strftime('%d.%m.%Y'),
                'time': pub_dt.strftime('%H:%M'),
                'max_post_url': row[2] or '',
                'channel_id': row[3] or '',
                'title': row[4] or '',
                'source_chat_id': row[5] or '',
                'source_post_id': row[6] or '',
            })
        return result

    # --------------------------------------------------------
    # НАСТРОЙКИ
    # --------------------------------------------------------

    def set_setting(self, key: str, value: str):
        conn = self._connect()
        c = conn.cursor()
        c.execute('''
            INSERT INTO settings (key, value, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = CURRENT_TIMESTAMP
        ''', (key, value))
        conn.commit()
        conn.close()

    def get_setting(self, key: str, default: str = None) -> Optional[str]:
        conn = self._connect()
        c = conn.cursor()
        c.execute('SELECT value FROM settings WHERE key = ?', (key,))
        row = c.fetchone()
        conn.close()
        return row[0] if row else default

    def get_all_settings(self) -> Dict[str, str]:
        conn = self._connect()
        c = conn.cursor()
        c.execute('SELECT key, value FROM settings')
        result = {row[0]: row[1] for row in c.fetchall()}
        conn.close()
        return result
