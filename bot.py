import asyncio
import os
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from logic import get_account_id, get_player_info

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer("Привет! Кидай ссылку на профиль Steam, и я покажу стату.")

@dp.message()
async def handle_url(message: types.Message):
    text = message.text
    
    if "steamcommunity.com" not in text:
        await message.answer("Это не похоже на ссылку Steam. Попробуй еще раз.")
        return

    await message.answer("Загружаю данные...")
    
    try:
        account_id = get_account_id(text)
        
        if not account_id:
            await message.answer("Не удалось получить ID. Проверь ссылку или настройки приватности профиля.")
            return
            
        player_data = get_player_info(account_id)
        
        if not player_data:
            await message.answer("Не удалось найти игрока в базе OpenDota. Возможно, у него скрытая история матчей.")
            return
            
        name = player_data["profile"].get("personaname", "Неизвестный")
        rank = player_data.get("rank_tier")
        
        rank_str = f"Ранг: {rank}" if rank else "Ранг: Скрыт или нет данных"
        
        result_text = f"Игрок: {name}\n{rank_str}"
        
        await message.answer(result_text)

    except Exception as e:
        print(f"ОШИБКА: {e}")
        await message.answer("Произошла ошибка при запросе к API. Проверь консоль терминала.")

async def main():
    print("Бот запущен...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())