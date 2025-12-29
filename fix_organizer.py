import asyncio
from database import init_db, AsyncSessionLocal, User, Conference, Role
from sqlalchemy import select, update

async def fix():
    await init_db()
    async with AsyncSessionLocal() as session:
        # Находим тебя по telegram_id (замени на свой реальный ID из лога — 7838905670)
        result = await session.execute(select(User).where(User.telegram_id == 7838905670))
        me = result.scalar_one_or_none()
        if me:
            me.role = Role.ORGANIZER.value
            print("Роль изменена на Организатор")

        # Привязываем все конференции к тебе
        await session.execute(update(Conference).values(organizer_id=me.id))
        await session.commit()
        print("Все конференции привязаны к тебе")

asyncio.run(fix())