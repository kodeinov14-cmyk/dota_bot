import asyncio
import os
import sys
import threading
from dotenv import load_dotenv
from flask import Flask
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from logic import get_account_id, get_player_info

sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)

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
            await message.answer("Не удалось получить ID. Проверь ссылку.")
            return
            
        player_data = get_player_info(account_id)
        
        if not player_data:
            await message.answer("Не удалось найти игрока через STRATZ API. Возможно, история матчей скрыта.")
            return
            
        name = player_data["steamAccount"].get("name", "Неизвестный")
        match_count = player_data.get("matchCount", 0)
        win_count = player_data.get("winCount", 0)
        
        if match_count > 0:
            winrate = round((win_count / match_count) * 100, 2)
        else:
            winrate = 0
        
        result_text = f"Игрок: {name}\nМатчей: {match_count}\nВинрейт: {winrate}%"
        await message.answer(result_text)

    except Exception as e:
        print(f"ОШИБКА: {e}", flush=True)
        await message.answer(f"Произошла ошибка: {e}")

app = Flask(__name__)

@app.route('/')
def index():
    return "Bot is running"

@app.route('/health')
def health():
    return "OK"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

if __name__ == "__main__":
    t = threading.Thread(target=run_flask)
    t.daemon = True
    t.start()
    
    print("Бот запущен...", flush=True)
    asyncio.run(dp.start_polling(bot))