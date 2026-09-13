# parser/parser.py v-2
"""
Парсер каталога ileasing.ru на Playwright.

Логика:
1. Случайно выбирает подкатегорию из ALLOWED_CATEGORIES.
2. Обходит страницы каталога (?PAGEN_1=N), собирает карточки.
3. Фильтрует: цена >= MIN_PRICE, href ещё нет в БД.
4. Для новых карточек: заходит в карточку, скачивает ВСЕ фото через браузер.
5. Сохраняет: listings.db + data/images/{external_id}/*.jpg
6. Цель — 20 новых карточек за запуск. Лимит — 5 категорий.
"""

import os
import re
import json
import random
import logging
import asyncio
import io
from typing import Optional
from urllib.parse import urljoin

from playwright.async_api import async_playwright, Page, TimeoutError as PWTimeoutError

logger = logging.getLogger(__name__)

BASE_URL = "https://www.ileasing.ru"
MIN_PRICE = 2_200_000
TARGET_NEW = 20
MAX_CATEGORIES = 5
IMAGES_DIR = "data/images"
MAX_IMAGES = 10
