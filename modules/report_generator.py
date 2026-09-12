# bot/modules/report_generator.py
"""
Генератор отчётов.
Оставлен для совместимости с существующим app.py.
"""

import os
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class ReportGenerator:
    def __init__(self, file_manager, db):
        self.fm = file_manager
        self.db = db

    def generate_report(self, user_id: int) -> str:
        """
        Генерирует простой отчёт для пользователя.
        Возвращает путь к файлу или None.
        """
        try:
            folder = self.fm.get_user_folder(user_id)
            filename = f"Отчет_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
            path = os.path.join(folder, filename)

            with open(path, "w", encoding="utf-8") as f:
                f.write(f"Отчёт от {datetime.now().strftime('%d.%m.%Y %H:%M')}\n")
                f.write("=" * 40 + "\n\n")
                f.write("Публикации:\n")

                stats = self.db.stats()
                f.write(f"  Всего опубликовано: {stats.get('published_total', 0)}\n")

            logger.info(f"✅ Отчёт создан: {path}")
            return path
        except Exception as e:
            logger.error(f"❌ Ошибка генерации отчёта: {e}")
            return None

    def mark_report_downloaded(self, user_id: int):
        """Заглушка — отчёт считается скачанным автоматически."""
        pass
