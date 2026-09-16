# config.py
# ============================================================
# Настройки проекта 2
# Все значения — из переменных окружения (панель Bothost).
# Никаких .env в репозитории.
# ============================================================

import os


def _int(name, default):
    try:
        return int(os.environ.get(name, default))
    except (ValueError, TypeError):
        return default


def _float_pair(name, default):
    raw = os.environ.get(name, default)
    try:
        a, b = raw.split(',')
        return (float(a.strip()), float(b.strip()))
    except Exception:
        a, b = default.split(',')
        return (float(a), float(b))


# === MAX ===
MAX_USER_TOKEN = os.environ.get('MAX_USER_TOKEN', '')
MAX_BOT_TOKEN = os.environ.get('MAX_BOT_TOKEN', '')
MAX_CHANNEL_ID = os.environ.get('MAX_CHANNEL_ID', '')
MAX_API_BASE = 'https://platform-api2.max.ru'

# === Flask ===
SECRET_KEY = os.environ.get('SECRET_KEY', 'dev_secret_key_change_me')
PORT = _int('PORT', 3001)
PUBLIC_URL = os.environ.get('PUBLIC_URL', 'https://project2.bothost.tech')

# === Админка ===
ADMIN_USER = os.environ.get('ADMIN_USER', 'admin')
ADMIN_PASS = os.environ.get('ADMIN_PASS', '')
ALLOWED_ADMIN_IDS = [
    int(x) for x in (os.environ.get('ADMIN_IDS') or '').split(',')
    if x.strip().isdigit()
]

# === Пути ===
DATA_DIR = os.environ.get('DATA_DIR', '/app/data')
MEDIA_DIR = os.environ.get('MEDIA_DIR', '/app/data/media')
DB_PATH = os.environ.get('DB_PATH', '/app/data/project2.db')
LOG_DIR = os.environ.get('LOG_DIR', '/app/logs')

# === Расписание публикаций ===
SCHEDULE_START = os.environ.get('SCHEDULE_START', '06:00')
SCHEDULE_END = os.environ.get('SCHEDULE_END', '20:00')
DAILY_LIMIT = _int('DAILY_LIMIT', 50)

# === Парсинг ===
PARSE_TIMES = [
    t.strip() for t in (os.environ.get('PARSE_TIMES', '06:00,18:00')).split(',')
    if t.strip()
]
PARSE_LIMIT_PER_GROUP = _int('PARSE_LIMIT_PER_GROUP', 30)

# === Human-like задержки ===
HUMAN_DELAY_HISTORY = _float_pair('HUMAN_DELAY_HISTORY', '2,4')   # между запросами истории
HUMAN_DELAY_PHOTO = _float_pair('HUMAN_DELAY_PHOTO', '1,2')       # между фото
HUMAN_DELAY_POST = _float_pair('HUMAN_DELAY_POST', '2,3')         # между постами
HUMAN_DELAY_GROUP = _float_pair('HUMAN_DELAY_GROUP', '10,20')     # между группами


# === Хелперы ===
def get_allowed_admin_ids():
    return ALLOWED_ADMIN_IDS


def get_source_groups(db):
    """Возвращает список активных групп-источников из БД."""
    return db.get_active_sources()


def get_schedule(db):
    """Возвращает расписание из БД (с фолбэком на переменные окружения)."""
    start = db.get_setting('schedule_start') or SCHEDULE_START
    end = db.get_setting('schedule_end') or SCHEDULE_END
    limit = db.get_setting('daily_limit') or str(DAILY_LIMIT)
    try:
        limit = int(limit)
    except ValueError:
        limit = DAILY_LIMIT
    return {'start': start, 'end': end, 'daily_limit': limit}
