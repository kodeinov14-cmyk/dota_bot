import asyncio
import os
import threading
from dotenv import load_dotenv
from flask import Flask
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from logic import get_account_id, get_player_info

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
STEAM_API_KEY = os.getenv("STEAM_API_KEY")

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
            await message.answer("Не удалось получить ID. Проверь ссылку.")
            return
            
        player_data = get_player_info(account_id)
        
        if not player_data:
            await message.answer("Не удалось найти игрока в базе OpenDota. Возможно, у него скрытая история матчей или API недоступно.")
            return
            
        name = player_data["profile"].get("personaname", "Неизвестный")
        rank = player_data.get("rank_tier")
        rank_str = f"Ранг: {rank}" if rank else "Ранг: Скрыт или нет данных"
        
        result_text = f"Игрок: {name}\n{rank_str}"
        await message.answer(result_text)

    except Exception as e:
        print(f"ОШИБКА: {e}")
        await message.answer("Произошла ошибка при запросе к API.")

async def main():
    print("Бот запущен...")
    await dp.start_polling(bot)

# --- Веб-сервер для Render ---
app = Flask(__name__)

@app.route('/')
def index():
    return "Bot is running"

@app.route('/health')
def health():
    return "OK"

def run_bot():
    asyncio.run(main())

if __name__ == "__main__":
    # Запускаем бота в фоне
    t = threading.Thread(target=run_bot)
    t.start()
    
    # Запускаем веб-сервер, чтобы Render видел активный порт
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)