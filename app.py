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

from logic import (
    get_account_id,
    get_player_info,
    get_draft_state,
    get_counterpicks,
    get_item_builds,
    calculate_win_probability,
    calculate_win_probability_from_picks,
    format_draft_advice,
    format_manual_draft_advice,
    ROLE_NAMES,
    is_match_live,
    get_match_date_str,
    get_hero_name,
    get_hero_names,
    get_hero_id_by_name,
    get_item_name,
    get_gameplan_for_role,
    get_key_timings,
    detect_hero_role,
    get_heroes_countering,
    get_cache_stats,
    clear_all_caches,
    FALLBACK_HERO_NAMES,
    get_meta_picks_for_role,
    analyze_threats,
    analyze_opportunities,
)

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


class HistoricalMatchState(StatesGroup):
    waiting_for_match_id = State()
    selecting_hero = State()


# NEW Manual Draft FSM - text-based input flow
class ManualDraftState(StatesGroup):
    choose_role = State()        # Step 1: Choose role via inline buttons
    stage1_allies = State()      # Step 2: Allies picks in stage 1 (text input)
    stage1_enemies = State()     # Step 3: Enemies picks in stage 1 (text input)
    stage2_allies = State()      # Step 4: Allies picks in stage 2 (text input)
    stage2_enemies = State()     # Step 5: Enemies picks in stage 2 (text input) + show "Finish" button
    stage3_allies = State()      # Step 6 (optional): Allies picks in stage 3 (text input)
    stage3_enemies = State()     # Step 7 (optional): Enemies picks in stage 3 (text input)


# ==================== KEYBOARDS ====================
def get_role_keyboard() -> InlineKeyboardMarkup:
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


def get_start_keyboard() -> InlineKeyboardMarkup:
    """Главная клавиатура с кнопками входа"""
    buttons = [
        [InlineKeyboardButton(text="📜 Анализ исторического матча", callback_data="start_historical_match")],
        [InlineKeyboardButton(text="🎮 Ручной драфт-ассистент", callback_data="start_manual_draft")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_hero_selection_keyboard(heroes: list) -> InlineKeyboardMarkup:
    """Клавиатура для выбора героя из конкретного матча (максимум 10 героев)"""
    buttons = []
    row = []
    for i, hero in enumerate(heroes):
        row.append(InlineKeyboardButton(text=hero, callback_data=f"hist_hero_{hero}"))
        if len(row) == 2 or i == len(heroes) - 1:
            buttons.append(row)
            row = []
    buttons.append([InlineKeyboardButton(text="⬅️ Назад к драфту", callback_data="hist_back_to_draft")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_historical_keyboard(user_role: str, user_is_radiant: bool = True) -> InlineKeyboardMarkup:
    """Inline keyboard для исторических матчей (без кнопки обновления)"""
    team_text = "☀️ Radiant" if user_is_radiant else "🌙 Dire"
    buttons = [
        [
            InlineKeyboardButton(text=f"🎭 Смена роли ({ROLE_NAMES.get(user_role, user_role)})", callback_data="draft_change_role"),
        ],
        [
            InlineKeyboardButton(text=f"🔀 Сторона: {team_text}", callback_data="draft_toggle_team"),
            InlineKeyboardButton(text="📊 Детальный анализ", callback_data="draft_detailed"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_manual_draft_finish_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура с кнопкой завершения драфта (со 2-й стадии)"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Завершить драфт", callback_data="manual_finish_draft")]
    ])


# ==================== HELPER FUNCTIONS ====================
def format_historical_match_summary(draft: dict, match_id: int, match_date: str) -> str:
    """Форматирует сводку исторического матча: дата, результат, драфт"""
    pick_bans = draft.get("pickBans", [])
    did_radiant_win = draft.get("didRadiantWin")

    radiant_picks = []
    dire_picks = []
    radiant_bans = []
    dire_bans = []

    for pb in pick_bans:
        if pb.get("heroId"):
            hero_name = get_hero_name(pb["heroId"])
            order = pb.get("order") or 0
            if pb.get("isPick"):
                if pb.get("isRadiant"):
                    radiant_picks.append((order, hero_name))
                else:
                    dire_picks.append((order, hero_name))
            else:
                if pb.get("isRadiant"):
                    radiant_bans.append(hero_name)
                else:
                    dire_bans.append(hero_name)

    radiant_picks.sort(key=lambda x: x[0])
    dire_picks.sort(key=lambda x: x[0])

    radiant_picks = radiant_picks[:5]
    dire_picks = dire_picks[:5]

    if did_radiant_win is True:
        result_text = "☀️ <b>Radiant ПОБЕДИЛ</b>"
    elif did_radiant_win is False:
        result_text = "🌙 <b>Dire ПОБЕДИЛ</b>"
    else:
        result_text = "❓ Результат неизвестен"

    text = f"📜 <b>ИСТОРИЧЕСКИЙ МАТЧ #{match_id}</b>\n"
    text += f"📅 <b>Дата:</b> {match_date}\n"
    text += f"🏆 <b>Результат:</b> {result_text}\n\n"
    text += "📋 <b>ДРАФТ:</b>\n\n"

    text += "☀️ <b>Radiant:</b>\n"
    if radiant_picks:
        for order, name in radiant_picks:
            text += f"  {order}. {name}\n"
    else:
        text += "  (нет пиков)\n"
    if radiant_bans:
        text += f"  🚫 Баны: {', '.join(radiant_bans)}\n"

    text += "\n🌙 <b>Dire:</b>\n"
    if dire_picks:
        for order, name in dire_picks:
            text += f"  {order}. {name}\n"
    else:
        text += "  (нет пиков)\n"
    if dire_bans:
        text += f"  🚫 Баны: {', '.join(dire_bans)}\n"

    return text


def get_match_heroes(draft: dict) -> list:
    """Возвращает список уникальных героев из матча (только пики)"""
    pick_bans = draft.get("pickBans", [])
    heroes = []
    seen = set()
    for pb in pick_bans:
        if pb.get("isPick") and pb.get("heroId"):
            name = get_hero_name(pb["heroId"])
            if name not in seen:
                seen.add(name)
                heroes.append(name)
    return heroes


def format_hero_post_analysis(draft: dict, selected_hero: str, match_id: int) -> str:
    """Формирует пост-анализ для выбранного героя"""
    pick_bans = draft.get("pickBans", [])
    did_radiant_win = draft.get("didRadiantWin")

    # Определяем ID выбранного героя
    hero_id = get_hero_id_by_name(selected_hero)
    if not hero_id:
        return f"❌ Не удалось найти героя: {selected_hero}"

    # Определяем команду героя
    hero_team = None
    hero_pick_order = None
    for pb in pick_bans:
        if pb.get("isPick") and pb.get("heroId") == hero_id:
            hero_team = "Radiant" if pb.get("isRadiant") else "Dire"
            hero_pick_order = pb.get("order")
            break

    # Определяем роль через улучшенную функцию (использует player_slot если есть)
    role = detect_hero_role(draft, hero_id)

    # Собираем героев команд
    enemy_heroes = []
    our_heroes = []
    for pb in pick_bans:
        if pb.get("isPick") and pb.get("heroId"):
            name = get_hero_name(pb["heroId"])
            if pb.get("isRadiant") and hero_team == "Dire":
                enemy_heroes.append((pb.get("order"), name, pb["heroId"]))
            elif not pb.get("isRadiant") and hero_team == "Radiant":
                enemy_heroes.append((pb.get("order"), name, pb["heroId"]))
            elif pb.get("isRadiant") == (hero_team == "Radiant"):
                our_heroes.append(name)

    enemy_heroes.sort(key=lambda x: x[0])

    text = f"📊 <b>ПОСТ-АНАЛИЗ: {selected_hero}</b>\n"
    text += f"Команда: {'☀️ Radiant' if hero_team == 'Radiant' else '🌙 Dire'} | Порядок пика: {hero_pick_order or '?'}\n"
    text += f"Определенная роль: <b>{ROLE_NAMES.get(role, role)}</b>\n\n"

    # 1. Контрпики ВЫБРАННОГО героя (кого он побеждает)
    text += "🎯 <b>Этот герой КОНТРПИКАЕТ:</b>\n"
    counters_of_hero = get_counterpicks(hero_id, role)
    if counters_of_hero:
        for i, cp in enumerate(counters_of_hero[:5], 1):
            text += f"  {i}. <b>{cp['heroName']}</b> — {cp['winRate']}% WR ({cp['matchCount']} игр)\n"
    else:
        text += "  Данных недостаточно\n"

    text += "\n"

    # 2. Герои, которые КОНТРПИКАЮТ выбранного героя
    text += "⚔️ <b>Этого героя КОНТРПИКАЮТ:</b>\n"
    heroes_countering = get_heroes_countering(hero_id, role)
    if heroes_countering:
        for i, hc in enumerate(heroes_countering[:5], 1):
            text += f"  {i}. <b>{hc['heroName']}</b> — {hc['winRate']}% WR против {selected_hero} ({hc['matchCount']} игр)\n"
    else:
        text += "  Данных недостаточно\n"

    text += "\n"

    # 3. Геймплан для роли
    radiant_picks_formatted = [(i + 1, h) for i, h in enumerate(our_heroes)] if hero_team == "Radiant" else [(h[0], h[1]) for h in enemy_heroes]
    dire_picks_formatted = [(h[0], h[1]) for h in enemy_heroes] if hero_team == "Radiant" else [(i + 1, h) for i, h in enumerate(our_heroes)]
    enemy_last_pick = enemy_heroes[0][1] if enemy_heroes else ""
    gameplan = get_gameplan_for_role(role, enemy_last_pick, radiant_picks_formatted, dire_picks_formatted, hero_team == "Radiant")
    text += f"📝 <b>Геймплан ({ROLE_NAMES.get(role, role)}):</b>\n{gameplan}\n\n"

    # 4. Ключевые тайминги
    timings = get_key_timings(role, hero_id)
    if timings:
        text += f"⏰ <b>Ключевые тайминги:</b>\n{timings}\n\n"

    # 5. Ретроспективный прогноз
    win_prob = calculate_win_probability(draft, role)
    text += f"📊 <b>Ретроспективный прогноз победы команды:</b> ~{win_prob}%\n\n"

    # Реальный результат
    if did_radiant_win is not None:
        hero_won = (did_radiant_win and hero_team == "Radiant") or (not did_radiant_win and hero_team == "Dire")
        result = "✅ <b>ПОБЕДА</b>" if hero_won else "❌ <b>ПОРАЖЕНИЕ</b>"
        text += f"🏁 <b>Реальный исход:</b> {result}"

    return text


# ==================== HERO NAME PARSING ====================
def parse_hero_names(text: str) -> list:
    """
    Парсит текст пользователя и возвращает список найденных героев.
    Разделители: запятая, 'и', ' и ', новая строка, пробел (если известные имена).
    Фuzzy match к FALLBACK_HERO_NAMES.
    """
    if not text or not text.strip():
        return []

    # Создаем маппинг для fuzzy поиска (lowercase -> original name)
    hero_lookup = {}
    for hero_id, hero_name in FALLBACK_HERO_NAMES.items():
        hero_lookup[hero_name.lower()] = hero_name
        # Также добавляем краткие варианты
        short = hero_name.lower().replace(" ", "").replace("-", "").replace("'", "")
        hero_lookup[short] = hero_name

    # Популярные сокращения/алиасы
    aliases = {
        "бафф": "Bane",
        "бейн": "Bane",
        "бм": "Broodmother",
        "бруд": "Broodmother",
        "брудмама": "Broodmother",
        "брудмазер": "Broodmother",
        "бх": "Bounty Hunter",
        "баунти": "Bounty Hunter",
        "хант": "Bounty Hunter",
        "цм": "Crystal Maiden",
        "кристал": "Crystal Maiden",
        "дизраптор": "Disruptor",
        "дизра": "Disruptor",
        "пудж": "Pudge",
        "лина": "Lina",
        "па": "Phantom Assassin",
        "фантаз": "Phantom Assassin",
        "фен": "Phantom Assassin",
        "там": "Templar Assassin",
        "тампл": "Templar Assassin",
        "ти": "Templar Assassin",
        "ск": "Shadow Fiend",
        "сф": "Shadow Fiend",
        "sf": "Shadow Fiend",
        "тень": "Shadow Fiend",
        "маг": "Magnus",
        "магнус": "Magnus",
        "еншантресс": "Enchantress",
        "енчан": "Enchantress",
        "чэнь": "Chen",
        "прам": "Primal Beast",
        "праймал": "Primal Beast",
        "мюерта": "Muerta",
        "мертв": "Muerta",
        "рингмастер": "Ringmaster",
        "ринг": "Ringmaster",
        "доньбрекер": "Dawnbreaker",
        "донь": "Dawnbreaker",
        "марси": "Mars",
        "марс": "Mars",
        "мк": "Monkey King",
        "монк": "Monkey King",
        "марки": "Monkey King",
        "ликан": "Lycan",
        "лик": "Lycan",
        "арк": "Arc Warden",
        "арк варден": "Arc Warden",
        "варден": "Arc Warden",
        "апк": "Arc Warden",
        "ам": "Anti-Mage",
        "анти": "Anti-Mage",
        "антимаг": "Anti-Mage",
        "анти мейдж": "Anti-Mage",
        "мейдж": "Anti-Mage",
        "шредера": "Timbersaw",
        "тимбер": "Timbersaw",
        "брайсбэк": "Bristleback",
        "брайс": "Bristleback",
        "сентер": "Centaur Warrunner",
        "центавр": "Centaur Warrunner",
        "одерлорд": "Underlord",
        "андерлорд": "Underlord",
        "трукс": "Tusk",
        "бара": "Spirit Breaker",
        "спирит брейкер": "Spirit Breaker",
        "спирит": "Spirit Breaker",
        "клок": "Clockwerk",
        "клокверк": "Clockwerk",
        "джак": "Jakiro",
        "джакиро": "Jakiro",
        "нетворк": "Nature's Prophet",
        "фуррион": "Nature's Prophet",
        "пророк": "Nature's Prophet",
        "инвокер": "Invoker",
        "инвок": "Invoker",
        "окс": "Outworld Destroyer",
        "аутворлд": "Outworld Destroyer",
        "оди": "Outworld Destroyer",
        "воид": "Void Spirit",
        "воид спирит": "Void Spirit",
        "эмбер": "Ember Spirit",
        "эмбер спирит": "Ember Spirit",
        "эрс": "Earth Spirit",
        "ерф спирит": "Earth Spirit",
        "верл": "Warlock",
        "ворлок": "Warlock",
        "ск": "Skywrath Mage",
        "скайрат": "Skywrath Mage",
        "ск скилл": "Skywrath Mage",
        "скाई": "Skywrath Mage",
        "оа": "Oracle",
        "оракул": "Oracle",
        "дп": "Death Prophet",
        "десс": "Death Prophet",
        "профет": "Death Prophet",
        "виж": "Visage",
        "висаж": "Visage",
        "слар": "Slardar",
        "сладар": "Slardar",
        "саб": "Slark",
        "сларк": "Slark",
        "св": "Sven",
        "свин": "Sven",
        "свен": "Sven",
        "sv": "Sven",
        "tb": "Terrorblade",
        "тб": "Terrorblade",
        "террор": "Terrorblade",
        "тблейд": "Terrorblade",
        "нс": "Night Stalker",
        "найт": "Night Stalker",
        "сладкий": "Slark",
        "випера": "Viper",
        "вайпер": "Viper",
        "пюк": "Puck",
        "пук": "Puck",
        "кошмар": "Bane",
        "бафф": "Bane",
        "дк": "Dragon Knight",
        "драгон": "Dragon Knight",
        "найт": "Night Stalker",
        "оа": "Outworld Destroyer",
        "таск": "Tusk",
        "туск": "Tusk",
        "виверн": "Winter Wyvern",
        "вивер": "Winter Wyvern",
        "вв": "Winter Wyvern",
        "кх": "Kunkka",
        "кунка": "Kunkka",
        "клейнк": "Clinkz",
        "кликз": "Clinkz",
        "зевс": "Zeus",
        "зевс": "Zeus",
        "лич": "Lich",
        "лич": "Lich",
        "лион": "Lion",
        "лион": "Lion",
        "шейм": "Shadow Shaman",
        "шейман": "Shadow Shaman",
        "шов": "Shadow Shaman",
        "витч": "Witch Doctor",
        "витч док": "Witch Doctor",
        "док": "Witch Doctor",
        "дред": "Drow Ranger",
        "дроу": "Drow Ranger",
        "дров": "Drow Ranger",
        "снайп": "Sniper",
        "снайпер": "Sniper",
        "тровер": "Troll Warlord",
        "тровал": "Troll Warlord",
        "джуг": "Juggernaut",
        "джаг": "Juggernaut",
        "джиг": "Juggernaut",
        "лайф": "Lifestealer",
        "лафстейл": "Lifestealer",
        "наикс": "Nyx Assassin",
        "найкс": "Nyx Assassin",
        "па": "Phantom Assassin",
        "фен": "Phantom Assassin",
        "паш": "Phantom Assassin",
        "резект": "Razor",
        "рейзор": "Razor",
        "сд": "Shadow Demon",
        "шд": "Shadow Demon",
        "шадоу": "Shadow Demon",
        "демон": "Shadow Demon",
        "висп": "Io",
        "айо": "Io",
        "ио": "Io",
        "кипер": "Keeper of the Light",
        "кол": "Keeper of the Light",
        "лигхт": "Keeper of the Light",
        "андединг": "Undying",
        "андиинг": "Undying",
        "унайдинг": "Undying",
        "трекс": "Treant Protector",
        "треант": "Treant Protector",
        "протек": "Treant Protector",
        "огр": "Ogre Magi",
        "огр маги": "Ogre Magi",
        "маги": "Ogre Magi",
        "кольт": "Colossus",  # не существует, но на всякий
        "чаос": "Chaos Knight",
        "книт": "Chaos Knight",
        "фв": "Faceless Void",
        "фейс": "Faceless Void",
        "воид": "Faceless Void",
        "мк": "Monkey King",
        "монк": "Monkey King",
        "марки": "Monkey King",
        "панг": "Pangolier",
        "панголиер": "Pangolier",
        "хад": "Hoodwink",
        "хадвинк": "Hoodwink",
        "марси": "Marci",
        "марки": "Marci",
        "виверн": "Winter Wyvern",
        "апк": "Arc Warden",
        "арк": "Arc Warden",
        "варден": "Arc Warden",
        "темпер": "Templar Assassin",
        "ти эй": "Templar Assassin",
        "ти": "Templar Assassin",
        "сифи": "Shadow Fiend",
        "сф": "Shadow Fiend",
        "бх": "Bounty Hunter",
        "бхантер": "Bounty Hunter",
        "дп": "Disruptor",
        "диз": "Disruptor",
        "дизрап": "Disruptor",
        "эйдж": "Ancient Apparition",
        "анциент": "Ancient Apparition",
        "апп": "Ancient Apparition",
        "дум": "Doom",
        "дум": "Doom",
        "урса": "Ursa",
        "урса": "Ursa",
        "гта": "Gyrocopter",
        "джиро": "Gyrocopter",
        "гро": "Gyrocopter",
        "алч": "Alchemist",
        "алхимик": "Alchemist",
        "инв": "Invoker",
        "силенс": "Silencer",
        "сайл": "Silencer",
        "оди": "Outworld Destroyer",
        "окс": "Outworld Destroyer",
        "аут": "Outworld Destroyer",
        "ликан": "Lycan",
        "лик": "Lycan",
        "брю": "Brewmaster",
        "бру": "Brewmaster",
        "рубик": "Rubick",
        "рубик": "Rubick",
        "найкс": "Nyx Assassin",
        "наикс": "Nyx Assassin",
        "нага": "Naga Siren",
        "сайрен": "Naga Siren",
        "мд": "Medusa",
        "медуза": "Medusa",
        "спек": "Spectre",
        "спектра": "Spectre",
        "паш": "Phantom Assassin",
        "фен": "Phantom Assassin",
        "тампл": "Templar Assassin",
        "там": "Templar Assassin",
        "ти": "Templar Assassin",
        "мф": "Morphling",
        "морф": "Morphling",
        "найт": "Night Stalker",
        "сталкер": "Night Stalker",
        "бм": "Broodmother",
        "бруд": "Broodmother",
        "мама": "Broodmother",
        "хюск": "Huskar",
        "хаск": "Huskar",
        "хаскар": "Huskar",
        "вивер": "Viper",
        "вайп": "Viper",
        "вейп": "Viper",
        "дк": "Dragon Knight",
        "драгон": "Dragon Knight",
        "найт": "Night Stalker",
        "веер": "Weaver",
        "вивер": "Weaver",
        "дкис": "Dazzle",
        "даз": "Dazzle",
        "дазл": "Dazzle",
        "омни": "Omniknight",
        "омникнайт": "Omniknight",
        "енчан": "Enchantress",
        "еншантресс": "Enchantress",
        "чен": "Chen",
        "прам": "Primal Beast",
        "праймал": "Primal Beast",
        "мюерта": "Muerta",
        "мертв": "Muerta",
        "ринг": "Ringmaster",
        "донь": "Dawnbreaker",
        "марси": "Mars",
        "марс": "Mars",
        "тимбер": "Timbersaw",
        "шред": "Timbersaw",
        "брайс": "Bristleback",
        "бэк": "Bristleback",
        "центавр": "Centaur Warrunner",
        "одерлорд": "Underlord",
        "туск": "Tusk",
        "бара": "Spirit Breaker",
        "спирит": "Spirit Breaker",
        "клок": "Clockwerk",
        "джак": "Jakiro",
        "фуррион": "Nature's Prophet",
        "пророк": "Nature's Prophet",
        "нетворк": "Nature's Prophet",
        "инв": "Invoker",
        "окс": "Outworld Destroyer",
        "оди": "Outworld Destroyer",
        "воид": "Void Spirit",
        "эмбер": "Ember Spirit",
        "ерф": "Earth Spirit",
        "ворлок": "Warlock",
        "ск": "Skywrath Mage",
        "скай": "Skywrath Mage",
        "оракул": "Oracle",
        "дп": "Death Prophet",
        "десс": "Death Prophet",
        "висаж": "Visage",
        "слар": "Slardar",
        "сларк": "Slark",
        "сладкий": "Slark",
        "свен": "Sven",
        "террор": "Terrorblade",
        "тблейд": "Terrorblade",
        "найт": "Night Stalker",
        "скай": "Skywrath Mage",
        "випера": "Viper",
        "вайпер": "Viper",
        "пук": "Puck",
        "кошмар": "Bane",
        "бейн": "Bane",
        "бейн": "Bane",
    }

    # Объединяем lookup и aliases для удобства поиска
    all_names = {**hero_lookup, **aliases}

    # Функция для попытки найти героя в начале строки
    def try_match_hero(s: str):
        """Пытается найти героя в начале строки, возвращает (hero_name, remaining_string) или (None, s)"""
        s_lower = s.lower().strip()
        if not s_lower:
            return None, s

        # Проверяем самые длинные совпадения первыми (сортируем по длине убыванию)
        sorted_keys = sorted(all_names.keys(), key=len, reverse=True)
        for key in sorted_keys:
            if s_lower.startswith(key):
                # Проверяем, что после ключа идет разделитель, конец строки, или известное имя героя
                rest = s_lower[len(key):].strip()
                if not rest:
                    return all_names[key], ""
                # Если следующий символ - разделитель
                if rest[0] in ' ,.\n\t':
                    return all_names[key], s[len(key):].lstrip(' ,.\n\t')
                # Если остаток начинается с известного имени героя (например "ликан мк" -> после "ликан" остается "мк")
                for next_key in sorted_keys:
                    if rest.startswith(next_key):
                        return all_names[key], s[len(key):].lstrip(' ,.\n\t')
        return None, s

    # Нормализуем разделители: " и " (с пробелами), переносы строк
    # НЕ заменяем "и " внутри слов (например "анти" -> "ант")
    normalized = text.replace(" и ", ",").replace("\n", ",")
    # Разбиваем по запятым
    parts = [p.strip() for p in normalized.split(",") if p.strip()]

    results = []
    for part in parts:
        # Пытаемся извлечь несколько героев из одной части (например "Ликан Мк")
        remaining = part
        while remaining:
            hero, remaining = try_match_hero(remaining)
            if hero:
                results.append(hero)
            else:
                # Если не нашли известного героя в начале, пробуем fuzzy match на всей оставшейся строке
                part_lower = remaining.lower().strip()
                matched = False

                # Прямое совпадение
                if part_lower in all_names:
                    results.append(all_names[part_lower])
                    matched = True
                else:
                    # Частичное совпадение
                    for key, name in all_names.items():
                        if part_lower in key or key in part_lower:
                            results.append(name)
                            matched = True
                            break

                if not matched:
                    # Если не нашли - добавляем как есть
                    results.append(remaining.title())
                break

    # Убираем дубликаты, сохраняя порядок
    seen = set()
    unique_results = []
    for r in results:
        if r not in seen:
            seen.add(r)
            unique_results.append(r)

    return unique_results


def format_manual_draft_advice_new(
    stage: int,
    allies_picks: list,
    enemies_picks: list,
    user_role: str
) -> str:
    """
    Формирует совет для нового ручного драфта (по стадиям).
    stage: 1, 2, или 3
    allies_picks: список героев союзников
    enemies_picks: список героев врагов
    user_role: роль пользователя
    """
    role_name = ROLE_NAMES.get(user_role, user_role)

    # Счётчики
    allies_count = len(allies_picks)
    enemies_count = len(enemies_picks)

    text = f"🎮 <b>РУЧНОЙ ДРАФТ-АССИСТЕНТ</b> | Стадия {stage}\n"
    text += f"Твоя роль: <b>{role_name}</b>\n\n"

    # Текущий драфт
    text += "📋 <b>ТЕКУЩИЙ ДРАФТ:</b>\n"
    text += "🤝 <b>СОЮЗНИКИ:</b>\n"
    if allies_picks:
        for i, name in enumerate(allies_picks, 1):
            text += f"  {i}. {name}\n"
    else:
        text += "  (пусто)\n"

    text += "\n⚔️ <b>ВРАГИ:</b>\n"
    if enemies_picks:
        for i, name in enumerate(enemies_picks, 1):
            text += f"  {i}. {name}\n"
    else:
        text += "  (пусто)\n"

    text += "\n" + "─" * 30 + "\n\n"

    # Анализ и советы
    text += "🧠 <b>АНАЛИЗ И СОВЕТЫ:</b>\n\n"

    # 1. Контрпики против врагов
    if enemies_picks:
        text += "🎯 <b>Контрпики против врагов:</b>\n"
        for enemy_hero in enemies_picks[:5]:
            enemy_id = get_hero_id_by_name(enemy_hero)
            if enemy_id:
                enemy_counters = get_counterpicks(enemy_id, user_role)
                if enemy_counters:
                    top = enemy_counters[0]
                    text += f"  vs <b>{enemy_hero}</b>: {top['heroName']} ({top['winRate']}% WR, {top['matchCount']} игр)\n"
                else:
                    text += f"  vs <b>{enemy_hero}</b>: данных нет\n"
            else:
                text += f"  vs <b>{enemy_hero}</b>: герой не найден в базе\n"
        text += "\n"

    # 2. Синергия с союзниками
    if allies_picks:
        text += "🤝 <b>Синергия с союзниками:</b>\n"
        for ally_hero in allies_picks[:5]:
            ally_id = get_hero_id_by_name(ally_hero)
            if ally_id:
                # Получаем контрпики союзника (герои, которые хорошо работают с ним)
                # Используем get_counterpicks с ролью союзника, но нам нужны синергии
                # Для простоты покажем популярных пиков для роли
                pass
        # Пока просто покажем общую рекомендацию
        text += "  Ищи героев, которые усиливают пики союзников (инициирование, сейв, урон).\n"
        text += "\n"

    # 3. Мета-пики для роли
    text += "📊 <b>Топ мета-пики для твоей роли (общий WR):</b>\n"
    meta_picks = get_meta_picks_for_role(user_role, allies_picks, enemies_picks)
    for i, mp in enumerate(meta_picks[:5], 1):
        text += f"  {i}. <b>{mp['heroName']}</b> — {mp['winRate']}% WR ({mp['matchCount']} игр)\n"
    text += "\n"

    # 4. Геймплан (без привязки к роли, без предметов)
    enemy_list = ", ".join(enemies_picks[:5]) if enemies_picks else "неизвестны"
    allies_list = ", ".join(allies_picks[:5]) if allies_picks else "нет"

    gameplan = (
        "• <b>Early (0-10 мин):</b> Контроллируй линию, не умирай зря, собирай опыт/голду. "
        "Следи за рунами, ставь обсы, реагируй на движения врага.\n"
        "• <b>Mid (10-20 мин):</b> Группируйся за объектами (тауэры, Рошан). "
        "Ищи пики на одиночках, создавай пространство для команды.\n"
        f"• <b>Late (20+ мин):</b> Играй за команде, не лезь один. "
        f"Таргет приоритет: саппорты → мид → кари. Контроль хайграунда.\n"
        f"• <b>Против {enemy_list}:</b> Адаптируй стиль игры под их драфт. Не давай фармить комфортно.\n"
        f"• <b>С союзниками {allies_list}:</b> Комбинируй способности, качай синергичные таланты."
    )
    text += f"📝 <b>ГЕЙМПЛАН:</b>\n{gameplan}\n\n"

    # 5. Опасности и возможности
    threats = analyze_threats(enemies_picks, allies_picks, user_role)
    if threats:
        text += f"⚠️ <b>ОПАСНОСТИ:</b>\n{threats}\n\n"

    opportunities = analyze_opportunities(enemies_picks, allies_picks, user_role)
    if opportunities:
        text += f"✅ <b>ВОЗМОЖНОСТИ:</b>\n{opportunities}\n"

    return text


# ==================== START COMMAND ====================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "Привет! Кидай ссылку на профиль Steam, и я покажу разведку по игроку.\n\n"
        "🆕 <b>Премиум функции:</b>",
        reply_markup=get_start_keyboard(),
        parse_mode="HTML"
    )


@dp.callback_query(F.data == "start_historical_match")
async def start_historical_match(callback: types.CallbackQuery, state: FSMContext):
    """Начало флоу исторического матча - просим ввести Match ID"""
    await state.set_state(HistoricalMatchState.waiting_for_match_id)
    await callback.message.edit_text(
        "📜 <b>Анализ исторического матча</b>\n\n"
        "Пришли Match ID матча (только число).\n\n"
        "Пример: <code>8123456789</code>",
        parse_mode="HTML"
    )
    await callback.answer()


@dp.callback_query(F.data == "start_manual_draft")
async def start_manual_draft(callback: types.CallbackQuery, state: FSMContext):
    """Запуск нового ручного драфт-ассистента"""
    await state.clear()
    await state.set_state(ManualDraftState.choose_role)
    await callback.message.edit_text(
        "🎮 <b>РУЧНОЙ ДРАФТ-АССИСТЕНТ</b>\n\n"
        "Ты вводишь пики по стадиям, бот даёт советы мгновенно.\n"
        "Работает для любого матча (рейтинг, турбо, аркады).\n\n"
        "<b>Шаг 1:</b> Выбери свою роль:",
        reply_markup=get_role_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()


# ==================== COMMAND /draft (legacy) ====================
@dp.message(Command("draft"))
async def cmd_draft(message: types.Message, state: FSMContext):
    """Запуск драфт-ассистента (старая команда для совместимости)"""
    args = message.text.split()
    if len(args) < 2:
        await message.answer(
            "Использование: /draft <match_id>\n"
            "Пример: /draft 1234567890"
        )
        return

    try:
        match_id = int(args[1])
    except ValueError:
        await message.answer("Неверный Match ID. Должно быть число.")
        return

    await process_match_id(message, match_id, state, is_historical=None)


# ==================== MATCH ID HANDLERS ====================
@dp.message(HistoricalMatchState.waiting_for_match_id)
async def process_historical_match_id(message: types.Message, state: FSMContext):
    """Обработка ввода Match ID для исторического матча"""
    try:
        match_id = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Неверный Match ID. Должно быть число. Попробуй еще раз:")
        return

    await process_match_id(message, match_id, state, is_historical=True)


@dp.message(DraftState.waiting_for_role)
async def process_live_draft_match_id(message: types.Message, state: FSMContext):
    """Обработка ввода Match ID для лайв драфта"""
    data = await state.get_data()
    if not data.get("need_match_id"):
        return

    try:
        match_id = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Неверный Match ID. Должно быть число. Попробуй еще раз:")
        return

    await state.update_data(need_match_id=False)
    await process_match_id(message, match_id, state, is_historical=False)


async def process_match_id(message: types.Message, match_id: int, state: FSMContext, is_historical: bool = None):
    """Общая логика обработки Match ID"""
    await message.answer("🔄 Получаю данные матча...")
    draft = get_draft_state(match_id)

    if not draft:
        await message.answer("❌ Не удалось найти матч. Проверь Match ID.")
        return

    pick_bans = draft.get("pickBans", [])
    if not pick_bans:
        await message.answer("❌ В этом матче нет драфта (не Captain's Mode или данные недоступны).")
        return

    match_is_live = is_match_live(draft)
    match_date = get_match_date_str(draft)

    if is_historical is None:
        is_historical = not match_is_live

    if not is_historical and match_is_live:
        # Лайв матч - старый флоу с выбором роли
        await state.update_data(match_id=match_id)
        await state.set_state(DraftState.waiting_for_role)
        await message.answer(
            f"🔴 <b>LIVE МАТЧ</b> #{match_id}\n"
            f"Драфт в процессе... ({len(pick_bans)} действий)\n\n"
            f"<b>Выбери свою роль:</b>",
            reply_markup=get_role_keyboard(),
            parse_mode="HTML"
        )
        return

    # ИСТОРИЧЕСКИЙ МАТЧ - новый флоу
    await state.update_data(match_id=match_id, draft=draft)
    await state.set_state(HistoricalMatchState.selecting_hero)

    summary_text = format_historical_match_summary(draft, match_id, match_date)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Подробности по герою", callback_data="hist_show_heroes")]
    ])

    await message.answer(summary_text, parse_mode="HTML", reply_markup=keyboard)


# ==================== DRAFT CALLBACKS (LIVE) ====================
@dp.callback_query(F.data.startswith("role_"), DraftState.waiting_for_role)
async def process_role_selection(callback: types.CallbackQuery, state: FSMContext):
    """Обработка выбора роли"""
    role = callback.data.replace("role_", "")
    role_display = ROLE_NAMES.get(role, role)

    data = await state.get_data()
    match_id = data.get("match_id")
    is_historical = data.get("is_historical", False)

    draft = get_draft_state(match_id)
    if not draft:
        await callback.message.edit_text("❌ Матч не найден.")
        await state.clear()
        return

    match_is_live = is_match_live(draft)

    await state.update_data(user_role=role, user_is_radiant=True)

    if is_historical or not match_is_live:
        # ИСТОРИЧЕСКИЙ МАТЧ - показываем пост-анализ сразу
        advice = format_draft_advice(draft, role, True, is_live=False)
        keyboard = get_historical_keyboard(role, True)
        await callback.message.edit_text(advice, parse_mode="HTML", reply_markup=keyboard)
        await state.set_state(DraftState.watching_draft)
    else:
        # ЛАЙВ МАТЧ - запускаем мониторинг
        await callback.message.edit_text(
            f"✅ Роль выбрана: <b>{role_display}</b>\n"
            f"🔴 <b>LIVE МАТЧ</b> — запускаю мониторинг...\n\n"
            f"Я буду присылать рекомендации перед твоим пиком.",
            parse_mode="HTML"
        )
        # watch_draft function removed - live draft monitoring not implemented
        await state.set_state(DraftState.watching_draft)


@dp.callback_query(F.data == "draft_change_role", DraftState.watching_draft)
async def process_change_role(callback: types.CallbackQuery, state: FSMContext):
    """Смена роли во время драфта"""
    await callback.message.edit_text(
        f"<b>Выбери новую роль:</b>",
        reply_markup=get_role_keyboard(),
        parse_mode="HTML"
    )
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

    draft = get_draft_state(match_id)
    if draft:
        advice = format_draft_advice(draft, role, user_is_radiant)
        await callback.message.answer(
            advice,
            parse_mode="HTML",
            reply_markup=get_historical_keyboard(role, user_is_radiant)
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
            reply_markup=get_historical_keyboard(user_role, user_is_radiant)
        )


# ==================== HISTORICAL MATCH CALLBACKS ====================
@dp.callback_query(F.data == "hist_show_heroes", HistoricalMatchState.selecting_hero)
async def show_hero_selection(callback: types.CallbackQuery, state: FSMContext):
    """Показывает клавиатуру с героями из матча для выбора"""
    data = await state.get_data()
    draft = data.get("draft")
    match_id = data.get("match_id")

    if not draft:
        await callback.message.edit_text("❌ Данные матча утеряны. Начни заново.")
        await state.clear()
        return

    heroes = get_match_heroes(draft)

    if not heroes:
        await callback.message.edit_text("❌ В матче нет пикнутых героев.")
        return

    keyboard = get_hero_selection_keyboard(heroes)
    await callback.message.edit_text(
        f"📊 <b>Выбери героя для детального анализа:</b>\n"
        f"Матч #{match_id} | {len(heroes)} уникальных героев",
        parse_mode="HTML",
        reply_markup=keyboard
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("hist_hero_"), HistoricalMatchState.selecting_hero)
async def process_hero_selection(callback: types.CallbackQuery, state: FSMContext):
    """Обработка выбора героя - показывает пост-анализ"""
    hero_name = callback.data.replace("hist_hero_", "")

    data = await state.get_data()
    draft = data.get("draft")
    match_id = data.get("match_id")

    if not draft:
        await callback.message.edit_text("❌ Данные матча утеряны. Начни заново.")
        await state.clear()
        return

    await callback.message.edit_text(f"🔄 Генерирую пост-анализ для <b>{hero_name}</b>...", parse_mode="HTML")
    await callback.answer()

    analysis = format_hero_post_analysis(draft, hero_name, match_id)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 К выбору героя", callback_data="hist_show_heroes")],
        [InlineKeyboardButton(text="⬅️ К сводке матча", callback_data="hist_back_to_draft")]
    ])

    await callback.message.edit_text(analysis, parse_mode="HTML", reply_markup=keyboard)


@dp.callback_query(F.data == "hist_back_to_draft", HistoricalMatchState.selecting_hero)
async def back_to_draft_summary(callback: types.CallbackQuery, state: FSMContext):
    """Возврат к сводке матча"""
    data = await state.get_data()
    draft = data.get("draft")
    match_id = data.get("match_id")
    match_date = get_match_date_str(draft)

    summary_text = format_historical_match_summary(draft, match_id, match_date)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Подробности по герою", callback_data="hist_show_heroes")]
    ])

    await callback.message.edit_text(summary_text, parse_mode="HTML", reply_markup=keyboard)
    await callback.answer()


# ==================== NEW MANUAL DRAFT HANDLERS ====================
@dp.callback_query(F.data.startswith("role_"), ManualDraftState.choose_role)
async def manual_choose_role(callback: types.CallbackQuery, state: FSMContext):
    """Выбор роли для нового ручного драфта"""
    role = callback.data.replace("role_", "")
    role_display = ROLE_NAMES.get(role, role)
    await state.update_data(user_role=role, allies_stage1=[], enemies_stage1=[], allies_stage2=[], enemies_stage2=[], allies_stage3=[], enemies_stage3=[])
    await state.set_state(ManualDraftState.stage1_allies)
    await callback.message.edit_text(
        f"✅ Роль выбрана: <b>{role_display}</b>\n\n"
        f"<b>Стадия 1/3 — ПИКИ СОЮЗНИКОВ:</b>\n"
        f"Напиши через запятую, кого взяли союзники на первой стадии.\n"
        f"Например: <code>Disruptor, Crystal Maiden</code> или <code>дизраптор и цм</code>",
        parse_mode="HTML"
    )
    await callback.answer()


@dp.message(ManualDraftState.stage1_allies)
async def manual_stage1_allies(message: types.Message, state: FSMContext):
    """Стадия 1: пики союзников"""
    heroes = parse_hero_names(message.text)
    if not heroes:
        await message.answer("❌ Не распознан ни один герой. Попробуй еще раз (например: <code>Disruptor, CM</code>)", parse_mode="HTML")
        return

    await state.update_data(allies_stage1=heroes)
    await state.set_state(ManualDraftState.stage1_enemies)

    allies_text = ", ".join(heroes)
    await message.answer(
        f"✅ <b>СОЮЗНИКИ (стадия 1):</b> {allies_text}\n\n"
        f"<b>Стадия 1/3 — ПИКИ ВРАГОВ:</b>\n"
        f"Напиши через запятую, кого взяли враги на первой стадии.\n"
        f"Например: <code>Bounty Hunter, Lion</code> или <code>бх и лион</code>",
        parse_mode="HTML"
    )


@dp.message(ManualDraftState.stage1_enemies)
async def manual_stage1_enemies(message: types.Message, state: FSMContext):
    """Стадия 1: пики врагов -> показываем совет"""
    heroes = parse_hero_names(message.text)
    if not heroes:
        await message.answer("❌ Не распознан ни один герой. Попробуй еще раз (например: <code>Bounty Hunter, Lion</code>)", parse_mode="HTML")
        return

    data = await state.get_data()
    allies = data.get("allies_stage1", [])
    user_role = data.get("user_role", "flex")

    await state.update_data(enemies_stage1=heroes)
    await state.set_state(ManualDraftState.stage2_allies)

    enemies_text = ", ".join(heroes)
    advice = format_manual_draft_advice_new(1, allies, heroes, user_role)

    await message.answer(
        f"✅ <b>ВРАГИ (стадия 1):</b> {enemies_text}\n\n"
        f"{advice}\n\n"
        f"<b>Стадия 2/3 — ПИКИ СОЮЗНИКОВ:</b>\n"
        f"Напиши через запятую, кого взяли союзники на второй стадии.",
        parse_mode="HTML"
    )


@dp.message(ManualDraftState.stage2_allies)
async def manual_stage2_allies(message: types.Message, state: FSMContext):
    """Стадия 2: пики союзников"""
    heroes = parse_hero_names(message.text)
    if not heroes:
        await message.answer("❌ Не распознан ни один герой. Попробуй еще раз.", parse_mode="HTML")
        return

    data = await state.get_data()
    allies_s1 = data.get("allies_stage1", [])
    all_allies = allies_s1 + heroes
    await state.update_data(allies_stage2=heroes)
    await state.set_state(ManualDraftState.stage2_enemies)

    allies_text = ", ".join(heroes)
    await message.answer(
        f"✅ <b>СОЮЗНИКИ (стадия 2):</b> {allies_text}\n\n"
        f"<b>Стадия 2/3 — ПИКИ СОЮЗНИКОВ:</b>\n"
        f"Напиши через запятую, кого взяли союзники на второй стадии.",
        parse_mode="HTML"
    )


@dp.message(ManualDraftState.stage2_enemies)
async def manual_stage2_enemies(message: types.Message, state: FSMContext):
    """Стадия 2: пики врагов -> показываем совет + кнопка завершить"""
    heroes = parse_hero_names(message.text)
    if not heroes:
        await message.answer("❌ Не распознан ни один герой. Попробуй еще раз.", parse_mode="HTML")
        return

    data = await state.get_data()
    allies_s1 = data.get("allies_stage1", [])
    allies_s2 = data.get("allies_stage2", [])
    enemies_s1 = data.get("enemies_stage1", [])
    all_allies = allies_s1 + allies_s2
    all_enemies = enemies_s1 + heroes
    user_role = data.get("user_role", "flex")

    await state.update_data(enemies_stage2=heroes)
    await state.set_state(ManualDraftState.stage3_allies)

    enemies_text = ", ".join(heroes)
    advice = format_manual_draft_advice_new(2, all_allies, all_enemies, user_role)

    await message.answer(
        f"✅ <b>ВРАГИ (стадия 2):</b> {enemies_text}\n\n"
        f"{advice}",
        parse_mode="HTML",
        reply_markup=get_manual_draft_finish_keyboard()
    )


@dp.message(ManualDraftState.stage3_allies)
async def manual_stage3_allies(message: types.Message, state: FSMContext):
    """Стадия 3: пики союзников (опционально)"""
    heroes = parse_hero_names(message.text)
    if not heroes:
        await message.answer("❌ Не распознан ни один герой. Попробуй еще раз.", parse_mode="HTML")
        return

    data = await state.get_data()
    allies_s1 = data.get("allies_stage1", [])
    allies_s2 = data.get("allies_stage2", [])
    all_allies = allies_s1 + allies_s2 + heroes
    await state.update_data(allies_stage3=heroes)
    await state.set_state(ManualDraftState.stage3_enemies)

    allies_text = ", ".join(heroes)
    await message.answer(
        f"✅ <b>СОЮЗНИКИ (стадия 3):</b> {allies_text}\n\n"
        f"<b>Стадия 3/3 — ПИКИ ВРАГОВ (финальная):</b>\n"
        f"Напиши через запятую, кого взяли враги на третьей стадии.",
        parse_mode="HTML"
    )


@dp.message(ManualDraftState.stage3_enemies)
async def manual_stage3_enemies(message: types.Message, state: FSMContext):
    """Стадия 3: пики врагов -> финальный совет"""
    heroes = parse_hero_names(message.text)
    if not heroes:
        await message.answer("❌ Не распознан ни один герой. Попробуй еще раз.", parse_mode="HTML")
        return

    data = await state.get_data()
    allies_s1 = data.get("allies_stage1", [])
    allies_s2 = data.get("allies_stage2", [])
    allies_s3 = data.get("allies_stage3", [])
    enemies_s1 = data.get("enemies_stage1", [])
    enemies_s2 = data.get("enemies_stage2", [])
    all_allies = allies_s1 + allies_s2 + allies_s3
    all_enemies = enemies_s1 + enemies_s2 + heroes
    user_role = data.get("user_role", "flex")

    await state.update_data(enemies_stage3=heroes)

    enemies_text = ", ".join(heroes)
    advice = format_manual_draft_advice_new(3, all_allies, all_enemies, user_role)

    await message.answer(
        f"✅ <b>ВРАГИ (стадия 3):</b> {enemies_text}\n\n"
        f"{advice}\n\n"
        f"🎮 <b>ДРАФТ ЗАВЕРШЁН!</b> Удачи в игре!",
        parse_mode="HTML"
    )
    await state.clear()


@dp.callback_query(F.data == "manual_finish_draft")
async def manual_finish_draft(callback: types.CallbackQuery, state: FSMContext):
    """Завершение драфта по кнопке (со 2-й стадии)"""
    data = await state.get_data()
    allies_s1 = data.get("allies_stage1", [])
    allies_s2 = data.get("allies_stage2", [])
    enemies_s1 = data.get("enemies_stage1", [])
    enemies_s2 = data.get("enemies_stage2", [])
    user_role = data.get("user_role", "flex")

    all_allies = allies_s1 + allies_s2
    all_enemies = enemies_s1 + enemies_s2

    advice = format_manual_draft_advice_new(2, all_allies, all_enemies, user_role)

    await callback.message.edit_text(
        f"✅ <b>ДРАФТ ЗАВЕРШЁН ПОЛЬЗОВАТЕЛЕМ (после стадии 2)</b>\n\n"
        f"{advice}\n\n"
        f"🎮 Удачи в игре!",
        parse_mode="HTML"
    )
    await state.clear()
    await callback.answer("Драфт завершён")


# ==================== HELPER PROXIES ====================
def get_counterpicks_by_name(hero_name: str, role: str):
    """Получить контрпики по имени героя"""
    hero_id = get_hero_id_by_name(hero_name)
    if hero_id:
        return get_counterpicks(hero_id, role)
    return []


def get_item_builds_by_name(hero_name: str, role: str):
    """Получить айтемы по имени героя"""
    hero_id = get_hero_id_by_name(hero_name)
    if hero_id:
        return get_item_builds(hero_id, role)
    return []


# ==================== STEAM URL HANDLER ====================
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

        heroes_text = ""
        heroes = player_data.get("heroesPerformance", [])
        for idx, h in enumerate(heroes, 1):
            h_id = h.get("heroId")
            h_matches = h.get("matchCount", 0)
            h_wins = h.get("winCount", 0)
            h_name = get_hero_name(h_id)
            h_wr = round((h_wins / h_matches) * 100, 1) if h_matches > 0 else 0
            heroes_text += f"{idx}. {h_name} ({h_wr}% винрейт, {h_matches} игр)\n"

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

        privacy_text = ""
        if hidden > 0:
            privacy_text = f"\n🔒 Публичных матчей: {public_total} / {match_count} ({hidden} скрыты — была приватность)\n💡 Откройте историю в Dota 2 → настройки → Expose Public Match Data"

        smurf_text = ""
        if match_count < 500 and total_winrate > 60:
            smurf_text = "\n⚠️ Внимание: Высокий винрейт на малом количестве игр (возможен смурф/буст)"

        result_text = (
            f"👤 {name}\n"
            f"🏆 Матчей всего: {match_count} (Винрейт: {total_winrate}%)\n\n"
            f"🔥 Топ герои:\n{heroes_text}"
            f"{privacy_text}\n"
            f"📈 Форма (последние {recent_total}): {form_text}"
            f"{smurf_text}"
        )

        await message.answer(result_text)

    except Exception as e:
        print(f"ОШИБКА: {e}", flush=True)
        await message.answer(f"Произошла ошибка: {e}")


# ==================== FLASK & MAIN ====================
app = Flask(__name__)


@app.route('/')
def index():
    return "Bot is running"


@app.route('/health')
def health():
    return "OK"


@app.route('/cache-stats')
def cache_stats():
    """Эндпоинт для мониторинга кэшей"""
    stats = get_cache_stats()
    return {
        "status": "ok",
        "caches": stats
    }


@app.route('/cache-clear', methods=['POST'])
def cache_clear():
    """Эндпоинт для очистки кэшей"""
    clear_all_caches()
    return {"status": "ok", "message": "All caches cleared"}


def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


if __name__ == "__main__":
    t = threading.Thread(target=run_flask)
    t.daemon = True
    t.start()

    print("Бот запущен...", flush=True)
    asyncio.run(dp.start_polling(bot))