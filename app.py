# app. v-4
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
                logger.error(f"❌ send_message: {response.status_code}")
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
            logger.error(f"❌ send_message_to_chat: {response.status_code}")
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
            logger.info(f"📤 send_message_with_attachments: chat_id={chat_id}, tokens={len(tokens)} шт.")
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
            return response.status_code == 200
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
        .btn-success { background: #28a745; color: white; }
        .btn-success:hover { background: #218838; }
        .btn:disabled { opacity: 0.5; cursor: not-allowed; }
        .log { background: #1e1e1e; color: #d4d4d4; padding: 15px; border-radius: 8px; font-family: 'Courier New', monospace; font-size: 12px; max-height: 300px; overflow-y: auto; margin-top: 15px; white-space: pre-wrap; line-height: 1.5; }
        .mode-badge { display: inline-block; padding: 4px 12px; border-radius: 20px; font-size: 12px; font-weight: 600; margin-left: 10px; }
        .mode-test { background: #fff3cd; color: #856404; }
        .mode-live { background: #d4edda; color: #155724; }
        .warning { background: #fff3cd; padding: 12px 15px; border-radius: 8px; border-left: 4px solid #ffc107; margin-bottom: 15px; font-size: 14px; color: #856404; }
        .freq-grid { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 10px; }
        .freq-btn { padding: 10px 20px; border: 2px solid #007bff; background: white; color: #007bff; border-radius: 8px; font-size: 15px; font-weight: 600; cursor: pointer; transition: all 0.2s; }
        .freq-btn:hover { background: #e7f5ff; }
        .freq-btn.active { background: #007bff; color: white; }
        .freq-current { color: #28a745; font-weight: 600; }
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
            <h2>📅 Частота публикации</h2>
            <p style="font-size:14px; color:#666; margin-top:0;">
                Текущая частота: <span class="freq-current" id="freqValue">—</span>
            </p>
            <div class="freq-grid" id="freqGrid">
                <button class="freq-btn" onclick="setFrequency(1)">1/день</button>
                <button class="freq-btn" onclick="setFrequency(2)">2/день</button>
                <button class="freq-btn" onclick="setFrequency(3)">3/день</button>
                <button class="freq-btn" onclick="setFrequency(5)">5/день</button>
                <button class="freq-btn" onclick="setFrequency(10)">10/день</button>
                <button class="freq-btn" onclick="setFrequency(20)">20/день</button>
            </div>
            <div id="freqLog" style="margin-top:10px; font-size:13px;"></div>
        </div>
        <div class="card">
            <h2>🚀 Ручное управление</h2>
            <div class="warning">
                ⚠️ Кнопка «Опубликовать сейчас» запустит публикацию немедленно.<br>
                Если <strong>TEST_MODE=true</strong> — пост придёт вам в личку.
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
                    '<div class="status-row"><span class="status-label">📊 Всего в очереди</span><span class="status-value">' + d.listings_total + '</span></div>' +
                    '<div class="status-row"><span class="status-label">👤 Админ</span><span class="status-value">' + (d.admin_id || '—') + '</span></div>' +
                    '<div class="status-row"><span class="status-label">📅 Частота</span><span class="status-value">' + d.posts_per_day + '/день</span></div>';
                document.getElementById('freqValue').textContent = d.posts_per_day + ' постов/день';
                // Подсветка активной кнопки
                document.querySelectorAll('.freq-btn').forEach(btn => {
                    btn.classList.toggle('active', btn.textContent.startsWith(d.posts_per_day + '/'));
                });
            } catch (e) {
                document.getElementById('stats').textContent = 'Ошибка: ' + e.message;
            }
        }
        async function setFrequency(value) {
            const el = document.getElementById('freqLog');
            el.textContent = '⏳ Сохранение...';
            try {
                const r = await fetch('/admin_settings', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({posts_per_day: value})
                });
                const d = await r.json();
                if (d.success) {
                    el.textContent = '✅ ' + d.message;
                    el.style.color = '#28a745';
                    loadStats();
                } else {
                    el.textContent = '❌ ' + d.message;
                    el.style.color = '#dc3545';
                }
            } catch (e) {
                el.textContent = '❌ ' + e.message;
                el.style.color = '#dc3545';
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

        pending_queue = 0
        try:
            pending_queue = db.count_pending_queue()
        except Exception as e:
            logger.error(f"Ошибка чтения pending_queue: {e}")

        return jsonify({
            "published_total": published_stats.get("published_total", 0),
            "listings_total": pending_queue,
            "pending": pending_queue,
            "admin_id": admin_id,
            "channel_id": CHANNEL_ID,
            "test_mode": os.environ.get("TEST_MODE", "false").lower() == "true",
            "posts_per_day": db.get_posts_per_day(),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/admin_settings', methods=['POST'])
def admin_settings():
    try:
        data = request.get_json()
        if not data or "posts_per_day" not in data:
            return jsonify({"success": False, "message": "Нет posts_per_day"}), 400

        value = int(data["posts_per_day"])
        if value < 1 or value > 48:
            return jsonify({"success": False, "message": "Допустимо 1-48"}), 400

        db.set_posts_per_day(value)
        sched_module.apply_schedule(value)

        return jsonify({
            "success": True,
            "message": f"Частота изменена на {value} постов/день. Расписание обновлено."
        })
    except Exception as e:
        logger.error(f"❌ Ошибка admin_settings: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


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
                try:
                    pending_q = db.count_pending_queue()
                except Exception:
                    pending_q = 0
                api.send_message(
                    user_id,
                    f"📊 **Статус бота**\n\n"
                    f"📦 Опубликовано: {stats.get('published_total', 0)}\n"
                    f"⏳ В очереди: {pending_q}\n"
                    f"📅 Частота: {db.get_posts_per_day()}/день\n"
                    f"📡 Канал: `{CHANNEL_ID}`\n"
                    f"🧪 TEST_MODE: {'ДА' if test_mode else 'НЕТ'}"
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

@app.route('/admin_clear_pending', methods=['GET', 'POST'])
def admin_clear_pending():
    try:
        with db._connect() as conn:
            cnt = conn.execute("SELECT COUNT(*) FROM pending_queue").fetchone()[0]
            conn.execute("DELETE FROM pending_queue")
            conn.commit()
        return jsonify({"success": True, "message": f"Удалено {cnt} записей из pending_queue"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
