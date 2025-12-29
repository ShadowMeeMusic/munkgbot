import asyncio
import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.client.default import DefaultBotProperties

from config import BOT_TOKEN, CHIEF_ADMIN_IDS, TECH_SPECIALIST_ID
from database import init_db, get_or_create_user, enable_wal
from keyboards import get_main_menu_keyboard
from handlers.common import router as common_router
from handlers.organizer import router as organizer_router
from handlers.admin import router as admin_router
from handlers.tech_support import router as tech_support_router
from handlers.ban import router as ban_router

logging.basicConfig(level=logging.INFO)

default_properties = DefaultBotProperties(parse_mode="HTML")
bot = Bot(token=BOT_TOKEN, default=default_properties)
dp = Dispatcher()

# Подключаем роутеры
dp.include_router(common_router)
dp.include_router(organizer_router)
dp.include_router(admin_router)
dp.include_router(tech_support_router)
dp.include_router(ban_router)

# Универсальная функция главного меню с приветствием
async def show_main_menu(message: types.Message | types.CallbackQuery):
    if isinstance(message, types.CallbackQuery):
        user = message.from_user
        msg = message.message
    else:
        user = message.from_user
        msg = message

    db_user = await get_or_create_user(user.id, user.full_name)

    if db_user.is_banned:
        await msg.answer(
            "🚫 Вы заблокированы в боте.\n"
            "Обратитесь к техподдержке для разблокировки."
        )
        return

    welcome_text = (
        f"Привет, <b>{user.full_name or 'друг'}</b>!\n\n"
        "Добро пожаловать в <b>MUN Bot</b> — платформу для участия и организации конференций Модели ООН.\n\n"
        f"Ваша роль: <b>{db_user.role}</b>\n\n"
        "Выберите действие:"
    )

    if user.id in CHIEF_ADMIN_IDS:
        welcome_text += "\n\n🔧 <b>Вы — Главный Админ</b>. Полный доступ."

    if user.id == TECH_SPECIALIST_ID:
        welcome_text += "\n\n🛠 <b>Вы — Главный Тех Специалист</b>."

    await msg.answer(welcome_text, reply_markup=get_main_menu_keyboard(db_user.role))

# /start и /main_menu — обновление меню по роли
@dp.message(Command("start"))
@dp.message(Command("main_menu"))
async def cmd_start_or_main_menu(message: types.Message):
    await show_main_menu(message)

# Кнопка "Обновить систему" — обновляет меню
@dp.message(F.text == "Обновить систему")
async def refresh_menu(message: types.Message):
    await show_main_menu(message)

# Участник
@dp.message(F.text == "Просмотр конференций")
async def text_conferences(message: types.Message):
    from handlers.common import cmd_conferences
    await cmd_conferences(message)

@dp.message(F.text == "Подать заявку на участие")
async def text_register(message: types.Message):
    from handlers.common import cmd_register
    await cmd_register(message)

@dp.message(F.text == "Создать конференцию")
async def text_create_conference(message: types.Message, state: FSMContext):
    from handlers.common import cmd_create_conference
    await cmd_create_conference(message, state)

@dp.message(F.text == "Обращение к тех. специалисту")
async def text_support_appeal(message: types.Message, state: FSMContext):
    from handlers.common import start_support_appeal
    await start_support_appeal(message, state)

# Организатор
@dp.message(F.text == "Мои конференции")
async def text_my_conferences(message: types.Message):
    from handlers.organizer import my_conferences
    await my_conferences(message)

@dp.message(F.text == "Заявки участников")
async def text_applications(message: types.Message):
    from handlers.organizer import current_applications
    await current_applications(message)

@dp.message(F.text == "Архив заявок")
async def text_archive(message: types.Message):
    from handlers.organizer import archive_applications
    await archive_applications(message)

# Глав Тех Специалист
@dp.message(F.text == "Очередь обращений участников")
async def text_support_requests(message: types.Message):
    from handlers.tech_support import list_support_requests
    await list_support_requests(message)

@dp.message(F.text == "Список забаненных пользователей")
async def text_banned_list(message: types.Message):
    from handlers.ban import banned_list
    await banned_list(message)

@dp.message(F.text == "Бан/разбан пользователей")
async def text_ban_menu(message: types.Message):
    await message.answer(
        "Команды для бана/разбана:\n"
        "/ban @username или /ban ID — забанить\n"
        "/unban @username или /unban ID — разбанить"
    )

@dp.message(F.text == "Назначить роль другим пользователям")
async def text_set_role_tech(message: types.Message):
    await message.answer("Используйте команду /set_role @username роль")

@dp.message(F.text == "Экспортировать данные бота")
async def text_export_bot_data_tech(message: types.Message):
    from handlers.admin import export_bot_data
    await export_bot_data(message)

# Админ — убрали "Назначить роль", добавили бан
@dp.message(F.text == "Просмотр заявок на конференции")
async def text_admin_requests(message: types.Message):
    from handlers.admin import admin_conference_requests
    await admin_conference_requests(message)

@dp.message(F.text == "Статистика")
async def text_stats(message: types.Message):
    from handlers.admin import stats
    await stats(message)

# Главный Админ
@dp.message(F.text == "Просмотр заявок на конференции")
async def text_chief_admin_requests(message: types.Message):
    from handlers.admin import admin_conference_requests
    await admin_conference_requests(message)

@dp.message(F.text == "Статистика")
async def text_chief_stats(message: types.Message):
    from handlers.admin import stats
    await stats(message)

@dp.message(F.text == "Просмотр конференций")
async def text_chief_conferences(message: types.Message):
    from handlers.common import cmd_conferences
    await cmd_conferences(message)

@dp.message(F.text == "Бан/разбан пользователей")
async def text_chief_ban(message: types.Message):
    await message.answer(
        "Команды для бана/разбана:\n"
        "/ban @username или /ban ID — забанить\n"
        "/unban @username или /unban ID — разбанить"
    )

@dp.message(F.text == "Приостановка бота")
async def text_chief_pause(message: types.Message):
    await message.answer("Используйте /pause_bot и /resume_bot")

@dp.message(F.text == "Экспорт данных бота")
async def text_export_bot_data(message: types.Message):
    from handlers.admin import export_bot_data
    await export_bot_data(message)

# Общие
@dp.message(F.text == "Помощь")
async def text_help(message: types.Message):
    from handlers.common import cmd_help
    await cmd_help(message)

# Универсальная отмена и возврат в меню
@dp.callback_query(F.data == "cancel_form")
async def cancel_form(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await show_main_menu(callback)
    try:
        await callback.message.delete()
    except:
        pass
    await callback.answer()

@dp.callback_query(F.data == "back_to_menu")
async def back_to_menu(callback: types.CallbackQuery):
    await show_main_menu(callback)
    try:
        await callback.message.delete()
    except:
        pass
    await callback.answer()

# Middleware для проверки бана
@dp.update.middleware()
async def ban_middleware(handler, event, data):
    if hasattr(event, "from_user") and event.from_user:
        user = await get_or_create_user(event.from_user.id, event.from_user.full_name)
        if user.is_banned:
            await event.answer(
                "🚫 Вы заблокированы в боте.\n"
                "Обратитесь к техподдержке для разблокировки."
            )
            return
    return await handler(event, data)

async def main():
    print("Инициализация базы данных...")
    await init_db()
    await enable_wal()
    print("База готова (WAL включён). Запуск бота...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())