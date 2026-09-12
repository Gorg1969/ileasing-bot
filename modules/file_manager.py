# bot/modules/file_manager.py
"""
Управление файлами пользователя.
Оставлен для совместимости с существующим app.py.
"""

import os
import logging

logger = logging.getLogger(__name__)


class FileManager:
    def __init__(self, data_dir: str = "data"):
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)

    def get_user_folder(self, user_id: int) -> str:
        """Возвращает папку пользователя, создаёт если нет."""
        folder = os.path.join(self.data_dir, "users", str(user_id))
        os.makedirs(folder, exist_ok=True)
        return folder

    def cleanup_user_folder(self, user_id: int) -> bool:
        """Удаляет папку пользователя."""
        import shutil
        folder = self.get_user_folder(user_id)
        if os.path.exists(folder):
            shutil.rmtree(folder)
            return True
        return False

    def save_file(self, user_id: int, filename: str, content: bytes) -> str:
        """Сохраняет файл в папку пользователя."""
        folder = self.get_user_folder(user_id)
        path = os.path.join(folder, filename)
        with open(path, "wb") as f:
            f.write(content)
        return path
