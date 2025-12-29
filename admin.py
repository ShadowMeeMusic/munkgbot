from aiogram import Router, types, F
from aiogram.filters import Command
from sqlalchemy import select, func
from aiogram.types import InlineKeyboardButton, BufferedInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder
import pandas as pd
import os
from datetime import datetime

from database import (
    AsyncSessionLocal,
    ConferenceCreationRequest,
    ConferenceEditRequest,
    Conference,
    Application,
    User,
    Role,
    get_or_create_user,
    DeletedConference
)
from keyboards import get_main_menu_keyboard
from config import CHIEF_ADMIN_IDS, TECH_SPECIALIST_ID

router = Router()

# Проверки ролей
async def is_admin_or_chief(user_id: int) -> bool:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.telegram_id == user_id))
        user = result.scalar_one_or_none()
        return user.role in [Role.ADMIN.value, Role.CHIEF_ADMIN.value] if user else False

async def is_chief_tech(user_id: int) -> bool:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.telegram_id == user_id))
        user = result.scalar_one_or_none()
        return user.role == Role.CHIEF_TECH.value if user else False

async def can_delete_conference(user_id: int) -> bool:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.telegram_id == user_id))
        user = result.scalar_one_or_none()
        return user.role in [Role.ADMIN.value, Role.CHIEF_ADMIN.value, Role.CHIEF_TECH.value] if user else False

# Универсальная функция обновления списка заявок
async def update_requests_message(event: types.Message | types.CallbackQuery):
    async with AsyncSessionLocal() as session:
        create_requests = (await session.execute(
            select(ConferenceCreationRequest).where(ConferenceCreationRequest.status == "pending")
        )).scalars().all()

        edit_requests = (await session.execute(
            select(ConferenceEditRequest).where(ConferenceEditRequest.status == "pending")
        )).scalars().all()

        appeal_requests = (await session.execute(
            select(ConferenceCreationRequest).where(
                ConferenceCreationRequest.status == "rejected",
                ConferenceCreationRequest.appeal == True
            )
        )).scalars().all()

        builder = InlineKeyboardBuilder()
        text = "<b>Админ-панель: Заявки</b>\n\n"

        if create_requests:
            text += "<b>Заявки на создание конференций:</b>\n\n"
            for req in create_requests:
                user = await session.get(User, req.user_id)
                data = req.data

                text += f"ID: <code>{req.id}</code> (создание)\n"
                text += f"От: {user.full_name or user.telegram_id}\n"
                text += f"Название: {data.get('name', '—')}\n"
                text += f"Город: {data.get('city', '—')}\n"
                text += f"Даты: {data.get('date_start', '—')} — {data.get('date_end', '—')}\n"
                text += f"Оргвзнос: {data.get('fee', 0)} руб.\n\n"

                builder.row(
                    InlineKeyboardButton(text="Одобрить", callback_data=f"conf_create_approve_{req.id}"),
                    InlineKeyboardButton(text="Отклонить", callback_data=f"conf_create_reject_{req.id}")
                )

        if edit_requests:
            text += "<b>Заявки на редактирование:</b>\n\n"
            for req in edit_requests:
                conf = await session.get(Conference, req.conference_id)
                organizer = await session.get(User, req.organizer_id)
                data = req.data

                text += f"ID: <code>{req.id}</code> (редактирование)\n"
                text += f"Конференция: <b>{conf.name}</b>\n"
                text += f"От: {organizer.full_name or organizer.telegram_id}\n"
                text += f"Новые данные: {data.get('name')} ({data.get('city')})\n\n"

                builder.row(
                    InlineKeyboardButton(text="Одобрить", callback_data=f"conf_edit_approve_{req.id}"),
                    InlineKeyboardButton(text="Отклонить", callback_data=f"conf_edit_reject_{req.id}")
                )

        if appeal_requests:
            text += "<b>Апелляции к Глав Админу:</b>\n\n"
            for req in appeal_requests:
                user = await session.get(User, req.user_id)
                data = req.data

                text += f"ID: <code>{req.id}</code> (апелляция)\n"
                text += f"От: {user.full_name or user.telegram_id}\n"
                text += f"Название: {data.get('name')}\n\n"

                builder.row(
                    InlineKeyboardButton(text="Одобрить (апелляция)", callback_data=f"conf_appeal_approve_{req.id}"),
                    InlineKeyboardButton(text="Отклонить (апелляция)", callback_data=f"conf_appeal_reject_{req.id}")
                )

        if not create_requests and not edit_requests and not appeal_requests:
            text += "Нет активных заявок."

        if isinstance(event, types.Message):
            await event.answer(text, reply_markup=builder.as_markup())
        else:
            try:
                await event.message.edit_text(text, reply_markup=builder.as_markup())
            except:
                await event.message.edit_text(text + "\n\n(Список обновлён)", reply_markup=builder.as_markup())

# Команда просмотра заявок
@router.message(Command("admin_requests"))
async def admin_conference_requests(message: types.Message):
    if not await is_admin_or_chief(message.from_user.id):
        await message.answer("Доступ запрещён.")
        return

    await update_requests_message(message)

# Обработка создания конференции
@router.callback_query(F.data.startswith("conf_create_approve_") | F.data.startswith("conf_create_reject_"))
async def process_create_request(callback: types.CallbackQuery):
    action = "approve" if "approve" in callback.data else "reject"
    req_id = int(callback.data.split("_")[-1])

    async with AsyncSessionLocal() as session:
        req = await session.get(ConferenceCreationRequest, req_id)
        if not req:
            await callback.answer("Заявка не найдена.")
            return

        user = await session.get(User, req.user_id)
        req_data = req.data

        if action == "approve":
            req.status = "approved"
            user.role = Role.ORGANIZER.value

            conference = Conference(
                name=req_data["name"],
                description=req_data.get("description"),
                city=req_data.get("city"),
                date_start=req_data.get("date_start"),
                date_end=req_data.get("date_end"),
                fee=float(req_data.get("fee", 0)),
                qr_code_path=req_data.get("qr_code_path"),
                organizer_id=user.id,
                is_active=True
            )
            session.add(conference)
            await session.commit()

            await callback.bot.send_message(
                user.telegram_id,
                f"🎉 Ваша заявка на создание конференции <b>{req_data['name']}</b> одобрена!\n\n"
                "Теперь вы — Организатор.\n"
                "Перезапустите бота командой /main_menu, чтобы увидеть новые функции."
            )
        else:
            req.status = "rejected"
            await session.commit()

            keyboard = InlineKeyboardBuilder()
            keyboard.row(InlineKeyboardButton(text="Подать апелляцию Глав Админу", callback_data=f"appeal_{req.id}"))

            await callback.bot.send_message(
                user.telegram_id,
                f"❌ Ваша заявка на создание конференции <b>{req_data['name']}</b> отклонена.",
                reply_markup=keyboard.as_markup()
            )

        await callback.answer(f"Заявка {'одобрена' if action == 'approve' else 'отклонена'}")

    await update_requests_message(callback)

# Обработка редактирования конференции — добавлен вызов обновления списка
@router.callback_query(F.data.startswith("conf_edit_approve_") | F.data.startswith("conf_edit_reject_"))
async def process_edit_request(callback: types.CallbackQuery):
    action = "approve" if "approve" in callback.data else "reject"
    req_id = int(callback.data.split("_")[-1])

    async with AsyncSessionLocal() as session:
        req = await session.get(ConferenceEditRequest, req_id)
        if not req:
            await callback.answer("Заявка на редактирование не найдена.")
            return

        conf = await session.get(Conference, req.conference_id)
        organizer = await session.get(User, req.organizer_id)
        edit_data = req.data

        if action == "approve":
            # Применяем изменения
            conf.name = edit_data.get("name", conf.name)
            conf.description = edit_data.get("description", conf.description)
            conf.city = edit_data.get("city", conf.city)
            conf.date_start = edit_data.get("date_start", conf.date_start)
            conf.date_end = edit_data.get("date_end", conf.date_end)
            conf.fee = edit_data.get("fee", conf.fee)
            if edit_data.get("qr_code_path"):
                conf.qr_code_path = edit_data["qr_code_path"]

            req.status = "approved"
            await session.commit()

            await callback.bot.send_message(
                organizer.telegram_id,
                f"✅ Ваши изменения в конференции <b>{conf.name}</b> одобрены администратором!"
            )
        else:
            req.status = "rejected"
            await session.commit()

            await callback.bot.send_message(
                organizer.telegram_id,
                f"❌ Ваши изменения в конференции <b>{conf.name}</b> отклонены администратором."
            )

        await callback.answer(f"Редактирование {'одобрено' if action == 'approve' else 'отклонено'}")

    # Обновляем список заявок — теперь кнопки исчезнут!
    await update_requests_message(callback)

# Апелляция от пользователя
@router.callback_query(F.data.startswith("appeal_"))
async def send_appeal(callback: types.CallbackQuery):
    req_id = int(callback.data.split("_")[1])

    async with AsyncSessionLocal() as session:
        req = await session.get(ConferenceCreationRequest, req_id)
        if not req or req.status != "rejected":
            await callback.answer("Заявка не найдена или уже обработана.")
            return

        req.appeal = True
        await session.commit()

    for admin_id in CHIEF_ADMIN_IDS:
        try:
            await callback.bot.send_message(
                admin_id,
                f"🆕 Новая апелляция!\n\nЗаявка ID: <code>{req_id}</code>\nПроверьте в /admin_requests"
            )
        except:
            pass

    await callback.message.edit_text("Ваша апелляция отправлена Глав Админу.")
    await callback.answer()

# Обработка апелляции
@router.callback_query(F.data.startswith("conf_appeal_approve_") | F.data.startswith("conf_appeal_reject_"))
async def process_appeal(callback: types.CallbackQuery):
    if callback.from_user.id not in CHIEF_ADMIN_IDS:
        await callback.answer("Доступ только Глав Админу.")
        return

    action = "approve" if "approve" in callback.data else "reject"
    req_id = int(callback.data.split("_")[-1])

    async with AsyncSessionLocal() as session:
        req = await session.get(ConferenceCreationRequest, req_id)
        if not req:
            await callback.answer("Заявка не найдена.")
            return

        user = await session.get(User, req.user_id)
        req_data = req.data

        if action == "approve":
            req.status = "approved"
            user.role = Role.ORGANIZER.value

            conference = Conference(
                name=req_data["name"],
                description=req_data.get("description"),
                city=req_data.get("city"),
                date_start=req_data.get("date_start"),
                date_end=req_data.get("date_end"),
                fee=float(req_data.get("fee", 0)),
                qr_code_path=req_data.get("qr_code_path"),
                organizer_id=user.id,
                is_active=True
            )
            session.add(conference)
            await session.commit()

            await callback.bot.send_message(user.telegram_id, "✅ Ваша апелляция одобрена! Вы стали Организатором.")
        else:
            req.appeal = False
            await session.commit()

            await callback.bot.send_message(user.telegram_id, "❌ Ваша апелляция отклонена.")

        await callback.answer("Апелляция обработана")

    await update_requests_message(callback)

# Удаление конференции админами
@router.message(Command("delete_conf"))
async def delete_conference_command(message: types.Message):
    if not await can_delete_conference(message.from_user.id):
        await message.answer("Доступ запрещён.")
        return

    try:
        _, conf_id_str, *reason_parts = message.text.split(maxsplit=2)
        conf_id = int(conf_id_str)
        reason = " ".join(reason_parts).strip()
        if not reason:
            await message.answer("Укажите причину: /delete_conf ID_конференции причина")
            return
    except:
        await message.answer("Формат: /delete_conf ID_конференции причина")
        return

    async with AsyncSessionLocal() as session:
        conf = await session.get(Conference, conf_id)
        if not conf:
            await message.answer("Конференция не найдена.")
            return

        organizer = await session.get(User, conf.organizer_id)

        deleted_log = DeletedConference(
            conference_name=conf.name,
            organizer_telegram_id=organizer.telegram_id,
            deleted_by_telegram_id=message.from_user.id,
            reason=reason,
            deleted_at=datetime.now().strftime("%Y-%m-%d %H:%M")
        )
        session.add(deleted_log)

        await session.delete(conf)
        await session.commit()

    await message.answer(f"Конференция <b>{conf.name}</b> удалена по причине: {reason}")

    try:
        await message.bot.send_message(
            organizer.telegram_id,
            f"❌ Ваша конференция <b>{conf.name}</b> удалена администратором.\nПричина: {reason}"
        )
    except:
        pass

# Экспорт данных бота — два файла
@router.message(F.text == "Экспорт данных бота")
async def export_bot_data(message: types.Message):
    if message.from_user.id not in CHIEF_ADMIN_IDS and message.from_user.id != TECH_SPECIALIST_ID:
        await message.answer("Доступ запрещён.")
        return

    async with AsyncSessionLocal() as session:
        users = (await session.execute(select(User))).scalars().all()
        conferences = (await session.execute(select(Conference))).scalars().all()
        deleted = (await session.execute(select(DeletedConference))).scalars().all()

        users_data = []
        for user in users:
            users_data.append({
                "Telegram ID": user.telegram_id,
                "ФИО": user.full_name or "—",
                "Роль": user.role,
                "Забанен": "Да" if user.is_banned else "Нет",
                "Причина бана": user.ban_reason or "—"
            })

        df_users = pd.DataFrame(users_data)
        users_filename = "export_users_bans.xlsx"
        df_users.to_excel(users_filename, index=False)

        conf_data = []
        for conf in conferences:
            organizer = await session.get(User, conf.organizer_id)
            organizer_name = organizer.full_name or organizer.telegram_id if organizer else "—"
            conf_data.append({
                "ID": conf.id,
                "Название": conf.name,
                "Организатор": organizer_name,
                "Город": conf.city or "—",
                "Даты": f"{conf.date_start or '—'} — {conf.date_end or '—'}",
                "Оргвзнос": conf.fee,
                "Активна": "Да" if conf.is_active else "Нет"
            })

        df_confs = pd.DataFrame(conf_data)

        deleted_data = []
        for d in deleted:
            deleted_data.append({
                "Название конференции": d.conference_name,
                "Организатор ID": d.organizer_telegram_id,
                "Удалил (ID)": d.deleted_by_telegram_id,
                "Причина удаления": d.reason,
                "Дата удаления": d.deleted_at
            })

        df_deleted = pd.DataFrame(deleted_data)

        confs_filename = "export_conferences_admin_actions.xlsx"
        with pd.ExcelWriter(confs_filename) as writer:
            df_confs.to_excel(writer, sheet_name="Активные_конференции", index=False)
            df_deleted.to_excel(writer, sheet_name="Удалённые_конференции", index=False)

    with open(users_filename, "rb") as f1:
        file1 = BufferedInputFile(f1.read(), filename=users_filename)
        await message.answer_document(file1, caption="1/2 Экспорт: Пользователи и баны")

    with open(confs_filename, "rb") as f2:
        file2 = BufferedInputFile(f2.read(), filename=confs_filename)
        await message.answer_document(file2, caption="2/2 Экспорт: Конференции и действия админа")

    os.remove(users_filename)
    os.remove(confs_filename)

# Статистика
@router.message(Command("stats"))
async def stats(message: types.Message):
    if not await is_admin_or_chief(message.from_user.id):
        await message.answer("Доступ запрещён.")
        return

    async with AsyncSessionLocal() as session:
        users_count = await session.scalar(select(func.count(User.id)))
        conf_count = await session.scalar(select(func.count(Conference.id)).where(Conference.is_active == True))
        apps_count = await session.scalar(select(func.count(Application.id)))

        text = "<b>Статистика бота:</b>\n\n"
        text += f"Пользователей: {users_count}\n"
        text += f"Активных конференций: {conf_count}\n"
        text += f"Всего заявок на участие: {apps_count}\n"

        db_user = await get_or_create_user(message.from_user.id, message.from_user.full_name)
        await message.answer(text, reply_markup=get_main_menu_keyboard(db_user.role))

# Назначение роли
@router.message(Command("set_role"))
async def set_role(message: types.Message):
    if not await is_chief_tech(message.from_user.id):
        await message.answer("Доступ запрещён. Только для Главного Тех Специалиста.")
        return

    await message.answer(
        "Формат: /set_role @username роль\n"
        "Роли: Участник, Организатор, Админ, Главный Админ, Глав Тех Специалист"
    )

    try:
        _, target, role_str = message.text.split(maxsplit=2)
        target = target.lstrip("@")

        async with AsyncSessionLocal() as session:
            if target.isdigit():
                result = await session.execute(select(User).where(User.telegram_id == int(target)))
            else:
                result = await session.execute(select(User).where(User.full_name.ilike(f"%{target}%")))
            target_user = result.scalar_one_or_none()

            if not target_user:
                await message.answer("Пользователь не найден.")
                return

            if role_str not in [r.value for r in Role]:
                await message.answer("Неверная роль.")
                return

            target_user.role = role_str
            await session.commit()

            await message.answer(f"Роль пользователя {target_user.full_name or target_user.telegram_id} изменена на {role_str}")
            try:
                await message.bot.send_message(target_user.telegram_id, f"Ваша роль изменена на: {role_str}")
            except:
                pass
    except:
        await message.answer("Неверный формат команды.")