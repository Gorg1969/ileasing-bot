# app.py
"""
Главный файл бота-публикатора.
Flask + веб-интерфейс + форма консультации + планировщик.

Особенности:
- ADMIN_USER_ID определяется автоматически: первый, кто напишет /start, становится админом.
- ID сохраняется в data/admin_id.txt
- Доступ к боту имеют только админ. Остальные видят "Доступ ограничен".
- Сбросить админа: /admin_reset (только текущий админ)
"""

from flask import Flask, request, jsonify, render_template_string
import requests
import logging
import os
import urllib3

from modules.database import Database
from modules.file_manager import FileManager
from modules.publisher import Publisher
from modules.report_generator import ReportGenerator
from modules import description_gen
from modules import scheduler as sched_module

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev_secret_key")
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = (
    os.environ.get("BOT_TOKEN")
    or os.environ.get("MAX_BOT_TOKEN")
    or os.environ.get("MAX_TOKEN")
    or os.environ.get("TOKEN")
    or os.environ.get("API_TOKEN")
)
BASE_URL = "https://platform-api2.max.ru"
DATA_DIR = os.environ.get("DATA_DIR", "/app/data")
CHANNEL_ID = os.environ.get("CHANNEL_ID", "-1234567890")
ADMIN_ID_FILE = os.path.join(DATA_DIR, "admin_id.txt")

if not TOKEN:
    logger.error("❌ ТОКЕН НЕ НАЙДЕН!")


# ========== ОПРЕДЕЛЕНИЕ АДМИНА ==========

def get_admin_id():
    """Читает ID админа из файла. Возвращает None, если не задан."""
    try:
        if os.path.exists(ADMIN_ID_FILE):
            with open(ADMIN_ID_FILE, "r") as f:
                value = f.read().strip()
                return int(value) if value else None
    except Exception as e:
        logger.error(f"❌ Ошибка чтения admin_id: {e}")
    return None


def save_admin_id(user_id: int):
    """Сохраняет ID админа в файл."""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(ADMIN_ID_FILE, "w") as f:
            f.write(str(user_id))
        logger.info(f"✅ Admin ID сохранён: {user_id}")
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка сохранения admin_id: {e}")
        return False


def is_admin(user_id: int) -> bool:
    """Проверяет, является ли пользователь админом."""
    admin_id = get_admin_id()
    return admin_id is not None and admin_id == user_id


# ========== API CLIENT ==========

class APIClient:
    def __init__(self):
        self.token = TOKEN
        self.base_url = BASE_URL

    def send_message(self, user_id, text, attachments=None):
        if not self.token:
            return False
        try:
            payload = {"text": text, "format": "markdown"}
            if attachments:
                payload["attachments"] = attachments
            response = requests.post(
                f"{self.base_url}/messages",
                headers={"Authorization": self.token, "Content-Type": "application/json"},
                params={"user_id": user_id},
                json=payload,
                timeout=30,
                verify=False
            )
            return response.status_code == 200
        except Exception as e:
            logger.error(f"❌ Ошибка отправки: {e}")
            return False

    def send_message_to_chat(self, chat_id, text):
        if not self.token:
            return False
        try:
            payload = {"chat_id": chat_id, "text": text, "format": "markdown"}
            response = requests.post(
                f"{self.base_url}/messages",
                headers={"Authorization": self.token, "Content-Type": "application/json"},
                json=payload,
                timeout=30,
                verify=False
            )
            if response.status_code == 200:
                return True
            logger.error(f"❌ Ошибка: {response.status_code} - {response.text}")
            return False
        except Exception as e:
            logger.error(f"❌ Ошибка: {e}")
            return False

    def send_message_with_attachments(self, chat_id, text, tokens):
        if not self.token:
            return False
        try:
            attachments = [{"type": "image", "payload": {"token": t}} for t in tokens[:10]]
            payload = {
                "chat_id": chat_id,
                "text": text,
                "format": "markdown",
                "attachments": attachments
            }
            response = requests.post(
                f"{self.base_url}/messages",
                headers={"Authorization": self.token, "Content-Type": "application/json"},
                json=payload,
                timeout=60,
                verify=False
            )
            if response.status_code == 200:
                return True
            logger.error(f"❌ Ошибка: {response.status_code} - {response.text}")
            return False
        except Exception as e:
            logger.error(f"❌ Ошибка: {e}")
            return False

    def upload_file(self, image_bytes, filename='image.jpg'):
        if not self.token:
            return None
        try:
            response = requests.post(
                f"{self.base_url}/uploads",
                headers={"Authorization": self.token},
                params={"type": "image"},
                timeout=30,
                verify=False
            )
            if response.status_code != 200:
                return None
            upload_data = response.json()
            upload_url = upload_data.get('url')
            if not upload_url:
                return None

            files = {'data': (filename, image_bytes, 'image/jpeg')}
            upload_response = requests.post(upload_url, files=files, timeout=60, verify=False)
            if upload_response.status_code != 200:
                return None

            result = upload_response.json()
            if 'photos' in result and isinstance(result['photos'], dict):
                for photo_data in result['photos'].values():
                    if isinstance(photo_data, dict) and 'token' in photo_data:
                        return photo_data['token']
            if 'token' in result:
                return result['token']
            return None
        except Exception as e:
            logger.error(f"❌ Ошибка загрузки файла: {e}")
            return None

    def delete_message(self, message_id):
        """Удаляет сообщение из канала."""
        if not self.token or not message_id:
            return False
        try:
            response = requests.delete(
                f"{self.base_url}/messages",
                headers={"Authorization": self.token},
                params={"message_id": message_id},
                timeout=30,
                verify=False
            )
            if response.status_code == 200:
                logger.info(f"🗑️ Удалено сообщение {message_id}")
                return True
            logger.error(f"❌ Ошибка удаления {message_id}: {response.status_code} - {response.text}")
            return False
        except Exception as e:
            logger.error(f"❌ Ошибка удаления: {e}")
            return False


# ========== ИНИЦИАЛИЗАЦИЯ ==========

api = APIClient()
db = Database(f"{DATA_DIR}/published.db")
fm = FileManager(DATA_DIR)
publisher = Publisher(api, fm, db)
report_gen = ReportGenerator(fm, db)

sched_module.init_scheduler(api, db, publisher, description_gen, CHANNEL_ID)
scheduler = sched_module.start_scheduler()
sched_module.refresh_listings()


# ========== HTML ФОРМЫ КОНСУЛЬТАЦИИ ==========

CONSULTATION_PAGE = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Заявка на консультацию</title>
    <style>
        body { font-family: -apple-system, Arial, sans-serif; background: #f0f2f5; margin: 0; padding: 20px; }
        .container { max-width: 500px; margin: 50px auto; background: white; padding: 40px; border-radius: 12px; box-shadow: 0 4px 20px rgba(0,0,0,0.08); }
        h1 { color: #1a1a1a; margin-top: 0; font-size: 24px; }
        .subtitle { color: #666; margin-bottom: 30px; font-size: 14px; }
        .field { margin-bottom: 20px; }
        label { display: block; margin-bottom: 8px; color: #333; font-weight: 500; font-size: 14px; }
        input { width: 100%; padding: 12px 15px; border: 2px solid #e0e0e0; border-radius: 8px; font-size: 16px; box-sizing: border-box; transition: border-color 0.2s; }
        input:focus { outline: none; border-color: #007bff; }
        .btn { width: 100%; padding: 14px; background: #007bff; color: white; border: none; border-radius: 8px; font-size: 16px; font-weight: bold; cursor: pointer; transition: background 0.2s; }
        .btn:hover { background: #0056b3; }
        .btn:disabled { background: #ccc; cursor: not-allowed; }
        .message { margin-top: 20px; padding: 15px; border-radius: 8px; display: none; font-size: 15px; }
        .message.success { background: #d4edda; color: #155724; display: block; border-left: 4px solid #28a745; }
        .message.error { background: #f8d7da; color: #721c24; display: block; border-left: 4px solid #dc3545; }
        .note { margin-top: 20px; padding: 15px; background: #e7f5ff; border-left: 4px solid #007bff; border-radius: 8px; font-size: 13px; color: #004085; line-height: 1.5; }
    </style>
</head>
<body>
    <div class="container">
        <h1>Запись на консультацию</h1>
        <p class="subtitle">Заполните форму, и наш эксперт свяжется с вами</p>

        <form id="consultForm">
            <div class="field">
                <label for="fio">ФИО *</label>
                <input type="text" id="fio" name="fio" required placeholder="Иванов Иван Иванович">
            </div>
            <div class="field">
                <label for="phone">Номер телефона *</label>
                <input type="tel" id="phone" name="phone" required placeholder="+7 (999) 123-45-67">
            </div>
            <div class="field">
                <label for="inn">ИНН *</label>
                <input type="text" id="inn" name="inn" required placeholder="123456789012" pattern="[0-9]{10,12}">
            </div>
            <button type="submit" class="btn" id="submitBtn">ОТПРАВИТЬ</button>
        </form>

        <div id="message" class="message"></div>

        <div class="note">
            После отправки с вами свяжется эксперт по лизингу.
        </div>
    </div>

    <script>
        const form = document.getElementById('consultForm');
        const messageDiv = document.getElementById('message');
        const submitBtn = document.getElementById('submitBtn');

        form.addEventListener('submit', async (e) => {
            e.preventDefault();
            submitBtn.disabled = true;
            submitBtn.textContent = 'Отправка...';
            messageDiv.style.display = 'none';

            const data = {
                fio: document.getElementById('fio').value.trim(),
                phone: document.getElementById('phone').value.trim(),
                inn: document.getElementById('inn').value.trim()
            };

            try {
                const response = await fetch('/submit_consultation', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(data)
                });

                const result = await response.json();

                if (result.success) {
                    messageDiv.className = 'message success';
                    messageDiv.textContent = '✅ Спасибо, ваша заявка отправлена, ожидайте звонка специалиста!';
                    form.reset();
                } else {
                    messageDiv.className = 'message error';
                    messageDiv.textContent = '❌ ' + (result.message || 'Ошибка отправки');
                }
            } catch (error) {
                messageDiv.className = 'message error';
                messageDiv.textContent = '❌ Ошибка сети: ' + error.message;
            } finally {
                submitBtn.disabled = false;
                submitBtn.textContent = 'ОТПРАВИТЬ';
            }
        });
    </script>
</body>
</html>
"""


# ========== МАРШРУТЫ ==========

@app.route('/')
def index():
    admin_id = get_admin_id()
    return jsonify({
        "status": "running",
        "admin_configured": admin_id is not None,
    })


@app.route('/health')
def health():
    return {"status": "ok", "token_set": bool(TOKEN)}


@app.route('/consultation', methods=['GET'])
def consultation_page():
    return render_template_string(CONSULTATION_PAGE)


@app.route('/submit_consultation', methods=['POST'])
def submit_consultation():
    """Принимает заявку с формы, отправляет админу в личку MAX."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'message': 'Нет данных'}), 400

        fio = (data.get('fio') or '').strip()
        phone = (data.get('phone') or '').strip()
        inn = (data.get('inn') or '').strip()

        if not fio or not phone or not inn:
            return jsonify({'success': False, 'message': 'Заполните все поля'}), 400

        consultation_id = db.add_consultation(fio, phone, inn)
        admin_id = get_admin_id()

        if admin_id is None:
            logger.warning(f"⚠️ Admin ID не задан. Заявка #{consultation_id} сохранена.")
            return jsonify({'success': True, 'message': 'Заявка сохранена.'})

        text = (
            f"🔔 **Новая заявка на консультацию!**\n\n"
            f"👤 ФИО: {fio}\n"
            f"📞 Телефон: {phone}\n"
            f"🏢 ИНН: {inn}\n\n"
            f"🕐 Заявка #{consultation_id}"
        )
        sent = api.send_message(admin_id, text)

        if sent:
            db.mark_consultation_sent(consultation_id)
            logger.info(f"✅ Заявка #{consultation_id} отправлена админу {admin_id}")
        else:
            logger.error(f"❌ Не удалось отправить заявку #{consultation_id}")

        return jsonify({
            'success': True,
            'message': 'Заявка отправлена',
            'consultation_id': consultation_id
        })

    except Exception as e:
        logger.error(f"❌ Ошибка приёма заявки: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/webhook', methods=['POST'])
def webhook():
    """Обрабатывает события от MAX."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"ok": True}), 200

        update_type = data.get('update_type')

        if update_type == 'message_created':
            message = data.get('message', {})
            recipient = message.get('recipient', {})
            sender = message.get('sender', {})
            body = message.get('body', {})

            chat_id = recipient.get('chat_id')
            user_id = sender.get('user_id')
            text = body.get('text', '').strip() if body.get('text') else ''
            message_id = body.get('mid')

            logger.info(f"📨 user_id={user_id}, text={text}")

            # ============ ЗАЩИТА: игнорируем всех, кроме админа ============
            current_admin = get_admin_id()

            # Если админ ещё не задан — первый, кто написал /start, становится им
            if current_admin is None and text == '/start' and user_id:
                save_admin_id(user_id)
                api.send_message(
                    user_id,
                    "✅ **Вы зарегистрированы как администратор!**\n\n"
                    "Теперь сюда будут приходить заявки с формы консультации.\n\n"
                    "Команды:\n"
                    "/start — это меню\n"
                    "/admin_reset — сбросить админа\n"
                    "/status — статус бота"
                )
                return jsonify({"ok": True}), 200

            # Все остальные — игнорируются
            if not is_admin(user_id):
                logger.info(f"⛔ Игнорирую не-админа {user_id}")
                return jsonify({"ok": True}), 200

            # ============ ТОЛЬКО ДЛЯ АДМИНА ============

            if text == '/start':
                api.send_message(
                    user_id,
                    "🏠 **Главное меню**\n\n"
                    f"📝 Записаться на консультацию:\n"
                    f"https://{request.host}/consultation"
                )
                return jsonify({"ok": True}), 200

            if text == '/admin_reset':
                if os.path.exists(ADMIN_ID_FILE):
                    os.remove(ADMIN_ID_FILE)
                api.send_message(
                    user_id,
                    "🗑️ **Admin ID сброшен.**\n\n"
                    "Следующий, кто напишет /start, станет админом."
                )
                return jsonify({"ok": True}), 200

            if text == '/status':
                stats = db.stats()
                api.send_message(
                    user_id,
                    f"📊 **Статус бота**\n\n"
                    f"📦 Опубликовано: {stats.get('published_total', 0)}\n"
                    f"📡 Канал: `{CHANNEL_ID}`"
                )
                return jsonify({"ok": True}), 200

            # Неизвестная команда — молчим
            return jsonify({"ok": True}), 200

        return jsonify({"ok": True}), 200

    except Exception as e:
        logger.error(f"❌ Ошибка в вебхуке: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"ok": False}), 500


@app.route('/setup_webhook')
def setup_webhook():
    """Настраивает вебхук MAX."""
    token = request.args.get('token') or TOKEN
    if not token:
        return "❌ Токен не найден", 400

    webhook_url = f"{request.host_url}webhook".replace("http://", "https://")
    headers = {"Authorization": token, "Content-Type": "application/json"}

    try:
        payload = {
            "url": webhook_url,
            "update_types": ["message_created", "bot_started", "bot_stopped"]
        }
        r = requests.post(
            "https://platform-api2.max.ru/subscriptions",
            headers=headers,
            json=payload,
            timeout=30,
            verify=False
        )
        if r.status_code == 200:
            return f"✅ Вебхук настроен: {webhook_url}"
        return f"❌ Ошибка: {r.status_code} - {r.text}"
    except Exception as e:
        return f"❌ Ошибка: {e}"


@app.route('/manual_publish', methods=['POST'])
def manual_publish():
    """Ручной запуск публикации (для отладки)."""
    try:
        sched_module.publish_random_post()
        return jsonify({'success': True, 'message': 'Публикация запущена'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/manual_cleanup', methods=['POST'])
def manual_cleanup():
    """Ручной запуск очистки старых постов."""
    try:
        sched_module.cleanup_old_posts()
        return jsonify({'success': True, 'message': 'Очистка запущена'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/refresh_listings', methods=['POST'])
def refresh_listings_route():
    """Ручное обновление listings.db."""
    try:
        ok = sched_module.refresh_listings()
        return jsonify({'success': ok})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    if TOKEN:
        logger.info(f"✅ Токен найден (первые 10): {TOKEN[:10]}...")

    admin_id = get_admin_id()
    if admin_id:
        logger.info(f"✅ Admin ID: {admin_id}")
    else:
        logger.info("ℹ️ Admin ID не задан. Напишите боту /start, чтобы зарегистрироваться как админ.")

    app.run(host='0.0.0.0', port=port, threaded=True)
