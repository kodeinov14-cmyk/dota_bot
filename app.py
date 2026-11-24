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

# ==================== DRAFT KEYBOARDS ====================
def get_draft_control_keyboard(user_role: str, user_is_radiant: bool = True):
    """Inline keyboard для управления драфтом"""
    team_text = "☀️ Radiant" if user_is_radiant else "🌙 Dire"
    buttons = [
        [
            InlineKeyboardButton(text="🔄 Обновить", callback_data=f"draft_refresh"),
            InlineKeyboardButton(text=f"🎭 Смена роли ({ROLE_NAMES.get(user_role, user_role)})", callback_data="draft_change_role"),
        ],
        [
            InlineKeyboardButton(text=f"🔀 Сторона: {team_text}", callback_data="draft_toggle_team"),
            InlineKeyboardButton(text="📊 Детальный анализ", callback_data="draft_detailed"),
        ],
        [
            InlineKeyboardButton(text="⏹ Остановить мониторинг", callback_data="draft_stop"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


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

    # Сохраняем роль и команду (по умолчанию Radiant)
    await state.update_data(user_role=role, user_is_radiant=True)

    # Запускаем фоновую задачу мониторинга
    asyncio.create_task(watch_draft(callback.message, match_id, role, True, state))

    await state.set_state(DraftState.watching_draft)


@dp.callback_query(F.data == "draft_change_role", DraftState.watching_draft)
async def process_change_role(callback: types.CallbackQuery, state: FSMContext):
    """Смена роли во время драфта"""
    await callback.message.edit_text(
        f"<b>Выбери новую роль:</b>",
        reply_markup=get_role_keyboard(),
        parse_mode="HTML"
    )
    # Временно переключаем в состояние выбора роли
    await state.set_state(DraftState.waiting_for_role)


@dp.callback_query(F.data.startswith("role_"), DraftState.watching_draft)
async def process_role_change_during_draft(callback: types.CallbackQuery, state: FSMContext):
    """Обработка смены роли во время драфта"""
    role = callback.data.replace("role_", "")
    role_display = ROLE_NAMES.get(role, role)

    data = await state.get_data()
    match_id = data.get("match_id")
    user_is_radiant = data.get("user_is_radiant", True)

    await state.update_data(user_role=role)

    await callback.message.edit_text(
        f"✅ Роль изменена: <b>{role_display}</b>\n"
        f"🔄 Обновляю рекомендации...",
        parse_mode="HTML"
    )

    # Сразу показываем обновленный совет
    draft = get_draft_state(match_id)
    if draft:
        advice = format_draft_advice(draft, role, user_is_radiant)
        await callback.message.answer(
            advice,
            parse_mode="HTML",
            reply_markup=get_draft_control_keyboard(role, user_is_radiant)
        )

    await state.set_state(DraftState.watching_draft)


@dp.callback_query(F.data == "draft_toggle_team", DraftState.watching_draft)
async def process_toggle_team(callback: types.CallbackQuery, state: FSMContext):
    """Переключение стороны (Radiant/Dire)"""
    data = await state.get_data()
    user_role = data.get("user_role", "flex")
    user_is_radiant = not data.get("user_is_radiant", True)

    await state.update_data(user_is_radiant=user_is_radiant)

    team_text = "☀️ Radiant" if user_is_radiant else "🌙 Dire"
    match_id = data.get("match_id")

    await callback.message.edit_text(
        f"🔀 Сторона изменена: <b>{team_text}</b>\n"
        f"🔄 Обновляю рекомендации...",
        parse_mode="HTML"
    )

    draft = get_draft_state(match_id)
    if draft:
        advice = format_draft_advice(draft, user_role, user_is_radiant)
        await callback.message.answer(
            advice,
            parse_mode="HTML",
            reply_markup=get_draft_control_keyboard(user_role, user_is_radiant)
        )


@dp.callback_query(F.data == "draft_refresh", DraftState.watching_draft)
async def process_refresh(callback: types.CallbackQuery, state: FSMContext):
    """Принудительное обновление состояния драфта"""
    data = await state.get_data()
    match_id = data.get("match_id")
    user_role = data.get("user_role", "flex")
    user_is_radiant = data.get("user_is_radiant", True)

    await callback.answer("🔄 Обновляю...")

    draft = get_draft_state(match_id)
    if draft:
        advice = format_draft_advice(draft, user_role, user_is_radiant)
        await callback.message.edit_text(
            advice,
            parse_mode="HTML",
            reply_markup=get_draft_control_keyboard(user_role, user_is_radiant)
        )
    else:
        await callback.message.edit_text("❌ Матч не найден или закончился.")


@dp.callback_query(F.data == "draft_detailed", DraftState.watching_draft)
async def process_detailed(callback: types.CallbackQuery, state: FSMContext):
    """Показать детальный анализ"""
    data = await state.get_data()
    match_id = data.get("match_id")
    user_role = data.get("user_role", "flex")
    user_is_radiant = data.get("user_is_radiant", True)

    await callback.answer("📊 Генерирую детальный анализ...")

    draft = get_draft_state(match_id)
    if draft:
        # Дополнительный детальный анализ
        detailed = get_detailed_analysis(draft, user_role, user_is_radiant)
        await callback.message.answer(detailed, parse_mode="HTML")


@dp.callback_query(F.data == "draft_stop", DraftState.watching_draft)
async def process_stop(callback: types.CallbackQuery, state: FSMContext):
    """Остановка мониторинга"""
    await state.clear()
    await callback.message.edit_text(
        "⏹ <b>Мониторинг остановлен</b>\n\n"
        "Можешь начать новый драфт командой /draft <match_id>",
        parse_mode="HTML"
    )


async def watch_draft(message: types.Message, match_id: int, user_role: str, user_is_radiant: bool, state: FSMContext):
    """Фоновая задача: опрашивает драфт каждые 5 секунд и присылает советы"""
    last_advice_action = -1
    last_pick_count = 0
    last_draft_hash = None
    consecutive_errors = 0
    max_consecutive_errors = 3

    def _draft_hash(pick_bans):
        """Создает хеш состояния драфта, безопасно обрабатывая None значения"""
        return hash(str(sorted([
            (pb.get("order") or 0, pb.get("heroId"), pb.get("isPick"), pb.get("isRadiant"))
            for pb in pick_bans
        ])))

    # Сразу отправляем первое состояние
    try:
        draft = get_draft_state(match_id)
        if not draft:
            await message.answer("❌ Матч не найден или закончился.")
            await state.clear()
            return

        pick_bans = draft.get("pickBans", [])
        advice = format_draft_advice(draft, user_role, user_is_radiant)
        await message.answer(
            advice,
            parse_mode="HTML",
            reply_markup=get_draft_control_keyboard(user_role, user_is_radiant)
        )
        last_advice_action = len(pick_bans)
        last_draft_hash = _draft_hash(pick_bans)
        print(f"[Draft] Initial state sent for match {match_id}, actions: {last_advice_action}", flush=True)
    except Exception as e:
        print(f"[Draft] Initial fetch error for match {match_id}: {e}", flush=True)
        await message.answer(f"❌ Ошибка при получении драфта: {e}")
        await state.clear()
        return

    while True:
        try:
            await asyncio.sleep(5)

            draft = get_draft_state(match_id)
            if not draft:
                await message.answer("❌ Матч не найден или закончился.")
                break

            pick_bans = draft.get("pickBans", [])
            current_action = len(pick_bans)

            # Создаем хеш текущего состояния для дедупликации
            draft_hash = _draft_hash(pick_bans)

            # Проверяем, изменилось ли что-то
            if draft_hash == last_draft_hash:
                continue

            last_draft_hash = draft_hash
            consecutive_errors = 0  # Reset on success

            # Формируем совет для текущего состояния
            try:
                advice = format_draft_advice(draft, user_role, user_is_radiant)
            except Exception as e:
                print(f"[Draft] format_draft_advice error: {e}", flush=True)
                advice = f"⚠️ Ошибка формирования совета: {e}"

            # Отправляем совет с клавиатурой управления
            if current_action != last_advice_action or last_advice_action == -1:
                last_advice_action = current_action
                try:
                    await message.answer(
                        advice,
                        parse_mode="HTML",
                        reply_markup=get_draft_control_keyboard(user_role, user_is_radiant)
                    )
                    print(f"[Draft] Update sent for match {match_id}, action #{current_action}", flush=True)
                except Exception as e:
                    print(f"[Draft] Send message error: {e}", flush=True)

            # Проверяем, закончен ли драфт (10 пиков)
            picks_count = sum(1 for pb in pick_bans if pb.get("isPick"))
            if picks_count >= 10:
                await message.answer("✅ Драфт завершен! Удачи в игре! 🎮")
                break

        except Exception as e:
            consecutive_errors += 1
            error_msg = f"[Draft] Error #{consecutive_errors} for match {match_id}: {type(e).__name__}: {e}"
            print(error_msg, flush=True)

            # Send error to user on first error
            if consecutive_errors == 1:
                await message.answer(f"⚠️ Ошибка при опросе драфта: {type(e).__name__}: {e}\nПовторная попытка через 10 сек...")

            if consecutive_errors >= max_consecutive_errors:
                await message.answer(f"❌ Мониторинг остановлен после {max_consecutive_errors} ошибок: {type(e).__name__}: {e}")
                break

            await asyncio.sleep(10)

    await state.clear()
    print(f"[Draft] Monitoring stopped for match {match_id}", flush=True)


def get_detailed_analysis(draft_state: dict, user_role: str, user_is_radiant: bool) -> str:
    """Детальный анализ драфта"""
    from logic import get_hero_name, get_counterpicks, get_item_builds, ROLE_NAMES

    pick_bans = draft_state.get("pickBans", [])

    radiant_heroes = []
    dire_heroes = []
    for pb in pick_bans:
        if pb.get("isPick") and pb.get("heroId"):
            name = get_hero_name(pb["heroId"])
            if pb.get("isRadiant"):
                radiant_heroes.append(name)
            else:
                dire_heroes.append(name)

    role_name = ROLE_NAMES.get(user_role, user_role)
    team = "Radiant" if user_is_radiant else "Dire"
    enemy_team = "Dire" if user_is_radiant else "Radiant"
    enemy_heroes = dire_heroes if user_is_radiant else radiant_heroes

    text = f"📊 **ДЕТАЛЬНЫЙ АНАЛИЗ ДРАФТА**\n\n"
    text += f"Твоя команда: {team} | Роль: {role_name}\n\n"

    # Анализ вражеской команды
    text += f"🔴 **Вражеская команда ({enemy_team}):**\n"
    for hero in enemy_heroes:
        counters = get_counterpicks_by_name(hero, user_role)
        if counters:
            top = counters[0]
            text += f"  • {hero} → Контр: **{top['heroName']}** ({top['winRate']}% WR)\n"
        else:
            text += f"  • {hero} → Контр: данных нет\n"

    text += "\n"

    # Синергии в нашей команде
    our_heroes = radiant_heroes if user_is_radiant else dire_heroes
    if len(our_heroes) > 1:
        text += f"🤝 **Синергии в нашей команде:**\n"
        # Можно добавить проверку синергий
        text += f"  Твои союзники: {', '.join([h for h in our_heroes if h != get_hero_name(0)])}\n"

    text += "\n"

    # Рекомендации по айтемам против всей вражеской команды
    text += f"🛡 **Предметы против вражеского драфта:**\n"
    for hero in enemy_heroes[:3]:  # Топ-3 врага
        items = get_item_builds_by_name(hero, user_role)
        if items:
            core = [get_item_name(i['itemId']) for i in items[:2]]
            text += f"  vs {hero}: {', '.join(core)}\n"

    return text


def get_counterpicks_by_name(hero_name: str, role: str):
    """Получить контрпики по имени героя"""
    from logic import get_hero_names, get_counterpicks
    names = get_hero_names()
    hero_id = None
    for k, v in names.items():
        if v.lower() == hero_name.lower():
            hero_id = k
            break
    if hero_id:
        return get_counterpicks(hero_id, role)
    return []


def get_item_builds_by_name(hero_name: str, role: str):
    """Получить айтемы по имени героя"""
    from logic import get_hero_names, get_item_builds
    names = get_hero_names()
    hero_id = None
    for k, v in names.items():
        if v.lower() == hero_name.lower():
            hero_id = k
            break
    if hero_id:
        return get_item_builds(hero_id, role)
    return []


def get_item_name(item_id: int) -> str:
    """Получить название предмета (прокси к logic)"""
    from logic import get_item_name as logic_get_item_name
    return logic_get_item_name(item_id)


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