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


class ManualDraftState(StatesGroup):
    choose_role = State()
    choose_side = State()
    picking = State()


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
        [InlineKeyboardButton(text="🔴 Live драфт-ассистент", callback_data="start_live_draft")],
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


def get_draft_control_keyboard(user_role: str, user_is_radiant: bool = True) -> InlineKeyboardMarkup:
    """Inline keyboard для управления драфтом (LIVE матчи)"""
    team_text = "☀️ Radiant" if user_is_radiant else "🌙 Dire"
    buttons = [
        [
            InlineKeyboardButton(text="🔄 Обновить", callback_data="draft_refresh"),
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


# ==================== MANUAL DRAFT KEYBOARDS ====================
def get_manual_draft_side_keyboard() -> InlineKeyboardMarkup:
    """Выбор стороны для ручного драфта"""
    buttons = [
        [
            InlineKeyboardButton(text="☀️ Я — Radiant", callback_data="manual_side_radiant"),
            InlineKeyboardButton(text="🌙 Я — Dire", callback_data="manual_side_dire"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_manual_draft_main_keyboard(user_role: str, user_is_radiant: bool, radiant_count: int, dire_count: int, next_is_radiant: bool) -> InlineKeyboardMarkup:
    """Главная клавиатура ручного драфта"""
    team_text = "☀️ Radiant" if user_is_radiant else "🌙 Dire"
    next_text = "☀️ Radiant" if next_is_radiant else "🌙 Dire"
    total = radiant_count + dire_count

    buttons = [
        [
            InlineKeyboardButton(text=f"🎭 Роль: {ROLE_NAMES.get(user_role, user_role)}", callback_data="manual_change_role"),
            InlineKeyboardButton(text=f"🔀 Сторона: {team_text}", callback_data="manual_change_side"),
        ],
        [
            InlineKeyboardButton(text=f"➕ Добавить пик для {next_text}", callback_data="manual_add_pick"),
        ],
        [
            InlineKeyboardButton(text=f"↩️ Отменить последний пик", callback_data="manual_undo"),
            InlineKeyboardButton(text="🔄 Сбросить драфт", callback_data="manual_reset"),
        ],
        [
            InlineKeyboardButton(text="⏹ Завершить", callback_data="manual_stop"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_hero_search_keyboard(query: str = "", page: int = 0, user_is_radiant: bool = True, radiant_picks: list = None, dire_picks: list = None) -> InlineKeyboardMarkup:
    """Клавиатура поиска героя с пагинацией (исключая уже взятых)"""
    if radiant_picks is None:
        radiant_picks = []
    if dire_picks is None:
        dire_picks = []

    taken = set(radiant_picks + dire_picks)
    all_heroes = list(FALLBACK_HERO_NAMES.values())

    # Фильтр по запросу
    if query:
        filtered = [h for h in all_heroes if query.lower() in h.lower() and h not in taken]
    else:
        filtered = [h for h in all_heroes if h not in taken]

    # Пагинация: 20 героев на страницу, по 2 в ряд
    per_page = 20
    total_pages = max(1, (len(filtered) + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))

    start = page * per_page
    end = min(start + per_page, len(filtered))
    page_heroes = filtered[start:end]

    buttons = []
    row = []
    for i, hero in enumerate(page_heroes):
        row.append(InlineKeyboardButton(text=hero, callback_data=f"manual_pick_{hero}"))
        if len(row) == 2 or i == len(page_heroes) - 1:
            buttons.append(row)
            row = []

    # Навигация
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="⬅️", callback_data=f"manual_page_{page-1}_{query}"))
    nav_row.append(InlineKeyboardButton(text=f"{page+1}/{total_pages}", callback_data="manual_page_info"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton(text="➡️", callback_data=f"manual_page_{page+1}_{query}"))
    if nav_row:
        buttons.append(nav_row)

    # Поиск и назад
    buttons.append([
        InlineKeyboardButton(text="🔍 Поиск", callback_data="manual_search"),
        InlineKeyboardButton(text="⬅️ Назад", callback_data="manual_back_to_main"),
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


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


@dp.callback_query(F.data == "start_live_draft")
async def start_live_draft(callback: types.CallbackQuery, state: FSMContext):
    """Начало флоу лайв драфта - просим ввести Match ID"""
    await state.set_state(DraftState.waiting_for_role)
    await state.update_data(need_match_id=True)
    await callback.message.edit_text(
        "🔴 <b>Live драфт-ассистент</b>\n\n"
        "Пришли Match ID активного матча (только число).\n\n"
        "Пример: <code>8123456789</code>",
        parse_mode="HTML"
    )
    await callback.answer()


@dp.callback_query(F.data == "start_manual_draft")
async def start_manual_draft(callback: types.CallbackQuery, state: FSMContext):
    """Запуск ручного драфт-ассистента"""
    await state.clear()
    await state.set_state(ManualDraftState.choose_role)
    await callback.message.edit_text(
        "🎮 <b>РУЧНОЙ ДРАФТ-АССИСТЕНТ</b>\n\n"
        "Ты сам вводишь пики, бот даёт советы мгновенно.\n"
        "Работает для любого матча (рейтинг, турбо, аркады).\n\n"
        "<b>Шаг 1 из 3:</b> Выбери свою роль:",
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
    last_draft_hash = None
    consecutive_errors = 0
    max_consecutive_errors = 3

    def _draft_hash(pick_bans):
        return hash(str(sorted([
            (pb.get("order") or 0, pb.get("heroId"), pb.get("isPick"), pb.get("isRadiant"))
            for pb in pick_bans
        ])))

    try:
        draft = get_draft_state(match_id)
        if not draft:
            await message.answer("❌ Матч не найден или закончился.")
            await state.clear()
            return

        if not is_match_live(draft):
            advice = format_draft_advice(draft, user_role, user_is_radiant, is_live=False)
            await message.answer(advice, parse_mode="HTML", reply_markup=get_historical_keyboard(user_role, user_is_radiant))
            await message.answer("📜 Матч завершен. Мониторинг остановлен.")
            await state.clear()
            return

        pick_bans = draft.get("pickBans", [])
        advice = format_draft_advice(draft, user_role, user_is_radiant, is_live=True)
        await message.answer(advice, parse_mode="HTML", reply_markup=get_draft_control_keyboard(user_role, user_is_radiant))
        last_advice_action = len(pick_bans)
        last_draft_hash = _draft_hash(pick_bans)
    except Exception as e:
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

            if not is_match_live(draft):
                advice = format_draft_advice(draft, user_role, user_is_radiant, is_live=False)
                await message.answer(advice, parse_mode="HTML", reply_markup=get_historical_keyboard(user_role, user_is_radiant))
                await message.answer("📜 Матч завершен. Мониторинг остановлен.")
                break

            pick_bans = draft.get("pickBans", [])
            current_action = len(pick_bans)
            draft_hash = _draft_hash(pick_bans)

            if draft_hash == last_draft_hash:
                continue

            last_draft_hash = draft_hash
            consecutive_errors = 0

            try:
                advice = format_draft_advice(draft, user_role, user_is_radiant, is_live=True)
            except Exception as e:
                advice = f"⚠️ Ошибка формирования совета: {e}"

            if current_action != last_advice_action or last_advice_action == -1:
                last_advice_action = current_action
                try:
                    await message.answer(advice, parse_mode="HTML", reply_markup=get_draft_control_keyboard(user_role, user_is_radiant))
                except Exception as e:
                    print(f"[Draft] Send message error: {e}", flush=True)

            picks_count = sum(1 for pb in pick_bans if pb.get("isPick"))
            if picks_count >= 10:
                await message.answer("✅ Драфт завершен! Удачи в игре! 🎮")
                break

        except Exception as e:
            consecutive_errors += 1
            if consecutive_errors == 1:
                await message.answer(f"⚠️ Ошибка при опросе драфта: {type(e).__name__}: {e}\nПовторная попытка через 10 сек...")

            if consecutive_errors >= max_consecutive_errors:
                await message.answer(f"❌ Мониторинг остановлен после {max_consecutive_errors} ошибок: {type(e).__name__}: {e}")
                break

            await asyncio.sleep(10)

    await state.clear()


# ==================== DETAILED ANALYSIS ====================
def get_detailed_analysis(draft_state: dict, user_role: str, user_is_radiant: bool) -> str:
    """Детальный анализ драфта"""
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

    text += f"🔴 **Вражеская команда ({enemy_team}):**\n"
    for hero in enemy_heroes:
        counters = get_counterpicks_by_name(hero, user_role)
        if counters:
            top = counters[0]
            text += f"  • {hero} → Контр: **{top['heroName']}** ({top['winRate']}% WR)\n"
        else:
            text += f"  • {hero} → Контр: данных нет\n"

    text += "\n"

    our_heroes = radiant_heroes if user_is_radiant else dire_heroes
    if len(our_heroes) > 1:
        text += f"🤝 **Синергии в нашей команде:**\n"
        text += f"  Твои союзники: {', '.join([h for h in our_heroes if h != get_hero_name(0)])}\n"

    text += "\n"

    text += f"🛡 **Предметы против вражеского драфта:**\n"
    for hero in enemy_heroes[:3]:
        items = get_item_builds_by_name(hero, user_role)
        if items:
            core = [get_item_name(i['itemId']) for i in items[:2]]
            text += f"  vs {hero}: {', '.join(core)}\n"

    return text


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


# ==================== MANUAL DRAFT HANDLERS ====================
@dp.message(Command("draft_manual"))
async def cmd_manual_draft(message: types.Message, state: FSMContext):
    """Запуск ручного драфт-ассистента"""
    await state.clear()
    await state.set_state(ManualDraftState.choose_role)
    await message.answer(
        "🎮 <b>РУЧНОЙ ДРАФТ-АССИСТЕНТ</b>\n\n"
        "Ты сам вводишь пики, бот даёт советы мгновенно.\n"
        "Работает для любого матча (рейтинг, турбо, аркады).\n\n"
        "<b>Шаг 1 из 3:</b> Выбери свою роль:",
        reply_markup=get_role_keyboard(),
        parse_mode="HTML"
    )


@dp.callback_query(F.data.startswith("role_"), ManualDraftState.choose_role)
async def manual_choose_role(callback: types.CallbackQuery, state: FSMContext):
    role = callback.data.replace("role_", "")
    role_display = ROLE_NAMES.get(role, role)
    await state.update_data(user_role=role)
    await state.set_state(ManualDraftState.choose_side)
    await callback.message.edit_text(
        f"✅ Роль выбрана: <b>{role_display}</b>\n\n"
        f"<b>Шаг 2 из 3:</b> Кто ты в этом матче?",
        reply_markup=get_manual_draft_side_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()


@dp.callback_query(F.data.in_({"manual_side_radiant", "manual_side_dire"}), ManualDraftState.choose_side)
async def manual_choose_side(callback: types.CallbackQuery, state: FSMContext):
    user_is_radiant = callback.data == "manual_side_radiant"
    side_text = "Radiant" if user_is_radiant else "Dire"
    await state.update_data(user_is_radiant=user_is_radiant, radiant_picks=[], dire_picks=[])
    await state.set_state(ManualDraftState.picking)

    data = await state.get_data()
    user_role = data.get("user_role", "flex")
    keyboard = get_manual_draft_main_keyboard(user_role, user_is_radiant, 0, 0, True)

    await callback.message.edit_text(
        f"✅ Сторона: <b>{side_text}</b>\n\n"
        f"<b>Шаг 3 из 3:</b> Начинаем драфт!\n"
        f"Ход: ☀️ Radiant (1-й пик)",
        reply_markup=keyboard,
        parse_mode="HTML"
    )
    await callback.answer()


def _get_next_pick_side(radiant_count: int, dire_count: int) -> bool:
    """Определяет, чей следующий пик (True = Radiant) по Captain's Mode порядку"""
    total = radiant_count + dire_count
    # Captain's Mode пики:
    # Pick 1: R D R D R D R D R D (первые 10 пиков = 5 на команду)
    # Порядок: 0=R, 1=D, 2=R, 3=D, 4=R, 5=D, 6=R, 7=D, 8=R, 9=D
    if total < 10:
        return total % 2 == 0  # чётный = Radiant
    # После 10 пиков драфт завершён
    return True


async def _show_manual_draft_state(message: types.Message, state: FSMContext):
    """Показывает текущее состояние ручного драфта"""
    data = await state.get_data()
    user_role = data.get("user_role", "flex")
    user_is_radiant = data.get("user_is_radiant", True)
    radiant_picks = data.get("radiant_picks", [])
    dire_picks = data.get("dire_picks", [])

    radiant_count = len(radiant_picks)
    dire_count = len(dire_picks)
    next_is_radiant = _get_next_pick_side(radiant_count, dire_count)
    total = radiant_count + dire_count

    keyboard = get_manual_draft_main_keyboard(user_role, user_is_radiant, radiant_count, dire_count, next_is_radiant)

    # Формируем совет
    advice = format_manual_draft_advice(
        radiant_picks, dire_picks, user_role, user_is_radiant, next_is_radiant
    )

    try:
        await message.edit_text(advice, parse_mode="HTML", reply_markup=keyboard)
    except Exception as e:
        if "message is not modified" not in str(e):
            await message.answer(advice, parse_mode="HTML", reply_markup=keyboard)


@dp.callback_query(F.data == "manual_add_pick", ManualDraftState.picking)
async def manual_add_pick(callback: types.CallbackQuery, state: FSMContext):
    """Открывает поиск героя для добавления пика"""
    data = await state.get_data()
    user_is_radiant = data.get("user_is_radiant", True)
    radiant_picks = data.get("radiant_picks", [])
    dire_picks = data.get("dire_picks", [])

    keyboard = get_hero_search_keyboard("", 0, user_is_radiant, radiant_picks, dire_picks)
    await callback.message.edit_text(
        "🔍 <b>Выбери героя для пика:</b>\n"
        "Можешь нажать кнопку или начать вводить имя для поиска.",
        reply_markup=keyboard,
        parse_mode="HTML"
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("manual_pick_"), ManualDraftState.picking)
async def manual_process_pick(callback: types.CallbackQuery, state: FSMContext):
    """Обработка выбора героя"""
    hero_name = callback.data.replace("manual_pick_", "")

    data = await state.get_data()
    user_is_radiant = data.get("user_is_radiant", True)
    radiant_picks = data.get("radiant_picks", [])
    dire_picks = data.get("dire_picks", [])

    # Проверяем, не взят ли уже
    if hero_name in radiant_picks or hero_name in dire_picks:
        await callback.answer("❌ Этот герой уже взят!", show_alert=True)
        return

    # Добавляем пик согласно чьей очереди
    radiant_count = len(radiant_picks)
    dire_count = len(dire_picks)
    next_is_radiant = _get_next_pick_side(radiant_count, dire_count)

    if next_is_radiant:
        radiant_picks.append(hero_name)
    else:
        dire_picks.append(hero_name)

    await state.update_data(radiant_picks=radiant_picks, dire_picks=dire_picks)
    await _show_manual_draft_state(callback.message, state)
    await callback.answer(f"✅ {hero_name} добавлен для {'Radiant' if next_is_radiant else 'Dire'}")


@dp.callback_query(F.data.startswith("manual_page_"), ManualDraftState.picking)
async def manual_page_change(callback: types.CallbackQuery, state: FSMContext):
    """Смена страницы в поиске героев"""
    # Формат: manual_page_{page}_{query}
    parts = callback.data.split("_", 3)
    page = int(parts[2])
    query = parts[3] if len(parts) > 3 else ""

    data = await state.get_data()
    user_is_radiant = data.get("user_is_radiant", True)
    radiant_picks = data.get("radiant_picks", [])
    dire_picks = data.get("dire_picks", [])

    keyboard = get_hero_search_keyboard(query, page, user_is_radiant, radiant_picks, dire_picks)
    await callback.message.edit_reply_markup(reply_markup=keyboard)
    await callback.answer()


@dp.callback_query(F.data == "manual_search", ManualDraftState.picking)
async def manual_search_prompt(callback: types.CallbackQuery, state: FSMContext):
    """Запрос поискового запроса (упрощённо — просто показываем первую страницу)"""
    # В inline-режиме полноценный поиск сложен, делаем проще:
    # пользователь может просто листать страницы
    await callback.answer("Листай страницы ⬅️➡️ или введи имя героя в следующем обновлении", show_alert=True)


@dp.callback_query(F.data == "manual_back_to_main", ManualDraftState.picking)
async def manual_back_to_main(callback: types.CallbackQuery, state: FSMContext):
    """Возврат к главной панели драфта"""
    await _show_manual_draft_state(callback.message, state)
    await callback.answer()


@dp.callback_query(F.data == "manual_undo", ManualDraftState.picking)
async def manual_undo(callback: types.CallbackQuery, state: FSMContext):
    """Отмена последнего пика"""
    data = await state.get_data()
    radiant_picks = data.get("radiant_picks", [])
    dire_picks = data.get("dire_picks", [])

    # Удаляем последний пик в порядке Captain's Mode
    total = len(radiant_picks) + len(dire_picks)
    if total == 0:
        await callback.answer("Нечего отменять", show_alert=True)
        return

    # Определяем, чей был последний пик
    last_is_radiant = _get_next_pick_side(
        len(radiant_picks) - (1 if len(radiant_picks) > len(dire_picks) else 0),
        len(dire_picks) - (1 if len(dire_picks) >= len(radiant_picks) else 0)
    )

    if last_is_radiant and radiant_picks:
        removed = radiant_picks.pop()
    elif dire_picks:
        removed = dire_picks.pop()
    else:
        await callback.answer("Ошибка отмены", show_alert=True)
        return

    await state.update_data(radiant_picks=radiant_picks, dire_picks=dire_picks)
    await _show_manual_draft_state(callback.message, state)
    await callback.answer(f"↩️ Отменён пик: {removed}")


@dp.callback_query(F.data == "manual_reset", ManualDraftState.picking)
async def manual_reset(callback: types.CallbackQuery, state: FSMContext):
    """Полный сброс драфта"""
    data = await state.get_data()
    user_role = data.get("user_role", "flex")
    user_is_radiant = data.get("user_is_radiant", True)

    await state.update_data(radiant_picks=[], dire_picks=[])
    await _show_manual_draft_state(callback.message, state)
    await callback.answer("🔄 Драфт сброшен")


@dp.callback_query(F.data == "manual_change_role", ManualDraftState.picking)
async def manual_change_role(callback: types.CallbackQuery, state: FSMContext):
    """Смена роли в процессе драфта"""
    await state.set_state(ManualDraftState.choose_role)
    await callback.message.edit_text(
        "🎭 <b>Смена роли:</b> выбери новую:",
        reply_markup=get_role_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()


@dp.callback_query(F.data == "manual_change_side", ManualDraftState.picking)
async def manual_change_side(callback: types.CallbackQuery, state: FSMContext):
    """Смена стороны в процессе драфта"""
    data = await state.get_data()
    user_is_radiant = not data.get("user_is_radiant", True)
    await state.update_data(user_is_radiant=user_is_radiant)

    side_text = "Radiant" if user_is_radiant else "Dire"
    await _show_manual_draft_state(callback.message, state)
    await callback.answer(f"🔀 Сторона изменена: {side_text}")


@dp.callback_query(F.data == "manual_stop", ManualDraftState.picking)
async def manual_stop(callback: types.CallbackQuery, state: FSMContext):
    """Завершение ручного драфта"""
    await state.clear()
    await callback.message.edit_text(
        "⏹ <b>Ручной драфт завершён</b>\n\n"
        "Можешь начать новый командой /draft_manual",
        parse_mode="HTML"
    )
    await callback.answer()


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