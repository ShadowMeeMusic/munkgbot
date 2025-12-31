from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.types import FSInputFile, InlineKeyboardButton, BufferedInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from sqlalchemy import select, func, delete
from sqlalchemy.orm import joinedload
from datetime import datetime, timedelta
import os
import pandas as pd

from database import AsyncSessionLocal, Conference, Application, User, Role, ConferenceEditRequest
from keyboards import get_main_menu_keyboard, get_cancel_keyboard
from states import RejectReason, EditConference, Broadcast
from config import CHIEF_ADMIN_IDS

router = Router()

PAYMENTS_DIR = "payments"
os.makedirs(PAYMENTS_DIR, exist_ok=True)
os.makedirs("qr_codes", exist_ok=True)
os.makedirs("posters", exist_ok=True)  # Папка для постеров

pagination = {}
last_my_conferences_msg = {}

# Проверка: Организатор и НЕ забанен
async def is_active_organizer(user_id: int) -> bool:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.telegram_id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            return False
        return user.role == Role.ORGANIZER.value and not user.is_banned

# Получение заявок
async def get_applications(user_id: int, mode: str):
    if not await is_active_organizer(user_id):
        return []

    async with AsyncSessionLocal() as session:
        organizer_result = await session.execute(select(User).where(User.telegram_id == user_id))
        organizer = organizer_result.scalar_one_or_none()

        conf_result = await session.execute(select(Conference).where(Conference.organizer_id == organizer.id))
        conf_ids = [c.id for c in conf_result.scalars().all()]
        if not conf_ids:
            return []

        query = select(Application).options(
            joinedload(Application.user),
            joinedload(Application.conference)
        ).where(Application.conference_id.in_(conf_ids))

        if mode == "current":
            query = query.where(Application.status.in_(["pending", "payment_pending", "payment_sent", "confirmed"]))
        else:
            query = query.where(Application.status.in_(["approved", "rejected", "link_sent"]))

        result = await session.execute(query.order_by(Application.id))
        return result.unique().scalars().all()

# Клавиатура для заявки
def build_keyboard(app_id: int, index: int, total: int, mode: str):
    builder = InlineKeyboardBuilder()
    if mode == "current":
        builder.row(
            InlineKeyboardButton(text="Принять", callback_data=f"approve_{app_id}"),
            InlineKeyboardButton(text="Отклонить", callback_data=f"reject_{app_id}")
        )

    nav = []
    if index > 0:
        nav.append(InlineKeyboardButton(text="◀ Назад", callback_data=f"nav_{mode}_{index-1}"))
    if index < total - 1:
        nav.append(InlineKeyboardButton(text="Вперёд ▶", callback_data=f"nav_{mode}_{index+1}"))
    if nav:
        builder.row(*nav)

    export_text = "Экспорт текущих" if mode == "current" else "Экспорт архива"
    builder.row(InlineKeyboardButton(text=f"📊 {export_text}", callback_data=f"export_{mode}"))
    builder.row(InlineKeyboardButton(text="🔙 Главное меню", callback_data="back_to_menu"))
    return builder.as_markup()

# Отображение заявки
async def show_application(target, apps: list, index: int, mode: str):
    if not apps:
        text = "Нет текущих заявок." if mode == "current" else "Архив пуст."
        await target.answer(text, reply_markup=get_main_menu_keyboard("Организатор"))
        return

    app = apps[index]
    conf = app.conference
    participant = app.user

    text = f"<b>Заявка {index + 1} из {len(apps)}</b>\n\n"
    text += f"<b>Конференция:</b> {conf.name}\n"
    text += f"<b>ID заявки:</b> <code>{app.id}</code>\n\n"
    text += f"<b>Анкета участника:</b>\n"
    text += f"• ФИО: {participant.full_name or 'Не указано'}\n"
    text += f"• Возраст: {participant.age or '—'}\n"
    text += f"• Email: {participant.email or '—'}\n"
    text += f"• Учебное заведение: {participant.institution or '—'}\n"
    text += f"• Опыт в MUN: {participant.experience or 'Нет'}\n"
    text += f"• Комитет: {app.committee or '—'}\n\n"
    text += f"<b>Статус:</b> {app.status}"
    if app.reject_reason:
        text += f"\n<b>Причина отклонения:</b> {app.reject_reason}"

    keyboard = build_keyboard(app.id, index, len(apps), mode)

    if isinstance(target, types.Message):
        await target.answer(text, reply_markup=keyboard)
    else:
        await target.message.edit_text(text, reply_markup=keyboard)

# Мои конференции
@router.message(F.text == "📋 Мои конференции")
async def my_conferences(message: types.Message):
    user_id = message.from_user.id

    if not await is_active_organizer(user_id):
        await message.answer("Доступ запрещён: вы заблокированы или не являетесь Организатором.")
        return

    async with AsyncSessionLocal() as session:
        organizer_result = await session.execute(select(User).where(User.telegram_id == user_id))
        organizer = organizer_result.scalar_one_or_none()

        result = await session.execute(select(Conference).where(Conference.organizer_id == organizer.id))
        conferences = result.scalars().all()

        if not conferences:
            await message.answer("У вас пока нет конференций.", reply_markup=get_main_menu_keyboard("Организатор"))
            return

        builder = InlineKeyboardBuilder()
        text = "<b>Ваши конференции:</b>\n\n"
        for conf in conferences:
            text += f"<b>{conf.name}</b>\n"
            text += f"Город: {conf.city or 'Онлайн'}\n"
            text += f"Дата проведения: {conf.date}\n"
            text += f"Оргвзнос: {conf.fee} руб.\n\n"

            builder.row(
                InlineKeyboardButton(text=f"Редактировать {conf.name}", callback_data=f"edit_conf_{conf.id}"),
                InlineKeyboardButton(text=f"Удалить {conf.name}", callback_data=f"delete_conf_{conf.id}")
            )
            builder.row(InlineKeyboardButton(text=f"📢 Рассылка участникам {conf.name}", callback_data=f"broadcast_{conf.id}"))
            builder.row(InlineKeyboardButton(text=f"📊 Экспорт участников {conf.name}", callback_data=f"export_conf_{conf.id}"))

        builder.row(InlineKeyboardButton(text="🔙 Главное меню", callback_data="back_to_menu"))

        if user_id in last_my_conferences_msg:
            try:
                await message.bot.delete_message(message.chat.id, last_my_conferences_msg[user_id])
            except:
                pass

        sent_msg = await message.answer(text, reply_markup=builder.as_markup())
        last_my_conferences_msg[user_id] = sent_msg.message_id

# Экспорт участников
@router.callback_query(F.data.startswith("export_conf_"))
async def export_conference_participants(callback: types.CallbackQuery):
    if not await is_active_organizer(callback.from_user.id):
        await callback.answer("Доступ запрещён: вы заблокированы.", show_alert=True)
        return

    conf_id = int(callback.data.split("_")[-1])
    async with AsyncSessionLocal() as session:
        conf = await session.get(Conference, conf_id)
        if not conf:
            await callback.answer("Конференция не найдена.")
            return

        result = await session.execute(
            select(Application).options(joinedload(Application.user)).where(Application.conference_id == conf_id)
        )
        apps = result.scalars().all()

        if not apps:
            await callback.answer("Нет участников для экспорта", show_alert=True)
            return

        data = []
        for app in apps:
            participant = app.user
            data.append({
                "ФИО": participant.full_name or "—",
                "Возраст": participant.age or "—",
                "Email": participant.email or "—",
                "Учебное заведение": participant.institution or "—",
                "Опыт в MUN": participant.experience or "—",
                "Комитет": app.committee or "—",
                "Статус": app.status,
                "Причина отклонения": app.reject_reason or "—"
            })

        df = pd.DataFrame(data)
        filename = f"participants_{conf.name.replace(' ', '_')[:20]}.xlsx"
        df.to_excel(filename, index=False)

        with open(filename, "rb") as f:
            file = BufferedInputFile(f.read(), filename=filename)

        await callback.message.answer_document(file, caption=f"📊 Экспорт участников конференции {conf.name}")
        await callback.answer("Файл отправлен!")
        os.remove(filename)

# Текущие заявки
@router.message(F.text == "📩 Заявки участников")
async def current_applications(message: types.Message):
    if not await is_active_organizer(message.from_user.id):
        await message.answer("Доступ запрещён: вы заблокированы или не являетесь Организатором.")
        return

    apps = await get_applications(message.from_user.id, "current")
    pagination[message.from_user.id] = {"mode": "current", "index": 0}
    await show_application(message, apps, 0, "current")

# Архив заявок
@router.message(F.text == "🗃 Архив заявок")
async def archive_applications(message: types.Message):
    if not await is_active_organizer(message.from_user.id):
        await message.answer("Доступ запрещён: вы заблокированы или не являетесь Организатором.")
        return

    apps = await get_applications(message.from_user.id, "archive")
    pagination[message.from_user.id] = {"mode": "archive", "index": 0}
    await show_application(message, apps, 0, "archive")

# Навигация
@router.callback_query(F.data.startswith("nav_"))
async def navigate(callback: types.CallbackQuery):
    if not await is_active_organizer(callback.from_user.id):
        await callback.answer("Доступ запрещён: вы заблокированы.", show_alert=True)
        return

    _, mode, index_str = callback.data.split("_")
    index = int(index_str)
    user_id = callback.from_user.id
    pagination[user_id]["index"] = index
    apps = await get_applications(user_id, mode)
    await show_application(callback, apps, index, mode)
    await callback.answer()

# Одобрение заявки
@router.callback_query(F.data.startswith("approve_"))
async def approve_application(callback: types.CallbackQuery):
    if not await is_active_organizer(callback.from_user.id):
        await callback.answer("Доступ запрещён: вы заблокированы.", show_alert=True)
        return

    app_id = int(callback.data.split("_")[1])
    async with AsyncSessionLocal() as session:
        app = await session.get(Application, app_id)
        if not app:
            await callback.answer("Заявка не найдена.")
            return

        app.status = "approved"
        await session.commit()

        conf = await session.get(Conference, app.conference_id)
        participant = await session.get(User, app.user_id)

        await callback.bot.send_message(
            participant.telegram_id,
            f"🎉 <b>Ваша заявка на {conf.name} одобрена!</b>\n\n"
            "Нажмите кнопку ниже для подтверждения участия.",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Подтвердить участие", callback_data=f"confirm_part_{app.id}")]
            ])
        )

        await callback.answer("Заявка одобрена")

        user_id = callback.from_user.id
        state = pagination.get(user_id, {"mode": "current", "index": 0})
        apps = await get_applications(user_id, state["mode"])
        if apps and state["index"] < len(apps):
            await show_application(callback, apps, state["index"], state["mode"])

# Отклонение заявки
@router.callback_query(F.data.startswith("reject_"))
async def start_reject(callback: types.CallbackQuery, state: FSMContext):
    if not await is_active_organizer(callback.from_user.id):
        await callback.answer("Доступ запрещён: вы заблокированы.", show_alert=True)
        return

    app_id = int(callback.data.split("_")[1])
    await state.update_data(app_id=app_id)
    await state.set_state(RejectReason.waiting)
    await callback.message.answer("Введите причину отклонения:", reply_markup=get_cancel_keyboard())
    await callback.answer()

@router.message(F.text, RejectReason.waiting)
async def save_reject_reason(message: types.Message, state: FSMContext):
    if not await is_active_organizer(message.from_user.id):
        await message.answer("Доступ запрещён: вы заблокированы.")
        await state.clear()
        return

    data = await state.get_data()
    app_id = data["app_id"]

    async with AsyncSessionLocal() as session:
        app = await session.get(Application, app_id)
        if app:
            app.status = "rejected"
            app.reject_reason = message.text
            await session.commit()

            conf = await session.get(Conference, app.conference_id)
            participant = await session.get(User, app.user_id)

            await message.bot.send_message(
                participant.telegram_id,
                f"К сожалению, ваша заявка на {conf.name} отклонена.\n\nПричина: {message.text}"
            )

    await message.answer("Заявка отклонена, причина сохранена.", reply_markup=get_main_menu_keyboard("Организатор"))
    await state.clear()

# Подтверждение участия
@router.callback_query(F.data.startswith("confirm_part_"))
async def confirm_participation(callback: types.CallbackQuery):
    app_id = int(callback.data.split("_")[-1])
    async with AsyncSessionLocal() as session:
        app = await session.get(Application, app_id)
        if not app:
            await callback.answer("Заявка не найдена.")
            return

        conf = await session.get(Conference, app.conference_id)
        participant = await session.get(User, app.user_id)
        organizer = await session.get(User, conf.organizer_id)

        participant_name = participant.full_name or f"ID {participant.telegram_id}"

        if conf.fee > 0:
            app.status = "payment_pending"
            await session.commit()

            text = "💳 Конференция платная.\n\nПоздравляем, вы прошли отбор! Подтвердите своё участие, оплатив оргвзнос по QR-коду ниже и отправив скриншот чека боту."
            if conf.qr_code_path and os.path.exists(conf.qr_code_path):
                photo = FSInputFile(conf.qr_code_path)
                await callback.bot.send_photo(participant.telegram_id, photo, caption=text)
            else:
                await callback.bot.send_message(participant.telegram_id, text + "\n\n(QR-код не загружен)")

            await callback.bot.send_message(participant.telegram_id, "Отправьте скриншот оплаты:")
        else:
            app.status = "confirmed"
            await session.commit()

            await callback.bot.send_message(
                participant.telegram_id,
                "✅ Вы подтвердили участие!\nОжидайте ссылку на чат от организатора.",
                reply_markup=get_main_menu_keyboard("Участник")
            )

            organizer_text = (
                f"Участник {participant_name} подтвердил участие (бесплатная конференция).\n"
                f"ID заявки {app.id}\n\n"
                f"Отправьте ему ссылку на чат по комитету командой /verify {app.id} [ссылка]"
            )
            await callback.bot.send_message(organizer.telegram_id, organizer_text)

    await callback.answer("Участие подтверждено")

# Приём скриншота оплаты
@router.message(F.photo)
async def receive_payment_screenshot(message: types.Message):
    async with AsyncSessionLocal() as session:
        user_apps = await session.execute(
            select(Application)
            .join(User)
            .where(User.telegram_id == message.from_user.id)
            .where(Application.status == "payment_pending")
        )
        apps = user_apps.scalars().all()

        if not apps:
            return  # Игнорируем, если не в ожидании оплаты

        app = apps[0]
        conf = await session.get(Conference, app.conference_id)
        organizer = await session.get(User, conf.organizer_id)
        participant = await session.get(User, app.user_id)

        participant_name = participant.full_name or f"ID {participant.telegram_id}"

        file_info = await message.bot.get_file(message.photo[-1].file_id)
        file_path = f"{PAYMENTS_DIR}/payment_{app.id}_{message.message_id}.jpg"
        await message.bot.download_file(file_info.file_path, file_path)

        app.payment_screenshot = file_path
        app.status = "payment_sent"
        await session.commit()

        caption = (
            f"Участник {participant_name} прислал скриншот оплаты.\n"
            f"ID заявки {app.id}\n\n"
            f"Проверьте и, если всё верно, подтвердите командой /verify {app.id} [ссылка_на_чат]"
        )
        await message.bot.send_photo(organizer.telegram_id, message.photo[-1].file_id, caption=caption)

    await message.answer("Скриншот отправлен организатору. Ожидайте подтверждения.")

# /verify
@router.message(Command("verify"))
async def verify_payment(message: types.Message):
    if not await is_active_organizer(message.from_user.id):
        await message.answer("Доступ запрещён: вы заблокированы или не являетесь Организатором.")
        return

    try:
        _, app_id_str, *link_parts = message.text.split(maxsplit=2)
        app_id = int(app_id_str)
        link = " ".join(link_parts).strip()
        if not link:
            raise ValueError
    except:
        await message.answer("Использование: /verify ID_заявки ссылка_на_чат")
        return

    async with AsyncSessionLocal() as session:
        app = await session.get(Application, app_id)
        if not app:
            await message.answer("Заявка не найдена.")
            return

        participant = await session.get(User, app.user_id)

        app.status = "link_sent"
        await session.commit()

        await message.bot.send_message(
            participant.telegram_id,
            f"✅ Участие подтверждено!\n\nСсылка на чат комитета:\n{link}"
        )

    await message.answer("Ссылка отправлена участнику.")

# Редактирование конференции — с новой валидацией даты
@router.callback_query(F.data.startswith("edit_conf_"))
async def start_edit(callback: types.CallbackQuery, state: FSMContext):
    if not await is_active_organizer(callback.from_user.id):
        await callback.answer("Доступ запрещён: вы заблокированы.", show_alert=True)
        return

    conf_id = int(callback.data.split("_")[-1])
    await state.update_data(conf_id=conf_id)

    async with AsyncSessionLocal() as session:
        conf = await session.get(Conference, conf_id, options=[joinedload(Conference.organizer)])
        if not conf or conf.organizer.telegram_id != callback.from_user.id:
            await callback.answer("Доступ запрещён.", show_alert=True)
            return

        await state.update_data(
            original_name=conf.name,
            name=conf.name,
            description=conf.description or "",
            city=conf.city or "",
            date=conf.date,
            fee=conf.fee,
            qr_code_path=conf.qr_code_path,
            poster_path=conf.poster_path
        )

    await state.set_state(EditConference.name)
    await callback.message.edit_text(
        f"Редактирование конференции <b>{conf.name}</b>\n\n"
        "Изменения будут отправлены на проверку Администратору.\n\n"
        "1. Название:",
        reply_markup=get_cancel_keyboard()
    )
    await callback.answer()

# НОВАЯ ВАЛИДАЦИЯ ДАТЫ ПРИ РЕДАКТИРОВАНИИ
def validate_conference_date_edit(date_str: str) -> str | None:
    today = datetime.now().date()
    try:
        conf_date = datetime.strptime(date_str.strip(), "%Y-%m-%d").date()
    except ValueError:
        return "Неверный формат даты. Используйте строго ГГГГ-ММ-ДД."

    min_date = today + timedelta(days=1)  # Завтра
    max_date = today + timedelta(days=5 * 365 + 1)  # ~5 лет

    if conf_date < min_date:
        return f"Дата проведения не может быть раньше завтрашнего дня ({min_date.strftime('%d.%m.%Y')})."
    if conf_date > max_date:
        return "Дата проведения не может быть позже, чем через 5 лет."

    return None

@router.message(EditConference.name)
async def edit_name(message: types.Message, state: FSMContext):
    if not await is_active_organizer(message.from_user.id):
        await message.answer("Доступ запрещён: вы заблокированы.")
        await state.clear()
        return

    await state.update_data(name=message.text.strip())
    await state.set_state(EditConference.description)
    await message.answer("2. Описание:", reply_markup=get_cancel_keyboard())

@router.message(EditConference.description)
async def edit_description(message: types.Message, state: FSMContext):
    if not await is_active_organizer(message.from_user.id):
        await message.answer("Доступ запрещён: вы заблокированы.")
        await state.clear()
        return

    await state.update_data(description=message.text.strip())
    await state.set_state(EditConference.city)
    await message.answer("3. Город (или 'Онлайн'):", reply_markup=get_cancel_keyboard())

@router.message(EditConference.city)
async def edit_city(message: types.Message, state: FSMContext):
    if not await is_active_organizer(message.from_user.id):
        await message.answer("Доступ запрещён: вы заблокированы.")
        await state.clear()
        return

    await state.update_data(city=message.text.strip())
    await state.set_state(EditConference.date)
    await message.answer("4. Дата проведения (ГГГГ-ММ-ДД):", reply_markup=get_cancel_keyboard())

@router.message(EditConference.date)
async def edit_date(message: types.Message, state: FSMContext):
    if not await is_active_organizer(message.from_user.id):
        await message.answer("Доступ запрещён: вы заблокированы.")
        await state.clear()
        return

    date_str = message.text.strip()

    error = validate_conference_date_edit(date_str)
    if error:
        await message.answer(f"Ошибка: {error}\nВведите дату заново (ГГГГ-ММ-ДД):")
        return

    await state.update_data(date=date_str)
    await state.set_state(EditConference.fee)
    await message.answer("5. Оргвзнос (0 — бесплатно):", reply_markup=get_cancel_keyboard())

@router.message(EditConference.fee)
async def edit_fee(message: types.Message, state: FSMContext):
    if not await is_active_organizer(message.from_user.id):
        await message.answer("Доступ запрещён: вы заблокированы.")
        await state.clear()
        return

    text = message.text.strip().replace(',', '.')
    if not text.replace('.', '', 1).replace('-', '', 1).isdigit():
        await message.answer("Введите корректное число.")
        return
    await state.update_data(fee=float(text))
    await state.set_state(EditConference.qr_code)
    await message.answer("6. QR-код (отправьте новое фото или напишите 'нет'):", reply_markup=get_cancel_keyboard())

@router.message(EditConference.qr_code, F.photo)
async def edit_qr_photo(message: types.Message, state: FSMContext):
    if not await is_active_organizer(message.from_user.id):
        await message.answer("Доступ запрещён: вы заблокированы.")
        await state.clear()
        return

    file_info = await message.bot.get_file(message.photo[-1].file_id)
    qr_path = f"qr_codes/edit_qr_{message.from_user.id}_{message.message_id}.jpg"
    await message.bot.download_file(file_info.file_path, qr_path)
    await state.update_data(qr_code_path=qr_path)
    await state.set_state(EditConference.poster)
    await message.answer("7. Постер конференции (отправьте новое фото или напишите 'нет'):", reply_markup=get_cancel_keyboard())

@router.message(EditConference.qr_code, F.text)
async def edit_qr_skip(message: types.Message, state: FSMContext):
    if not await is_active_organizer(message.from_user.id):
        await message.answer("Доступ запрещён: вы заблокированы.")
        await state.clear()
        return

    if message.text.lower().strip() == "нет":
        await state.update_data(qr_code_path=None)
    else:
        await message.answer("Отправьте фото QR-кода или напишите 'нет'")
        return

    await state.set_state(EditConference.poster)
    await message.answer("7. Постер конференции (отправьте новое фото или напишите 'нет'):", reply_markup=get_cancel_keyboard())

@router.message(EditConference.poster, F.photo)
async def edit_poster_photo(message: types.Message, state: FSMContext):
    if not await is_active_organizer(message.from_user.id):
        await message.answer("Доступ запрещён: вы заблокированы.")
        await state.clear()
        return

    file_info = await message.bot.get_file(message.photo[-1].file_id)
    poster_path = f"posters/edit_poster_{message.from_user.id}_{message.message_id}.jpg"
    await message.bot.download_file(file_info.file_path, poster_path)
    await state.update_data(poster_path=poster_path)
    await finish_edit_conference(message, state)

@router.message(EditConference.poster, F.text)
async def edit_poster_skip(message: types.Message, state: FSMContext):
    if not await is_active_organizer(message.from_user.id):
        await message.answer("Доступ запрещён: вы заблокированы.")
        await state.clear()
        return

    if message.text.lower().strip() == "нет":
        await state.update_data(poster_path=None)
        await finish_edit_conference(message, state)
    else:
        await message.answer("Отправьте фото постера или напишите 'нет'")

async def finish_edit_conference(message: types.Message, state: FSMContext):
    if not await is_active_organizer(message.from_user.id):
        await message.answer("Доступ запрещён: вы заблокированы.")
        await state.clear()
        return

    data = await state.get_data()
    async with AsyncSessionLocal() as session:
        conf_id = data["conf_id"]
        organizer_result = await session.execute(select(User.id).where(User.telegram_id == message.from_user.id))
        organizer_id = organizer_result.scalar_one()

        edit_req = ConferenceEditRequest(
            conference_id=conf_id,
            organizer_id=organizer_id,
            data={
                "name": data["name"],
                "description": data["description"],
                "city": data["city"],
                "date": data["date"],
                "fee": data["fee"],
                "qr_code_path": data.get("qr_code_path"),
                "poster_path": data.get("poster_path")
            },
            status="pending"
        )
        session.add(edit_req)
        await session.commit()

    await message.answer(
        "✅ <b>Заявка на редактирование конференции отправлена!</b>\n\n"
        "Ожидайте одобрения Администратора.",
        reply_markup=get_main_menu_keyboard("Организатор")
    )
    await state.clear()

# Удаление конференции
@router.callback_query(F.data.startswith("delete_conf_"))
async def confirm_delete(callback: types.CallbackQuery):
    if not await is_active_organizer(callback.from_user.id):
        await callback.answer("Доступ запрещён: вы заблокированы.", show_alert=True)
        return

    conf_id = int(callback.data.split("_")[-1])
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="Да, удалить", callback_data=f"confirm_delete_{conf_id}"),
        InlineKeyboardButton(text="Отмена", callback_data="back_to_menu")
    )
    await callback.message.edit_text(
        "Вы уверены, что хотите удалить конференцию и все связанные заявки?\nЭто действие необратимо.",
        reply_markup=builder.as_markup()
    )
    await callback.answer()

@router.callback_query(F.data.startswith("confirm_delete_"))
async def do_delete(callback: types.CallbackQuery):
    if not await is_active_organizer(callback.from_user.id):
        await callback.answer("Доступ запрещён: вы заблокированы.", show_alert=True)
        return

    conf_id = int(callback.data.split("_")[-1])
    user_id = callback.from_user.id

    async with AsyncSessionLocal() as session:
        conf = await session.get(Conference, conf_id)
        if not conf:
            await callback.answer("Конференция не найдена.")
            return

        organizer = await session.get(User, conf.organizer_id)

        notify_text = f"Организатор {callback.from_user.full_name or user_id} удалил конференцию: {conf.name}"
        for admin_id in CHIEF_ADMIN_IDS:
            try:
                await callback.bot.send_message(admin_id, notify_text)
            except:
                pass

        await session.execute(delete(Application).where(Application.conference_id == conf_id))
        await session.execute(delete(ConferenceEditRequest).where(ConferenceEditRequest.conference_id == conf_id))

        await session.delete(conf)
        await session.commit()

        remaining_confs = await session.scalar(
            select(func.count(Conference.id)).where(Conference.organizer_id == organizer.id)
        )
        if remaining_confs == 0:
            organizer.role = Role.PARTICIPANT.value
            await session.commit()
            await callback.bot.send_message(
                organizer.telegram_id,
                "У вас больше нет конференций.\n"
                "Ваша роль изменена на <b>Участник</b>.\n"
                "Перезапустите бота командой /main_menu, чтобы увидеть актуальное меню."
            )

    if user_id in last_my_conferences_msg:
        try:
            await callback.bot.delete_message(callback.message.chat.id, last_my_conferences_msg[user_id])
            del last_my_conferences_msg[user_id]
        except:
            pass

    await callback.message.edit_text(f"Конференция <b>{conf.name}</b> успешно удалена.")

    if remaining_confs > 0:
        await my_conferences(callback.message)

    await callback.answer("Удалено!")

# Рассылка
@router.callback_query(F.data.startswith("broadcast_"))
async def start_broadcast(callback: types.CallbackQuery, state: FSMContext):
    if not await is_active_organizer(callback.from_user.id):
        await callback.answer("Доступ запрещён: вы заблокированы.", show_alert=True)
        return

    conf_id = int(callback.data.split("_")[-1])
    await state.update_data(conference_id=conf_id)
    await state.set_state(Broadcast.message_text)

    async with AsyncSessionLocal() as session:
        conf = await session.get(Conference, conf_id, options=[joinedload(Conference.organizer)])
        if not conf or conf.organizer.telegram_id != callback.from_user.id:
            await callback.answer("Доступ запрещён.", show_alert=True)
            return

        await callback.message.edit_text(
            f"Рассылка участникам конференции <b>{conf.name}</b>\n\nВведите текст сообщения:",
            reply_markup=get_cancel_keyboard()
        )
    await callback.answer()

@router.message(Broadcast.message_text)
async def send_broadcast(message: types.Message, state: FSMContext):
    if not await is_active_organizer(message.from_user.id):
        await message.answer("Доступ запрещён: вы заблокированы.")
        await state.clear()
        return

    data = await state.get_data()
    conf_id = data["conference_id"]
    text = message.text

    async with AsyncSessionLocal() as session:
        conf = await session.get(Conference, conf_id)
        if not conf:
            await message.answer("Конференция не найдена.")
            await state.clear()
            return

        result = await session.execute(
            select(Application).options(joinedload(Application.user)).where(
                Application.conference_id == conf_id,
                Application.status.in_(["approved", "payment_pending", "payment_sent", "confirmed", "link_sent"])
            )
        )
        applications = result.scalars().all()

        sent_count = 0
        for app in applications:
            try:
                await message.bot.send_message(
                    app.user.telegram_id,
                    f"📢 <b>Сообщение от организатора конференции \"{conf.name}\"</b>\n\n{text}"
                )
                sent_count += 1
            except:
                pass

    await message.answer(
        f"✅ Рассылка завершена!\nСообщение отправлено {sent_count} участникам.",
        reply_markup=get_main_menu_keyboard("Организатор")
    )
    await state.clear()

# Возврат в главное меню
@router.callback_query(F.data == "back_to_menu")
async def back_to_menu(callback: types.CallbackQuery):
    user_id = callback.from_user.id

    if user_id in last_my_conferences_msg:
        try:
            await callback.bot.delete_message(callback.message.chat.id, last_my_conferences_msg[user_id])
            del last_my_conferences_msg[user_id]
        except:
            pass

    await callback.message.answer("🔙 Главное меню", reply_markup=get_main_menu_keyboard("Организатор"))
    try:
        await callback.message.delete()
    except:
        pass
    await callback.answer()