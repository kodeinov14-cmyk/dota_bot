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

HERO_NAMES = {
    1: "Anti-Mage", 2: "Axe", 3: "Bane", 4: "Bloodseeker", 5: "Crystal Maiden",
    6: "Drow Ranger", 7: "Earthshaker", 8: "Juggernaut", 9: "Mirana", 10: "Morphling",
    11: "Shadow Fiend", 12: "Phantom Lancer", 13: "Puck", 14: "Pudge", 15: "Razor",
    16: "Sand King", 17: "Storm Spirit", 18: "Sven", 19: "Tiny", 20: "Vengeful Spirit",
    21: "Windranger", 22: "Zeus", 23: "Kunkka", 25: "Lina", 26: "Lion", 27: "Shadow Shaman",
    28: "Slardar", 29: "Tidehunter", 30: "Witch Doctor", 31: "Lich", 32: "Riki",
    33: "Enigma", 34: "Tinker", 35: "Sniper", 36: "Necrophos", 37: "Warlock",
    38: "Beastmaster", 39: "Queen of Pain", 40: "Venomancer", 41: "Faceless Void",
    42: "Wraith King", 43: "Death Prophet", 44: "Phantom Assassin", 45: "Pugna",
    46: "Templar Assassin", 47: "Viper", 48: "Luna", 49: "Dragon Knight", 50: "Dazzle",
    51: "Clockwerk", 52: "Leshrac", 53: "Nature's Prophet", 54: "Lifestealer",
    55: "Dark Seer", 56: "Clinkz", 57: "Omniknight", 58: "Enchantress", 59: "Huskar",
    60: "Night Stalker", 61: "Broodmother", 62: "Bounty Hunter", 63: "Weaver",
    64: "Jakiro", 65: "Batrider", 66: "Chen", 67: "Spectre", 68: "Ancient Apparition",
    69: "Doom", 70: "Ursa", 71: "Spirit Breaker", 72: "Gyrocopter", 73: "Alchemist",
    74: "Invoker", 75: "Silencer", 76: "Outworld Destroyer", 77: "Lycan",
    78: "Brewmaster", 79: "Shadow Demon", 80: "Lone Druid", 81: "Chaos Knight",
    82: "Meepo", 83: "Treant Protector", 84: "Ogre Magi", 85: "Undying",
    86: "Rubick", 87: "Disruptor", 88: "Nyx Assassin", 89: "Naga Siren",
    90: "Keeper of the Light", 91: "Io", 92: "Visage", 93: "Slark", 94: "Medusa",
    95: "Troll Warlord", 96: "Centaur Warrunner", 97: "Magnus", 98: "Timbersaw",
    99: "Bristleback", 100: "Tusk", 101: "Skywrath Mage", 102: "Abaddon",
    103: "Elder Titan", 104: "Legion Commander", 105: "Techies", 106: "Ember Spirit",
    107: "Earth Spirit", 108: "Underlord", 109: "Terrorblade", 110: "Phoenix",
    111: "Oracle", 112: "Winter Wyvern", 113: "Arc Warden", 114: "Monkey King",
    119: "Dark Willow", 120: "Pangolier", 121: "Grimstroke", 123: "Hoodwink",
    126: "Void Spirit", 128: "Snapfire", 129: "Mars", 131: "Ringmaster", 135: "Dawnbreaker",
    136: "Marci", 137: "Primal Beast", 138: "Muerta"
}

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer("Привет! Кидай ссылку на профиль Steam, и я покажу разведку по игроку.")

@dp.message()
async def handle_url(message: types.Message):
    text = message.text
    
    if "steamcommunity.com" not in text:
        await message.answer("Это не похоже на ссылку Steam. Попробуй еще раз.")
        return

    await message.answer("Собираю разведданные...")
    
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
        
        total_winrate = round((win_count / match_count) * 100, 1) if match_count > 0 else 0

        # Блок героев
        heroes_text = ""
        heroes = player_data.get("heroesPerformance", [])
        for idx, h in enumerate(heroes, 1):
            h_id = h.get("heroId")
            h_matches = h.get("matchCount", 0)
            h_wins = h.get("winCount", 0)
            h_name = HERO_NAMES.get(h_id, f"Герой {h_id}")
            h_wr = round((h_wins / h_matches) * 100, 1) if h_matches > 0 else 0
            heroes_text += f"{idx}. {h_name} ({h_wr}% винрейт, {h_matches} игр)\n"

        # Блок формы (последние матчи)
        recent_matches = player_data.get("matches", [])
        recent_wins = 0
        for m in recent_matches:
            for p in m.get("players", []):
                if p.get("isVictory"):
                    recent_wins += 1
                    break
        
        recent_total = len(recent_matches)
        if recent_total > 0:
            form_text = f"{recent_wins}W - {recent_total - recent_wins}L ({round((recent_wins/recent_total)*100)}%)"
        else:
            form_text = "Нет данных"

        # Смурф-детектор
        smurf_text = ""
        if match_count < 500 and total_winrate > 60:
            smurf_text = "\n⚠️ Внимание: Высокий винрейт на малом количестве игр (возможен смурф/буст)"

        result_text = (
            f"👤 {name}\n"
            f"🏆 Матчей всего: {match_count} (Винрейт: {total_winrate}%)\n\n"
            f"🔥 Топ герои:\n{heroes_text}\n"
            f"📈 Форма (последние {recent_total}): {form_text}"
            f"{smurf_text}"
        )
        
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