from aiogram.fsm.state import State, StatesGroup

# Форма регистрации участника (анкета)
class ParticipantRegistration(StatesGroup):
    full_name = State()
    age = State()
    email = State()
    institution = State()
    experience = State()
    committee = State()             # Желаемый комитет

# Форма создания конференции (заявка на модерацию Админу)
class CreateConferenceRequest(StatesGroup):
    name = State()
    description = State()
    city = State()
    date_start = State()
    date_end = State()
    fee = State()
    qr_code = State()               # Фото QR-кода или 'нет'

# Причина отклонения заявки (Организатор)
class RejectReason(StatesGroup):
    waiting = State()

# Редактирование существующей конференции (Организатор)
class EditConference(StatesGroup):
    name = State()
    description = State()
    city = State()
    date_start = State()
    date_end = State()
    fee = State()
    qr_code = State()               # Новое фото QR или 'нет'

# Массовые рассылки участникам конференции (Организатор)
class Broadcast(StatesGroup):
    conference_id = State()
    message_text = State()

# Техподдержка — обращение от пользователя
class SupportAppeal(StatesGroup):
    message = State()

# Техподдержка — ответ от Глав Тех Специалиста
class SupportResponse(StatesGroup):
    request_id = State()
    response_text = State()

# Бан/разбан с причиной
class BanReasonState(StatesGroup):
    target = State()
    action = State()  # "ban" или "unban"
    reason = State()