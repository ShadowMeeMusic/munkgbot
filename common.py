from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select, func
from datetime import datetime
import os

from database import (
    AsyncSessionLocal,
    Conference,
    Application,
    User,
    Role,
    ConferenceCreationRequest,
    SupportRequest,
    get_or_create_user
)
from keyboards import get_conferences_keyboard, get_cancel_keyboard, get_main_menu_keyboard
from states import ParticipantRegistration, CreateConferenceRequest, SupportAppeal

router = Router()

os.makedirs("qr_codes", exist_ok=True)

# Валидация дат для создания конференции
def validate_conference_dates(date_start: str, date_end: str) -> str | None:
    today = datetime.now().date()
    try:
        start = datetime.strptime(date_start.strip(), "%Y-%m-%d").date()
        end = datetime.strptime(date_end.strip(), "%Y-%m-%d").date()
    except ValueError:
        return "Неверный формат даты. Используйте строго ГГГГ-ММ-ДД."

    if start < today:
        return "Дата начала не может быть раньше сегодняшнего дня."
    if end < start:
        return "Дата окончания не может быть раньше даты начала."

    return None

# Список конференций
@router.message(Command("conferences"))
async def cmd_conferences(message: types.Message):
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Conference).where(Conference.is_active == True)
        )
        conferences = result.scalars().all()

        if not conferences:
            await message.answer(
                "😔 Пока нет актуальных конференций.\n"
                "Следите за обновлениями или создайте свою!"
            )
            return

        text = "<b>Актуальные конференции Модели ООН:</b>\n\n"
        for i, conf in enumerate(conferences, 1):
            fee_text = f"💸 Оргвзнос: {conf.fee} руб." if conf.fee > 0 else "🆓 Бесплатно"
            text += f"{i}. <b>{conf.name}</b>\n"
            text += f"📍 {conf.city or 'Онлайн'}\n"
            text += f"📅 {conf.date_start} — {conf.date_end}\n"
            text += f"{fee_text}\n\n"

        text += "Нажмите на конференцию, чтобы подать заявку:"

        keyboard = get_conferences_keyboard(conferences)
        await message.answer(text, reply_markup=keyboard)

# Начало регистрации
@router.message(Command("register"))
async def cmd_register(message: types.Message):
    await cmd_conferences(message)
    await message.answer("Выберите конференцию выше для подачи заявки.")

# Выбор конференции — с проверкой даты
@router.callback_query(F.data.startswith("select_conf_"))
async def select_conference(callback: types.CallbackQuery, state: FSMContext):
    conf_id = int(callback.data.split("_")[-1])

    async with AsyncSessionLocal() as session:
        conf = await session.get(Conference, conf_id)
        if not conf:
            await callback.answer("Конференция не найдена.", show_alert=True)
            return

        # Проверка даты начала конференции
        today = datetime.now().date()
        try:
            start_date = datetime.strptime(conf.date_start.strip(), "%Y-%m-%d").date()
        except ValueError:
            await callback.answer("Ошибка в дате конференции.", show_alert=True)
            return

        if start_date < today:
            await callback.answer("Нельзя подать заявку на конференцию, которая уже началась или прошла.", show_alert=True)
            return

    await state.update_data(conference_id=conf_id)
    await state.set_state(ParticipantRegistration.full_name)

    await callback.message.edit_text(
        "✅ Конференция выбрана!\n\n"
        "<b>Заполните анкету участника</b>\n\n"
        "1. ФИО (полностью):",
        reply_markup=get_cancel_keyboard()
    )
    await callback.answer()

# Шаги анкеты
@router.message(ParticipantRegistration.full_name)
async def process_full_name(message: types.Message, state: FSMContext):
    await state.update_data(full_name=message.text.strip())
    await state.set_state(ParticipantRegistration.age)
    await message.answer("2. Возраст (от 11 до 99 лет):", reply_markup=get_cancel_keyboard())

@router.message(ParticipantRegistration.age)
async def process_age(message: types.Message, state: FSMContext):
    text = message.text.strip()
    try:
        age = int(text)
        if age < 11 or age > 99:
            await message.answer("Возраст должен быть от 11 до 99 лет. Повторите ввод:")
            return
    except ValueError:
        await message.answer("Введите возраст цифрами (от 11 до 99 лет):")
        return

    await state.update_data(age=age)
    await state.set_state(ParticipantRegistration.email)
    await message.answer("3. Email:", reply_markup=get_cancel_keyboard())

@router.message(ParticipantRegistration.email)
async def process_email(message: types.Message, state: FSMContext):
    await state.update_data(email=message.text.strip())
    await state.set_state(ParticipantRegistration.institution)
    await message.answer("4. Учебное заведение:", reply_markup=get_cancel_keyboard())

@router.message(ParticipantRegistration.institution)
async def process_institution(message: types.Message, state: FSMContext):
    await state.update_data(institution=message.text.strip())
    await state.set_state(ParticipantRegistration.experience)
    await message.answer("5. Опыт участия в MUN (кратко, если есть):", reply_markup=get_cancel_keyboard())

@router.message(ParticipantRegistration.experience)
async def process_experience(message: types.Message, state: FSMContext):
    await state.update_data(experience=message.text.strip())
    await state.set_state(ParticipantRegistration.committee)
    await message.answer("6. Желаемый комитет:", reply_markup=get_cancel_keyboard())

# Финал — сохранение заявки
@router.message(ParticipantRegistration.committee)
async def process_committee(message: types.Message, state: FSMContext):
    data = await state.get_data()
    data["committee"] = message.text.strip()

    async with AsyncSessionLocal() as session:
        user_result = await session.execute(select(User).where(User.telegram_id == message.from_user.id))
        user = user_result.scalar_one()

        user.full_name = data.get("full_name")
        user.age = data.get("age")
        user.email = data.get("email")
        user.institution = data.get("institution")
        user.experience = data.get("experience")

        application = Application(
            user_id=user.id,
            conference_id=data["conference_id"],
            committee=data["committee"],
            status="pending"
        )
        session.add(application)
        await session.commit()
        await session.refresh(application)

        conf = await session.get(Conference, data["conference_id"])

        notify_text = (
            f"🔔 <b>Новая заявка на участие!</b>\n\n"
            f"Конференция: <b>{conf.name}</b>\n\n"
            f"<b>Анкета участника:</b>\n"
            f"• ФИО: {data.get('full_name')}\n"
            f"• Возраст: {data.get('age')}\n"
            f"• Email: {data.get('email')}\n"
            f"• Учебное заведение: {data.get('institution')}\n"
            f"• Опыт в MUN: {data.get('experience')}\n"
            f"• Комитет: {data['committee']}\n\n"
            f"ID заявки: <code>{application.id}</code>\n\n"
            f"Одобрить/отклонить: /applications"
        )

        if conf.organizer_id:
            try:
                await message.bot.send_message(conf.organizer.telegram_id, notify_text)
            except:
                pass

    db_user = await get_or_create_user(message.from_user.id, message.from_user.full_name)
    await message.answer(
        "✅ <b>Заявка успешно отправлена!</b>\n\n"
        "Организатор рассмотрит её в ближайшее время.\n"
        "Вы получите уведомление о результате.",
        reply_markup=get_main_menu_keyboard(db_user.role)
    )
    await state.clear()

# Создание конференции — начало
@router.message(F.text == "Создать конференцию")
async def cmd_create_conference(message: types.Message, state: FSMContext):
    async with AsyncSessionLocal() as session:
        user_result = await session.execute(select(User).where(User.telegram_id == message.from_user.id))
        user = user_result.scalar_one_or_none()

        if not user or user.role != "Участник":
            await message.answer("Эта функция доступна только Участникам.")
            return

        conf_count = await session.scalar(
            select(func.count(Conference.id)).where(Conference.organizer_id == user.id)
        )
        if conf_count > 0:
            await message.answer(
                "У вас уже есть активная конференция.\n"
                "Удалите её или дождитесь завершения, чтобы создать новую."
            )
            return

    await state.set_state(CreateConferenceRequest.name)
    await message.answer("Создание конференции. Введите название:", reply_markup=get_cancel_keyboard())

# Форма создания конференции — с валидацией дат
@router.message(CreateConferenceRequest.name)
async def process_conf_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text.strip())
    await state.set_state(CreateConferenceRequest.description)
    await message.answer("Введите описание конференции:", reply_markup=get_cancel_keyboard())

@router.message(CreateConferenceRequest.description)
async def process_conf_description(message: types.Message, state: FSMContext):
    await state.update_data(description=message.text.strip())
    await state.set_state(CreateConferenceRequest.city)
    await message.answer("Город проведения (или 'Онлайн'):", reply_markup=get_cancel_keyboard())

@router.message(CreateConferenceRequest.city)
async def process_conf_city(message: types.Message, state: FSMContext):
    await state.update_data(city=message.text.strip())
    await state.set_state(CreateConferenceRequest.date_start)
    await message.answer("Дата начала (формат: ГГГГ-ММ-ДД):", reply_markup=get_cancel_keyboard())

@router.message(CreateConferenceRequest.date_start)
async def process_conf_date_start(message: types.Message, state: FSMContext):
    date_start = message.text.strip()
    data = await state.get_data()
    date_end = data.get("date_end", "2099-12-31")

    error = validate_conference_dates(date_start, date_end)
    if error:
        await message.answer(f"Ошибка: {error}\nВведите дату начала заново (ГГГГ-ММ-ДД):")
        return

    await state.update_data(date_start=date_start)
    await state.set_state(CreateConferenceRequest.date_end)
    await message.answer("Дата окончания (формат: ГГГГ-ММ-ДД):", reply_markup=get_cancel_keyboard())

@router.message(CreateConferenceRequest.date_end)
async def process_conf_date_end(message: types.Message, state: FSMContext):
    date_end = message.text.strip()
    data = await state.get_data()
    date_start = data.get("date_start", "1900-01-01")

    error = validate_conference_dates(date_start, date_end)
    if error:
        await message.answer(f"Ошибка: {error}\nВведите дату окончания заново (ГГГГ-ММ-ДД):")
        return

    await state.update_data(date_end=date_end)
    await state.set_state(CreateConferenceRequest.fee)
    await message.answer("Оргвзнос в рублях (0 — бесплатно):", reply_markup=get_cancel_keyboard())

@router.message(CreateConferenceRequest.fee)
async def process_conf_fee(message: types.Message, state: FSMContext):
    text = message.text.strip()
    if not text.replace('.', '', 1).replace('-', '', 1).isdigit():
        await message.answer("Введите корректное число (0 для бесплатной).")
        return
    await state.update_data(fee=float(text))
    await state.set_state(CreateConferenceRequest.qr_code)
    await message.answer(
        "Если конференция платная — отправьте фото QR-кода для оплаты.\n"
        "Если бесплатная — напишите 'нет' или отправьте любое фото.",
        reply_markup=get_cancel_keyboard()
    )

@router.message(CreateConferenceRequest.qr_code, F.photo)
async def process_conf_qr_photo(message: types.Message, state: FSMContext):
    file_info = await message.bot.get_file(message.photo[-1].file_id)
    qr_path = f"qr_codes/qr_{message.from_user.id}_{message.message_id}.jpg"
    await message.bot.download_file(file_info.file_path, qr_path)
    await state.update_data(qr_code_path=qr_path)
    await finish_conference_creation(message, state)

@router.message(CreateConferenceRequest.qr_code)
async def process_conf_qr_skip(message: types.Message, state: FSMContext):
    await state.update_data(qr_code_path=None)
    await finish_conference_creation(message, state)

async def finish_conference_creation(message: types.Message, state: FSMContext):
    data = await state.get_data()

    async with AsyncSessionLocal() as session:
        user_id = (await session.execute(select(User.id).where(User.telegram_id == message.from_user.id))).scalar_one()
        req = ConferenceCreationRequest(
            user_id=user_id,
            data=data,
            status="pending"
        )
        session.add(req)
        await session.commit()

    await message.answer(
        "✅ <b>Заявка на создание конференции отправлена!</b>\n\n"
        f"Название: {data['name']}\n"
        f"Город: {data.get('city') or 'Онлайн'}\n"
        f"Даты: {data.get('date_start')} — {data.get('date_end')}\n"
        f"Оргвзнос: {data.get('fee', 0)} руб.\n\n"
        "Ожидайте одобрения Администратора.",
        reply_markup=get_main_menu_keyboard("Участник")
    )
    await state.clear()

# Обращение к тех. специалисту
@router.message(F.text == "Обращение к тех. специалисту")
async def start_support_appeal(message: types.Message, state: FSMContext):
    await state.set_state(SupportAppeal.message)
    await message.answer(
        "📩 <b>Обращение в техподдержку</b>\n\n"
        "Опишите вашу проблему или вопрос:",
        reply_markup=get_cancel_keyboard()
    )

@router.message(SupportAppeal.message)
async def save_support_appeal(message: types.Message, state: FSMContext):
    async with AsyncSessionLocal() as session:
        user_id = (await session.execute(select(User.id).where(User.telegram_id == message.from_user.id))).scalar_one()
        req = SupportRequest(
            user_id=user_id,
            message=message.text,
            status="pending"
        )
        session.add(req)
        await session.commit()

    db_user = await get_or_create_user(message.from_user.id, message.from_user.full_name)
    await message.answer(
        "✅ Ваше обращение отправлено в техподдержку.\n"
        "Мы ответим вам в ближайшее время.",
        reply_markup=get_main_menu_keyboard(db_user.role)
    )
    await state.clear()

# Помощь
@router.message(Command("help"))
async def cmd_help(message: types.Message):
    db_user = await get_or_create_user(message.from_user.id, message.from_user.full_name)
    await message.answer(
        "ℹ️ <b>Помощь</b>\n\n"
        "Если у вас проблемы с ботом — используйте кнопку \"Обращение к тех. специалисту\"\n"
        "По вопросам MUN — обратитесь к организатору вашей конференции.",
        reply_markup=get_main_menu_keyboard(db_user.role)
    )

# Универсальная отмена
@router.callback_query(F.data == "cancel_form")
async def cancel_form(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("Форма отменена.", reply_markup=get_main_menu_keyboard("Участник"))
    await callback.answer()