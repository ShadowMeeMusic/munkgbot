import os
from dotenv import load_dotenv

load_dotenv()

# Токен бота (обязательно в .env)
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден в .env файле! Укажи его: BOT_TOKEN=твой_токен")

# ID главных админов (через запятую в .env, например: 123456789,987654321)
CHIEF_ADMIN_IDS_STR = os.getenv("CHIEF_ADMIN_IDS", "")
if not CHIEF_ADMIN_IDS_STR.strip():
    raise ValueError("CHIEF_ADMIN_IDS не найден в .env! Укажи хотя бы свой ID")

CHIEF_ADMIN_IDS = [int(id_str.strip()) for id_str in CHIEF_ADMIN_IDS_STR.split(",") if id_str.strip()]

# Путь к базе данных SQLite
DB_PATH = "mun_bot.db"

# В config.py (в конец файла)
TECH_SPECIALIST_ID = 7838905671# ← Твой ID для Главного Тех Специалиста7838905670