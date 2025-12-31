from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from sqlalchemy import select
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardButton, BufferedInputFile
import pandas as pd
import os

from database import AsyncSessionLocal, SupportRequest, User, Role
from keyboards import get_main_menu_keyboard, get_cancel_keyboard
from states import SupportResponse

router = Router()

# Проверка роли "Глав Тех Специалист"
async def is_tech_specialist(user_id: int) -> bool:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.telegram_id == user_id))
        user = result.scalar_one_or_none()
        return user.role == Role.CHIEF_TECH.value if user else False

# Команда для просмотра очереди обращений + кнопка экспорта
@router.message(Command("support_requests"))
async def list_support_requests(message: types.Message):
    if not await is_tech_specialist(message.from_user.id):
        await message.answer("Доступ запрещён. Только для Главного Тех Специалиста.")
        return

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(SupportRequest).order_by(SupportRequest.id))
        requests = result.scalars().all()

        if not requests:
            await message.answer(
                "Очередь обращений в техподдержку пуста.",
                reply_markup=get_main_menu_keyboard("Глав Тех Специалист")
            )
            return

        builder = InlineKeyboardBuilder()
        text = "<b>Очередь обращений в техподдержку:</b>\n\n"
        for req in requests:
            user = await session.get(User, req.user_id)
            status_emoji = "✅" if req.status == "resolved" else "⏳"
            status_text = "Обработано" if req.status == "resolved" else "Ожидает ответа"
            text += f"{status_emoji} <b>ID обращения: {req.id}</b> ({status_text})\n"
            text += f"От: {user.full_name or 'Без имени'} (@{user.telegram_id})\n"
            text += f"Сообщение:\n{req.message}\n"
            if req.response:
                text += f"\nОтвет:\n{req.response}\n"
            text += "\n"

            if req.status == "pending":
                builder.row(
                    InlineKeyboardButton(text=f"Ответить на обращение {req.id}", callback_data=f"support_answer_{req.id}")
                )

        # Кнопка экспорта всей очереди
        builder.row(InlineKeyboardButton(text="📊 Экспорт обращений в CSV", callback_data="export_support_csv"))

        builder.row(InlineKeyboardButton(text="🔙 Главное меню", callback_data="back_to_menu"))
        await message.answer(text, reply_markup=builder.as_markup())

# Экспорт обращений в CSV
@router.callback_query(F.data == "export_support_csv")
async def export_support_csv(callback: types.CallbackQuery):
    if not await is_tech_specialist(callback.from_user.id):
        await callback.answer("Доступ запрещён.", show_alert=True)
        return

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(SupportRequest).order_by(SupportRequest.id))
        requests = result.scalars().all()

        data = []
        for req in requests:
            user = await session.get(User, req.user_id)
            data.append({
                "ID обращения": req.id,
                "Telegram ID": user.telegram_id,
                "ФИО": user.full_name or "—",
                "Сообщение": req.message,
                "Статус": req.status,
                "Ответ": req.response or "—"
            })

    if not data:
        await callback.answer("Нет данных для экспорта", show_alert=True)
        return

    df = pd.DataFrame(data)
    filename = "support_requests_export.csv"
    df.to_csv(filename, index=False, encoding="utf-8-sig")

    with open(filename, "rb") as f:
        file = BufferedInputFile(f.read(), filename=filename)

    await callback.message.answer_document(file, caption="📊 Экспорт всех обращений в техподдержку")
    await callback.answer("Файл отправлен!")
    os.remove(filename)

# Начало ответа на обращение
@router.callback_query(F.data.startswith("support_answer_"))
async def start_support_response(callback: types.CallbackQuery, state: FSMContext):
    req_id = int(callback.data.split("_")[-1])
    await state.update_data(request_id=req_id)
    await state.set_state(SupportResponse.response_text)

    await callback.message.edit_text(
        f"Ответ на обращение <b>ID {req_id}</b>\n\n"
        "Введите текст ответа участнику:",
        reply_markup=get_cancel_keyboard()
    )
    await callback.answer()

# Отправка ответа участнику
@router.message(SupportResponse.response_text)
async def send_support_response(message: types.Message, state: FSMContext):
    data = await state.get_data()
    req_id = data["request_id"]
    response_text = message.text

    async with AsyncSessionLocal() as session:
        req = await session.get(SupportRequest, req_id)
        if not req or req.status == "resolved":
            await message.answer("Обращение не найдено или уже обработано.")
            await state.clear()
            return

        req.status = "resolved"
        req.response = response_text
        await session.commit()

        user = await session.get(User, req.user_id)

        try:
            await message.bot.send_message(
                user.telegram_id,
                f"📩 <b>Ответ от техподдержки</b>\n\n"
                f"По вашему обращению:\n\"{req.message}\"\n\n"
                f"Ответ:\n{response_text}"
            )
        except:
            await message.answer("Не удалось отправить ответ (пользователь заблокировал бота).")

    await message.answer(
        f"Ответ на обращение ID {req_id} отправлен.",
        reply_markup=get_main_menu_keyboard("Глав Тех Специалист")
    )
    await state.clear()