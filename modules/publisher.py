# bot/modules/publisher.py
"""
Публикация постов в канал MAX.
Упрощённая версия — работает с карточками из listings.db.
"""

import logging
import time

logger = logging.getLogger(__name__)


class Publisher:
    def __init__(self, api, file_manager, db):
        self.api = api
        self.fm = file_manager
        self.db = db
        self.pending_messages = {}
        self._stopped = set()
        self._diagnostic_log = []

    def stop(self, user_id: int):
        """Останавливает публикацию для пользователя."""
        self._stopped.add(user_id)
        logger.info(f"⏹ Остановка для пользователя {user_id}")

    def is_stopped(self, user_id: int) -> bool:
        return user_id in self._stopped

    def publish_folder_with_tokens(
        self,
        user_id: int,
        folder_name: str,
        ad_text: str,
        metadata_text: str,
        image_tokens: list,
    ):
        """
        Публикует пост в канал.
        Возвращает (success, message).
        """
        try:
            chat_id = str(user_id)  # заглушка
            if not image_tokens:
                ok = self.api.send_message_to_chat(chat_id, ad_text)
            else:
                ok = self.api.send_message_with_attachments(
                    chat_id, ad_text, image_tokens
                )
            if ok:
                return True, "Опубликовано"
            return False, "Ошибка публикации"
        except Exception as e:
            logger.error(f"❌ Ошибка публикации: {e}")
            return False, str(e)

    def handle_message_created(self, chat_id, message_id, user_id=None):
        """Заглушка для совместимости."""
        logger.info(f"📨 handle_message_created: chat={chat_id}, msg={message_id}")

    def clear_diagnostic_log(self):
        self._diagnostic_log = []

    def get_diagnostic_log(self) -> list:
        return self._diagnostic_log
