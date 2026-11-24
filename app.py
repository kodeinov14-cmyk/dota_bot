import asyncio
import os
import sys
import threading
from dotenv import load_dotenv
from flask import Flask
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from logic import get_account_id, get_player_info, get_draft_state, get_counterpicks, get_item_builds, calculate_win_probability, format_draft_advice, ROLE_NAMES

sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")

# FSM Storage
storage = MemoryStorage()

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=storage)


# ==================== FSM STATES ====================
class DraftState(StatesGroup):
    waiting_for_role = State()
    watching_draft = State()


# ==================== ROLE KEYBOARD ====================
def get_role_keyboard():
    """Inline keyboard для выбора роли"""
    buttons = [
        [
            InlineKeyboardButton(text="⚔️ Carry (Pos 1)", callback_data="role_carry"),
            InlineKeyboardButton(text="🧠 Mid (Pos 2)", callback_data="role_mid"),
        ],
        [
            InlineKeyboardButton(text="🛡 Offlane (Pos 3)", callback_data="role_offlane"),
            InlineKeyboardButton(text="🤝 Support (Pos 4)", callback_data="role_support"),
        ],
        [
            InlineKeyboardButton(text="✨ Hard Support (Pos 5)", callback_data="role_hard_support"),
            InlineKeyboardButton(text="🔄 Flex / Any", callback_data="role_flex"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

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
    await message.answer(
        "Привет! Кидай ссылку на профиль Steam, и я покажу разведку по игроку.\n\n"
        "🆕 <b>Премиум функции:</b>\n"
        "/draft <match_id> — Реальный помощник по драфту с контрпиками и айтемами"
    )


@dp.message(Command("draft"))
async def cmd_draft(message: types.Message, state: FSMContext):
    """Запуск драфт-ассистента"""
    args = message.text.split()
    if len(args) < 2:
        await message.answer(
            "Использование: /draft <match_id>\n"
            "Пример: /draft 1234567890\n\n"
            "Match ID можно найти на STRATZ или OpenDota в URL матча."
        )
        return

    try:
        match_id = int(args[1])
    except ValueError:
        await message.answer("Неверный Match ID. Должно быть число.")
        return

    # Сначала получаем состояние драфта
    await message.answer("🔄 Получаю состояние драфта...")
    draft = get_draft_state(match_id)

    if not draft:
        await message.answer("Не удалось найти матч. Проверь Match ID.")
        return

    # Проверяем, идет ли драфт (есть ли пики/баны)
    pick_bans = draft.get("pickBans", [])
    if not pick_bans:
        await message.answer("Драфт ещё не начался. Попробуй позже.")
        return

    # Сохраняем match_id и просим выбрать роль
    await state.update_data(match_id=match_id)
    await state.set_state(DraftState.waiting_for_role)

    await message.answer(
        f"✅ Матч найден: #{match_id}\n"
        f"Драфт в процессе... ({len(pick_bans)} действий)\n\n"
        f"<b>Выбери свою роль:</b>",
        reply_markup=get_role_keyboard(),
        parse_mode="HTML"
    )

# ==================== DRAFT CALLBACKS ====================
@dp.callback_query(F.data.startswith("role_"), DraftState.waiting_for_role)
async def process_role_selection(callback: types.CallbackQuery, state: FSMContext):
    """Обработка выбора роли"""
    role = callback.data.replace("role_", "")
    role_display = ROLE_NAMES.get(role, role)

    await callback.message.edit_text(
        f"✅ Роль выбрана: <b>{role_display}</b>\n"
        f"🔄 Запускаю мониторинг драфта...\n\n"
        f"Я буду присылать рекомендации перед твоим пиком.",
        parse_mode="HTML"
    )

    # Получаем match_id из state
    data = await state.get_data()
    match_id = data.get("match_id")

    # Запускаем фоновую задачу мониторинга
    asyncio.create_task(watch_draft(callback.message, match_id, role, state))

    await state.set_state(DraftState.watching_draft)


async def watch_draft(message: types.Message, match_id: int, user_role: str, state: FSMContext):
    """Фоновая задача: опрашивает драфт каждые 5 секунд и присылает советы"""
    last_advice_action = -1
    last_pick_count = 0

    while True:
        try:
            draft = get_draft_state(match_id)
            if not draft:
                await message.answer("❌ Матч не найден или закончился.")
                break

            pick_bans = draft.get("pickBans", [])
            current_action = len(pick_bans)

            # Проверяем, изменилось ли что-то
            if current_action == last_pick_count:
                await asyncio.sleep(5)
                continue

            last_pick_count = current_action

            # Определяем, чей сейчас ход (упрощенно)
            # В Captain's Mode порядок фиксированный
            # Можно использовать order поле из pickBans

            # Формируем совет для текущего состояния
            advice = format_draft_advice(draft, user_role)

            # Отправляем только если это новый совет (не дублируем)
            if current_action != last_advice_action:
                last_advice_action = current_action
                await message.answer(advice, parse_mode="HTML")

            # Проверяем, закончен ли драфт (10 пиков + баны)
            picks_count = sum(1 for pb in pick_bans if pb.get("isPick"))
            if picks_count >= 10:
                await message.answer("✅ Драфт завершен! Удачи в игре!")
                break

            await asyncio.sleep(5)

        except Exception as e:
            print(f"Draft watch error: {e}", flush=True)
            await asyncio.sleep(10)

    await state.clear()


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
        behavior = player_data.get("behaviorScore")
        season_rank = player_data.get("seasonRank")
        public_total = player_data.get("publicMatchesTotal", 0)
        hidden = player_data.get("hiddenMatches", 0)

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

        # Инфа о приватности матчей
        privacy_text = ""
        if hidden > 0:
            privacy_text = f"\n🔒 Публичных матчей: {public_total} / {match_count} ({hidden} скрыты — была приватность)\n💡 Откройте историю в Dota 2 → настройки → Expose Public Match Data"

        # Смурф-детектор
        smurf_text = ""
        if match_count < 500 and total_winrate > 60:
            smurf_text = "\n⚠️ Внимание: Высокий винрейт на малом количестве игр (возможен смурф/буст)"

        # MMR/ранг/behavior
        extra_text = ""
        if season_rank:
            rank_names = {
                1: "Herald", 2: "Herald", 3: "Herald", 4: "Herald", 5: "Herald",
                11: "Guardian", 12: "Guardian", 13: "Guardian", 14: "Guardian", 15: "Guardian",
                21: "Crusader", 22: "Crusader", 23: "Crusader", 24: "Crusader", 25: "Crusader",
                31: "Archon", 32: "Archon", 33: "Archon", 34: "Archon", 35: "Archon",
                41: "Legend", 42: "Legend", 43: "Legend", 44: "Legend", 45: "Legend",
                51: "Ancient", 52: "Ancient", 53: "Ancient", 54: "Ancient", 55: "Ancient",
                61: "Divine", 62: "Divine", 63: "Divine", 64: "Divine", 65: "Divine",
                71: "Immortal", 72: "Immortal", 73: "Immortal", 74: "Immortal", 75: "Immortal",
                80: "Immortal"
            }
            rank_name = rank_names.get(season_rank, f"Rank {season_rank}")
            extra_text += f"\n🏅 Ранг: {rank_name}"
        if behavior is not None:
            extra_text += f" | Behavior: {behavior}"

        result_text = (
            f"👤 {name}\n"
            f"🏆 Матчей всего: {match_count} (Винрейт: {total_winrate}%){extra_text}\n\n"
            f"🔥 Топ герои:\n{heroes_text}"
            f"{privacy_text}\n"
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