# app.py v-2
from flask import Flask, request, jsonify, render_template_string, send_file
import requests
import logging
import os
import shutil
import urllib3
import json
import threading
import time
import base64
from werkzeug.exceptions import ClientDisconnected
from modules.database import Database
from modules.file_manager import FileManager
from modules.publisher import Publisher
from modules.report_generator import ReportGenerator
from modules import description_gen
from modules import scheduler as sched_module

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev_secret_key")
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024

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
    try:
        if os.path.exists(ADMIN_ID_FILE):
            with open(ADMIN_ID_FILE, "r") as f:
                value = f.read().strip()
                return int(value) if value else None
    except Exception as e:
        logger.error(f"❌ Ошибка чтения admin_id: {e}")
    return None


def save_admin_id(user_id: int):
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
            if response.status_code != 200:
                logger.error(f"❌ send_message: {response.status_code} - {response.text[:200]}")
            return response.status_code == 200
        except Exception as e:
            logger.error(f"❌ Ошибка отправки: {e}")
            return False

    def send_message_to_chat(self, chat_id, text):
        if not self.token:
            return False
        try:
            payload = {"text": text, "format": "markdown"}
            response = requests.post(
                f"{self.base_url}/messages",
                headers={"Authorization": self.token, "Content-Type": "application/json"},
                params={"chat_id": chat_id},
                json=payload,
                timeout=30,
                verify=False
            )
            if response.status_code == 200:
                return True
            logger.error(f"❌ send_message_to_chat: {response.status_code} - {response.text[:200]}")
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
                "text": text,
                "format": "markdown",
                "attachments": attachments
            }
            logger.info(f"📤 send_message_with_attachments: chat_id={chat_id}, tokens={tokens}")
            response = requests.post(
                f"{self.base_url}/messages",
                headers={"Authorization": self.token, "Content-Type": "application/json"},
                params={"chat_id": chat_id},
                json=payload,
                timeout=60,
                verify=False
            )
            logger.info(f"📤 Ответ: HTTP {response.status_code}, {response.text[:300]}")
            if response.status_code == 200:
                return True
            logger.error(f"❌ Ошибка: {response.status_code} - {response.text[:300]}")
            return False
        except Exception as e:
            logger.error(f"❌ Ошибка: {e}")
            return False

    def upload_file(self, image_bytes, filename='image.jpg'):
        """
        Загрузка изображения в MAX API.
        
        Согласно документации [citation:1][citation:9]:
        - ШАГ 1: POST /uploads?type=image → получаем url и ТОКЕН
        - ШАГ 2: POST на полученный url → загружаем файл
        
        ВАЖНО: Для изображений токен приходит НА ШАГЕ 1 (в ответе на /uploads),
        а не после загрузки файла!
        """
        if not self.token:
            logger.error("❌ Нет токена для загрузки")
            return None

        try:
            # ШАГ 1: Получаем URL для загрузки И токен
            logger.info(f"📤 ШАГ 1: Запрос upload URL ({len(image_bytes)} байт)")
            response = requests.post(
                f"{self.base_url}/uploads",
                headers={"Authorization": self.token},
                params={"type": "image"},
                timeout=30,
                verify=False
            )
            logger.info(f"📤 ШАГ 1: HTTP {response.status_code}, ответ: {response.text[:500]}")

            if response.status_code != 200:
                logger.error(f"❌ Ошибка получения URL: {response.status_code} - {response.text[:300]}")
                return None

            try:
                upload_data = response.json()
            except ValueError:
                logger.error(f"❌ Невалидный JSON: {response.text[:200]}")
                return None

            upload_url = upload_data.get('url')
            # ✅ КЛЮЧЕВОЕ ИСПРАВЛЕНИЕ: для изображений токен приходит уже на шаге 1!
            token = upload_data.get('token')
            
            logger.info(f"📤 Получен upload_url: {upload_url}")
            logger.info(f"📤 Получен token (шаг 1): {token[:40] if token else 'НЕТ'}...")

            if not upload_url:
                logger.error(f"❌ Не получен URL: {upload_data}")
                return None

            # ШАГ 2: Загружаем файл по полученному URL
            files = {'data': (filename, image_bytes, 'image/jpeg')}
            logger.info(f"📤 ШАГ 2: POST на {upload_url}")

            upload_response = requests.post(
                upload_url,
                files=files,
                timeout=60,
                verify=False
            )
            logger.info(f"📤 ШАГ 2: HTTP {upload_response.status_code}, ответ: {upload_response.text[:500]}")

            if upload_response.status_code != 200:
                logger.error(f"❌ Ошибка загрузки: {upload_response.status_code} - {upload_response.text[:200]}")
                return None

            # Если токен не пришёл на шаге 1 — пробуем извлечь из ответа шага 2
            if not token:
                try:
                    upload_result = upload_response.json()
                    logger.info(f"📤 ШАГ 2: JSON ответа: {upload_result}")
                    
                    # Для изображений может быть структура photos
                    if 'photos' in upload_result and isinstance(upload_result['photos'], dict):
                        for photo_key, photo_data in upload_result['photos'].items():
                            if isinstance(photo_data, dict) and 'token' in photo_data:
                                token = photo_data['token']
                                logger.info(f"✅ Токен найден в photos[{photo_key}]: {token[:40]}...")
                                break
                    
                    if not token and 'token' in upload_result:
                        token = upload_result['token']
                        logger.info(f"✅ Токен найден на верхнем уровне: {token[:40]}...")
                except ValueError:
                    logger.warning(f"⚠️ Не удалось распарсить JSON шага 2: {upload_response.text[:200]}")

            if not token:
                logger.error(f"❌ Токен не найден ни на шаге 1, ни на шаге 2")
                return None

            logger.info(f"✅ Файл загружен, токен: {token[:40]}...")
            return token

        except Exception as e:
            logger.error(f"❌ Исключение при загрузке: {e}")
            import traceback
            traceback.print_exc()
            return None

    def delete_message(self, message_id):
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
            logger.error(f"❌ Ошибка удаления {message_id}: {response.status_code}")
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
        <div class="note">После отправки с вами свяжется эксперт по лизингу.</div>
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


# ========== HTML АДМИН-ПАНЕЛИ ==========

ADMIN_PAGE = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Админ-панель бота</title>
    <style>
        body { font-family: -apple-system, Arial, sans-serif; background: #f0f2f5; margin: 0; padding: 20px; }
        .container { max-width: 700px; margin: 30px auto; }
        .card { background: white; padding: 30px; border-radius: 12px; box-shadow: 0 4px 20px rgba(0,0,0,0.08); margin-bottom: 20px; }
        h1 { color: #1a1a1a; margin-top: 0; font-size: 22px; }
        h2 { color: #333; font-size: 16px; margin-top: 0; }
        .status-row { display: flex; justify-content: space-between; padding: 8px 0; border-bottom: 1px solid #eee; font-size: 14px; }
        .status-row:last-child { border-bottom: none; }
        .status-label { color: #666; }
        .status-value { font-weight: 600; color: #1a1a1a; }
        .btn { padding: 12px 24px; border: none; border-radius: 8px; font-size: 15px; font-weight: 600; cursor: pointer; margin-right: 10px; margin-bottom: 10px; transition: all 0.2s; }
        .btn-primary { background: #007bff; color: white; }
        .btn-primary:hover { background: #0056b3; }
        .btn-secondary { background: #6c757d; color: white; }
        .btn-secondary:hover { background: #545b62; }
        .btn-warning { background: #ffc107; color: #333; }
        .btn-warning:hover { background: #e0a800; }
        .btn:disabled { opacity: 0.5; cursor: not-allowed; }
        .log { background: #1e1e1e; color: #d4d4d4; padding: 15px; border-radius: 8px; font-family: 'Courier New', monospace; font-size: 12px; max-height: 300px; overflow-y: auto; margin-top: 15px; white-space: pre-wrap; line-height: 1.5; }
        .mode-badge { display: inline-block; padding: 4px 12px; border-radius: 20px; font-size: 12px; font-weight: 600; margin-left: 10px; }
        .mode-test { background: #fff3cd; color: #856404; }
        .mode-live { background: #d4edda; color: #155724; }
        .warning { background: #fff3cd; padding: 12px 15px; border-radius: 8px; border-left: 4px solid #ffc107; margin-bottom: 15px; font-size: 14px; color: #856404; }
    </style>
</head>
<body>
    <div class="container">
        <div class="card">
            <h1>🎛️ Админ-панель бота</h1>
            <div id="modeInfo"></div>
        </div>
        <div class="card">
            <h2>📊 Статистика</h2>
            <div id="stats">Загрузка...</div>
        </div>
        <div class="card">
            <h2>🚀 Ручное управление</h2>
            <div class="warning">
                ⚠️ Кнопка «Опубликовать сейчас» запустит публикацию немедленно.<br>
                Если <strong>TEST_MODE=true</strong> — пост придёт вам в личку, а не в канал.
            </div>
            <button class="btn btn-primary" onclick="publishNow()">🚀 Опубликовать сейчас</button>
            <button class="btn btn-secondary" onclick="refreshListings()">🔄 Обновить listings.db</button>
            <button class="btn btn-warning" onclick="cleanupOld()">🗑️ Очистить старые посты</button>
            <div id="log" class="log" style="display:none;"></div>
        </div>
    </div>
    <script>
        const logDiv = document.getElementById('log');
        function addLog(text) {
            logDiv.style.display = 'block';
            logDiv.textContent += new Date().toLocaleTimeString() + ' → ' + text + '\\n';
            logDiv.scrollTop = logDiv.scrollHeight;
        }
        async function loadStats() {
            try {
                const r = await fetch('/admin_stats');
                const d = await r.json();
                const modeBadge = d.test_mode
                    ? '<span class="mode-badge mode-test">🧪 ТЕСТОВЫЙ РЕЖИМ</span>'
                    : '<span class="mode-badge mode-live">🔴 БОЕВОЙ РЕЖИМ</span>';
                document.getElementById('modeInfo').innerHTML =
                    '<div style="font-size:14px;color:#666;">Режим: ' + modeBadge +
                    (d.test_mode ? '<br><small>Посты идут в личку админу.</small>'
                                 : '<br><small>Канал: <code>' + d.channel_id + '</code></small>') +
                    '</div>';
                document.getElementById('stats').innerHTML =
                    '<div class="status-row"><span class="status-label">📦 Опубликовано</span><span class="status-value">' + d.published_total + '</span></div>' +
                    '<div class="status-row"><span class="status-label">⏳ В очереди</span><span class="status-value">' + d.pending + '</span></div>' +
                    '<div class="status-row"><span class="status-label">📊 Всего</span><span class="status-value">' + d.listings_total + '</span></div>' +
                    '<div class="status-row"><span class="status-label">👤 Админ</span><span class="status-value">' + (d.admin_id || '—') + '</span></div>';
            } catch (e) {
                document.getElementById('stats').textContent = 'Ошибка: ' + e.message;
            }
        }
        async function publishNow() {
            addLog('🚀 Запуск публикации...');
            try {
                const r = await fetch('/manual_publish', {method: 'POST'});
                const d = await r.json();
                addLog(d.success ? '✅ ' + d.message : '❌ ' + d.message);
                loadStats();
            } catch (e) { addLog('❌ ' + e.message); }
        }
        async function refreshListings() {
            addLog('🔄 Обновление...');
            try {
                const r = await fetch('/refresh_listings', {method: 'POST'});
                const d = await r.json();
                addLog(d.success ? '✅ Обновлено' : '❌ Ошибка');
                loadStats();
            } catch (e) { addLog('❌ ' + e.message); }
        }
        async function cleanupOld() {
            addLog('🗑️ Очистка...');
            try {
                const r = await fetch('/manual_cleanup', {method: 'POST'});
                const d = await r.json();
                addLog(d.success ? '✅ ' + d.message : '❌ ' + d.message);
                loadStats();
            } catch (e) { addLog('❌ ' + e.message); }
        }
        loadStats();
        setInterval(loadStats, 30000);
    </script>
</body>
</html>
"""


# ========== МАРШРУТЫ ==========

@app.route('/')
def index():
    admin_id = get_admin_id()
    test_mode = os.environ.get("TEST_MODE", "false").lower() == "true"
    return jsonify({
        "status": "running",
        "admin_configured": admin_id is not None,
        "test_mode": test_mode,
    })


@app.route('/health')
def health():
    return {"status": "ok", "token_set": bool(TOKEN)}


@app.route('/consultation', methods=['GET'])
def consultation_page():
    return render_template_string(CONSULTATION_PAGE)


@app.route('/admin', methods=['GET'])
def admin_page():
    return render_template_string(ADMIN_PAGE)


@app.route('/admin_stats')
def admin_stats():
    try:
        published_stats = db.stats()
        admin_id = get_admin_id()

        listings_total = 0
        pending = 0
        try:
            import sqlite3
            listings_path = os.path.join(DATA_DIR, "listings.db")
            if os.path.exists(listings_path):
                conn = sqlite3.connect(listings_path, timeout=10)
                conn.row_factory = sqlite3.Row
                listings_total = conn.execute("SELECT COUNT(*) as c FROM listings").fetchone()["c"]
                pending = conn.execute("SELECT COUNT(*) as c FROM listings WHERE status='pending'").fetchone()["c"]
                conn.close()
        except Exception as e:
            logger.error(f"Ошибка чтения listings.db: {e}")

        return jsonify({
            "published_total": published_stats.get("published_total", 0),
            "listings_total": listings_total,
            "pending": pending,
            "admin_id": admin_id,
            "channel_id": CHANNEL_ID,
            "test_mode": os.environ.get("TEST_MODE", "false").lower() == "true",
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/submit_consultation', methods=['POST'])
def submit_consultation():
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

        return jsonify({
            'success': True,
            'message': 'Заявка отправлена',
            'consultation_id': consultation_id
        })

    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"ok": True}), 200

        update_type = data.get('update_type')

        if update_type == 'message_created':
            message = data.get('message', {})
            sender = message.get('sender', {})
            body = message.get('body', {})

            user_id = sender.get('user_id')
            text = body.get('text', '').strip() if body.get('text') else ''

            logger.info(f"📨 user_id={user_id}, text={text}")

            current_admin = get_admin_id()

            if current_admin is None and text == '/start' and user_id:
                save_admin_id(user_id)
                api.send_message(
                    user_id,
                    "✅ **Вы зарегистрированы как администратор!**\n\n"
                    "Команды:\n"
                    "/start — меню\n"
                    "/admin_reset — сбросить админа\n"
                    "/status — статус"
                )
                return jsonify({"ok": True}), 200

            if not is_admin(user_id):
                return jsonify({"ok": True}), 200

            if text == '/start':
                api.send_message(
                    user_id,
                    f"🏠 **Главное меню**\n\n"
                    f"🎛️ Админ-панель: https://{request.host}/admin\n"
                    f"📝 Форма консультации: https://{request.host}/consultation"
                )
                return jsonify({"ok": True}), 200

            if text == '/admin_reset':
                if os.path.exists(ADMIN_ID_FILE):
                    os.remove(ADMIN_ID_FILE)
                api.send_message(user_id, "🗑️ Admin ID сброшен.")
                return jsonify({"ok": True}), 200

            if text == '/status':
                stats = db.stats()
                test_mode = os.environ.get("TEST_MODE", "false").lower() == "true"
                api.send_message(
                    user_id,
                    f"📊 **Статус бота**\n\n"
                    f"📦 Опубликовано: {stats.get('published_total', 0)}\n"
                    f"📡 Канал: `{CHANNEL_ID}`\n"
                    f"🧪 Тестовый режим: {'ДА' if test_mode else 'НЕТ'}"
                )
                return jsonify({"ok": True}), 200

            return jsonify({"ok": True}), 200

        return jsonify({"ok": True}), 200

    except Exception as e:
        logger.error(f"❌ Ошибка в вебхуке: {e}")
        return jsonify({"ok": False}), 500


@app.route('/setup_webhook')
def setup_webhook():
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
    try:
        sched_module.publish_random_post(force=True)
        return jsonify({'success': True, 'message': 'Публикация запущена'})
    except Exception as e:
        logger.error(f"❌ Ошибка ручной публикации: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/manual_cleanup', methods=['POST'])
def manual_cleanup():
    try:
        sched_module.cleanup_old_posts()
        return jsonify({'success': True, 'message': 'Очистка запущена'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/refresh_listings', methods=['POST'])
def refresh_listings_route():
    try:
        ok = sched_module.refresh_listings()
        return jsonify({'success': ok})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    if TOKEN:
        logger.info(f"✅ Токен найден (первые 10): {TOKEN[:10]}...")

    test_mode = os.environ.get("TEST_MODE", "false").lower() == "true"
    logger.info(f"🧪 TEST_MODE: {test_mode}")

    admin_id = get_admin_id()
    if admin_id:
        logger.info(f"✅ Admin ID: {admin_id}")
    else:
        logger.info("ℹ️ Admin ID не задан. Напишите боту /start.")

    app.run(host='0.0.0.0', port=port, threaded=True)
