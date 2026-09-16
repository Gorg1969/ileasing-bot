## Переменные окружения (панель Bothost)

Все секреты задаются в панели хостинга Bothost → раздел «Переменные окружения».
В репозитории никаких `.env` файлов нет.

### Обязательные

| Переменная | Пример | Назначение |
|---|---|---|
| `MAX_USER_TOKEN` | `abc123...` | Токен юзербота (из DevTools web.max.ru) |
| `MAX_BOT_TOKEN` | `123:ABC...` | Токен бота-публикатора (от @MasterBot) |
| `MAX_CHANNEL_ID` | `-73112403724817` | ID канала для публикации (бот — админ) |
| `ADMIN_PASS` | `strong_password` | Пароль для входа в админку |

### Опциональные

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `SECRET_KEY` | `dev_secret_key_change_me` | Секрет Flask-сессий |
| `PORT` | `3001` | Порт Flask |
| `PUBLIC_URL` | `https://project2.bothost.tech` | Публичный URL |
| `ADMIN_USER` | `admin` | Логин в админку |
| `ADMIN_IDS` | — | ID пользователей MAX для /start |
| `DATA_DIR` | `/app/data` | Папка данных |
| `MEDIA_DIR` | `/app/data/media` | Папка медиа |
| `DB_PATH` | `/app/data/project2.db` | Путь к SQLite |
| `LOG_DIR` | `/app/logs` | Папка логов |
| `SCHEDULE_START` | `06:00` | Начало окна публикаций (МСК) |
| `SCHEDULE_END` | `20:00` | Конец окна публикаций (МСК) |
| `DAILY_LIMIT` | `50` | Лимит публикаций в день |
| `PARSE_TIMES` | `06:00,18:00` | Времена запуска парсера (МСК) |
| `PARSE_LIMIT_PER_GROUP` | `30` | Сколько постов брать с группы за раз |
| `HUMAN_DELAY_HISTORY` | `2,4` | Пауза между запросами истории (сек, min,max) |
| `HUMAN_DELAY_PHOTO` | `1,2` | Пауза между скачиванием фото |
| `HUMAN_DELAY_POST` | `2,3` | Пауза между обработкой постов |
| `HUMAN_DELAY_GROUP` | `10,20` | Пауза между группами |
