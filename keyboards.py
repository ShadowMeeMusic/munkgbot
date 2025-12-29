from aiogram.utils.keyboard import ReplyKeyboardBuilder
from aiogram.types import KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

# Главное меню — строго по ролям из PDF + новые кнопки
def get_main_menu_keyboard(role: str):
    builder = ReplyKeyboardBuilder()

    # Общая кнопка для всех ролей (кроме забаненных)
    builder.row(KeyboardButton(text="Обновить систему"))

    if role == "Участник":
        builder.row(
            KeyboardButton(text="Просмотр конференций"),
            KeyboardButton(text="Подать заявку на участие")
        )
        builder.row(
            KeyboardButton(text="Создать конференцию"),
            KeyboardButton(text="Обращение к тех. специалисту")
        )
        builder.row(KeyboardButton(text="Помощь"))

    elif role == "Организатор":
        builder.row(
            KeyboardButton(text="Мои конференции"),
            KeyboardButton(text="Заявки участников")
        )
        builder.row(
            KeyboardButton(text="Архив заявок"),
            KeyboardButton(text="Обращение к тех. специалисту")
        )
        builder.row(KeyboardButton(text="Помощь"))

    elif role == "Глав Тех Специалист":
        builder.row(
            KeyboardButton(text="Очередь обращений участников"),
            KeyboardButton(text="Список забаненных пользователей")
        )
        builder.row(
            KeyboardButton(text="Бан/разбан пользователей"),
            KeyboardButton(text="Назначить роль другим пользователям")
        )
        builder.row(
            KeyboardButton(text="Экспортировать данные бота")
        )

    elif role == "Админ":
        builder.row(
            KeyboardButton(text="Просмотр заявок на конференции"),
            KeyboardButton(text="Статистика")
        )
        builder.row(
            KeyboardButton(text="Бан/разбан пользователей"),  # Добавлено по твоему пункту 5
            KeyboardButton(text="Обращение к тех. специалисту")
        )
        builder.row(KeyboardButton(text="Помощь"))
        # Убрана кнопка "Назначить роль" — только у Глав Тех Специалиста

    elif role == "Главный Админ":
        builder.row(
            KeyboardButton(text="Просмотр заявок на конференции"),
            KeyboardButton(text="Статистика")
        )
        builder.row(
            KeyboardButton(text="Просмотр конференций"),
            KeyboardButton(text="Бан/разбан пользователей")
        )
        builder.row(
            KeyboardButton(text="Приостановка бота"),
            KeyboardButton(text="Экспорт данных бота")
        )
        builder.row(
            KeyboardButton(text="Обращение к тех. специалисту"),
            KeyboardButton(text="Помощь")
        )

    else:
        builder.row(KeyboardButton(text="Помощь"))

    return builder.as_markup(resize_keyboard=True, one_time_keyboard=False)

# Инлайн-клавиатура со списком конференций
def get_conferences_keyboard(conferences):
    builder = InlineKeyboardBuilder()
    for conf in conferences:
        text = f"{conf.name}"
        details = []
        if conf.city:
            details.append(conf.city)
        if conf.date_start:
            details.append(conf.date_start)
        if details:
            text += f" ({', '.join(details)})"
        builder.button(text=text, callback_data=f"select_conf_{conf.id}")
    builder.adjust(1)
    return builder.as_markup()

# Кнопка отмены для форм
def get_cancel_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_form")]
    ])