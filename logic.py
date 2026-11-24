import os
import re
import requests
import time
from dotenv import load_dotenv

load_dotenv()

STEAM_API_KEY = os.getenv("STEAM_API_KEY")
STRATZ_TOKEN = os.getenv("STRATZ_TOKEN")

# Роль -> STRATZ position enum
ROLE_TO_POSITION = {
    "carry": "POSITION_1",
    "mid": "POSITION_2",
    "offlane": "POSITION_3",
    "support": "POSITION_4",
    "hard_support": "POSITION_5",
    "flex": None,
    "any": None,
}

# Роль -> читаемое название
ROLE_NAMES = {
    "carry": "Кэрри (1-я позиция)",
    "mid": "Мид (2-я позиция)",
    "offlane": "Оффлайн (3-я позиция)",
    "support": "Саппорт (4-я позиция)",
    "hard_support": "Хард саппорт (5-я позиция)",
    "flex": "Флекс",
    "any": "Любая роль",
}


def get_account_id(steam_url: str):
    steam_url = steam_url.strip().rstrip('/')

    match_profiles = re.search(r'steamcommunity\.com/profiles/(\d+)', steam_url)
    if match_profiles:
        steamid64 = int(match_profiles.group(1))
        return steamid64 - 76561197960265728

    match_id = re.search(r'steamcommunity\.com/id/([a-zA-Z0-9_-]+)', steam_url)
    if match_id:
        vanity_url = match_id.group(1)
        api_url = f"http://api.steampowered.com/ISteamUser/ResolveVanityURL/v0001/?key={STEAM_API_KEY}&vanityurl={vanity_url}"
        try:
            response = requests.get(api_url, timeout=10).json()
            if response.get("response", {}).get("success") == 1:
                steamid64 = int(response["response"]["steamid"])
                return steamid64 - 76561197960265728
        except Exception as e:
            print(f"DEBUG: Ошибка Steam API: {e}", flush=True)
    return None


def stratz_request(query, variables):
    url = "https://api.stratz.com/graphql"
    headers = {
        "Authorization": f"Bearer {STRATZ_TOKEN}",
        "Content-Type": "application/json",
        "User-Agent": "STRATZ_API"
    }
    try:
        response = requests.post(url, json={"query": query, "variables": variables}, headers=headers, timeout=15)
        if response.status_code == 200:
            data = response.json()
            if "errors" in data:
                print(f"DEBUG: Ошибка GraphQL: {data['errors']}", flush=True)
                return None
            return data.get("data")
        else:
            print(f"DEBUG: STRATZ {response.status_code}: {response.text[:200]}", flush=True)
    except Exception as e:
        print(f"DEBUG: Ошибка запроса к STRATZ: {e}", flush=True)
    return None


def opendota_get(url, params=None):
    try:
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code == 200:
            return resp.json()
        else:
            print(f"DEBUG: OpenDota {resp.status_code}: {resp.text[:200]}", flush=True)
    except Exception as e:
        print(f"DEBUG: Ошибка OpenDota: {e}", flush=True)
    return None


def get_player_info(account_id: int):
    result = {}

    # 1. Профиль + MMR/ранг + behaviour — только STRATZ
    q_profile = """
    query GetPlayer($id: Long!) {
      player(steamAccountId: $id) {
        steamAccount { name }
        matchCount
        winCount
        behaviorScore
        ranks { seasonRankId }
      }
    }
    """
    data = stratz_request(q_profile, {"id": account_id})
    if not data or not data.get("player"):
        return None
    player = data["player"]
    result["steamAccount"] = player["steamAccount"]
    result["matchCount"] = player["matchCount"]
    result["winCount"] = player["winCount"]
    result["behaviorScore"] = player.get("behaviorScore")
    ranks = player.get("ranks") or []
    result["seasonRank"] = ranks[0].get("seasonRankId") if ranks else None

    # 2. Топ-3 героя — OpenDota (только ПУБЛИЧНЫЕ матчи)
    heroes = opendota_get(f"https://api.opendota.com/api/players/{account_id}/heroes")
    if heroes:
        public_total = sum(h.get("games", 0) for h in heroes)
        result["publicMatchesTotal"] = public_total
        result["hiddenMatches"] = max(0, player["matchCount"] - public_total)
        # Сортируем по количеству игр DESC, берём топ-3
        heroes_sorted = sorted(heroes, key=lambda h: h.get("games", 0), reverse=True)[:3]
        result["heroesPerformance"] = [
            {
                "heroId": h["hero_id"],
                "matchCount": h["games"],
                "winCount": h["win"]
            }
            for h in heroes_sorted
        ]
        print(f"[OpenDota] top-3 heroes: {[(h['hero_id'], h['games'], round(h['win']/h['games']*100,1)) for h in heroes_sorted]}", flush=True)
        print(f"[OpenDota] public matches: {public_total}, total: {player['matchCount']}, hidden: {result['hiddenMatches']}", flush=True)
    else:
        result["heroesPerformance"] = []
        result["publicMatchesTotal"] = 0
        result["hiddenMatches"] = player["matchCount"]
        print("[OpenDota] heroes: empty", flush=True)

    # 3. Последние 20 матчей для формы — OpenDota
    matches = opendota_get(
        f"https://api.opendota.com/api/players/{account_id}/matches",
        params={"limit": 20}
    )
    if matches:
        # OpenDota: player_slot < 128 = Radiant, radiant_win = победа Radiant
        # Приводим к формату, который ждёт app.py: [{id, players: [{isVictory}]}]
        result["matches"] = []
        for m in matches:
            is_radiant = m.get("player_slot", 0) < 128
            radiant_win = m.get("radiant_win", False)
            is_victory = (radiant_win and is_radiant) or (not radiant_win and not is_radiant)
            result["matches"].append({
                "id": m["match_id"],
                "didRadiantWin": radiant_win,
                "players": [{
                    "steamAccountId": account_id,
                    "heroId": m["hero_id"],
                    "isVictory": is_victory,
                    "isRadiant": is_radiant
                }]
            })
        print(f"[OpenDota] matches for form: {len(result['matches'])}", flush=True)
    else:
        result["matches"] = []
        print("[OpenDota] matches: empty", flush=True)

    return result


# ==================== DRAFT ASSISTANT ====================

# Hero ID -> Name mapping (will be populated from STRATZ)
HERO_NAMES_DRAFT = {}

def get_hero_names():
    """Получает список всех героев с ID и именами из STRATZ"""
    global HERO_NAMES_DRAFT
    if HERO_NAMES_DRAFT:
        return HERO_NAMES_DRAFT

    q = """
    query {
      constants {
        heroes {
          id
          displayName
          shortName
        }
      }
    }
    """
    data = stratz_request(q, {})
    if data and data.get("constants", {}).get("heroes"):
        for hero in data["constants"]["heroes"]:
            HERO_NAMES_DRAFT[hero["id"]] = hero["displayName"]
    return HERO_NAMES_DRAFT


def get_hero_name(hero_id: int) -> str:
    """Получает имя героя по ID"""
    names = get_hero_names()
    return names.get(hero_id, f"Hero {hero_id}")


def get_draft_state(match_id: int):
    """Получает текущее состояние драфта по match ID"""
    q = """
    query GetDraft($id: Long!) {
      match(id: $id) {
        id
        didRadiantWin
        startDateTime
        gameMode
        lobbyType
        pickBans {
          isPick
          heroId
          isRadiant
          order
          wasBannedSuccessfully
        }
        players {
          heroId
          isRadiant
          steamAccountId
        }
      }
    }
    """
    data = stratz_request(q, {"id": match_id})
    if not data or not data.get("match"):
        return None
    return data["match"]


def is_match_live(draft_state: dict) -> bool:
    """Определяет, идет ли матч в реальном времени"""
    if not draft_state:
        return False
    # Если матч не начался (нет времени старта) или еще идет
    # В STRATZ: если didRadiantWin is None и startDateTime есть - матч в процессе
    # Если didRadiantWin is not None - матч завершен
    did_radiant_win = draft_state.get("didRadiantWin")
    start_date = draft_state.get("startDateTime")

    # Матч в реальном времени если: есть дата старта, но результат еще не определен
    if start_date and did_radiant_win is None:
        return True
    return False


def get_match_date_str(draft_state: dict) -> str:
    """Возвращает строку с датой матча"""
    if not draft_state:
        return "Неизвестно"
    start_date = draft_state.get("startDateTime")
    if not start_date:
        return "Дата неизвестна"
    try:
        from datetime import datetime
        dt = datetime.fromtimestamp(start_date)
        return dt.strftime("%d.%m.%Y %H:%M")
    except:
        return "Неверная дата"


def get_counterpicks(enemy_hero_id: int, role: str = None, take: int = 10):
    """
    Получает контрпики против указанного героя.
    role: 'carry', 'mid', 'offlane', 'support', 'hard_support', 'flex', 'any'
    """
    position = ROLE_TO_POSITION.get(role)

    # Используем matchUp для получения винрейтов против героя
    # heroId2 - это контрпик, winRateHeroId2 - его винрейт против heroId1 (enemy)
    q = """
    query GetCounterpicks($heroId: Int!, $take: Int!) {
      heroStats {
        matchUp(heroId: $heroId, take: $take) {
          vs {
            heroId1
            heroId2
            matchCountVs
            winCountVs
            winRateHeroId1
            winRateHeroId2
            synergy
          }
        }
      }
    }
    """
    data = stratz_request(q, {"heroId": enemy_hero_id, "take": take * 3})
    if not data or not data.get("heroStats", {}).get("matchUp"):
        return []

    results = []
    for matchup in data["heroStats"]["matchUp"].get("vs", []):
        # heroId1 = enemy hero (queried), heroId2 = counter hero
        # winRateHeroId2 = win rate of heroId2 AGAINST heroId1
        hero_id = matchup.get("heroId2")
        win_rate = matchup.get("winRateHeroId2", 0)
        match_count = matchup.get("matchCountVs", 0)
        win_count = matchup.get("winCountVs", 0)
        synergy = matchup.get("synergy", 0)

        if hero_id and match_count >= 20:  # минимум 20 матчей для статистики
            results.append({
                "heroId": hero_id,
                "heroName": get_hero_name(hero_id),
                "winRate": round(win_rate * 100, 1),
                "matchCount": match_count,
                "winCount": win_count,
                "synergy": round(synergy, 2)
            })

    # Фильтруем по роли если указана (через отдельный запрос winRate по позиции)
    # Пока просто сортируем по винрейту
    results.sort(key=lambda x: x["winRate"], reverse=True)
    return results[:take]


def get_item_builds(hero_id: int, role: str = None):
    """
    Получает рекомендуемые предметы для героя на конкретной роли.
    role: 'carry', 'mid', 'offlane', 'support', 'hard_support'
    """
    position = ROLE_TO_POSITION.get(role)
    positions = [position] if position else ["POSITION_1", "POSITION_2", "POSITION_3", "POSITION_4", "POSITION_5"]

    q = """
    query GetItemBuilds($heroId: Int!, $positionIds: [MatchPlayerPositionType]!) {
      heroStats {
        itemFullPurchase(heroId: $heroId, positionIds: $positionIds) {
          itemId
          matchCount
          winCount
          winsAverage
        }
      }
    }
    """
    # Запрашиваем по каждой позиции отдельно и объединяем
    all_items = {}
    for pos in positions:
        data = stratz_request(q, {"heroId": hero_id, "positionIds": [pos]})
        if data and data.get("heroStats", {}).get("itemFullPurchase"):
            for item in data["heroStats"]["itemFullPurchase"]:
                item_id = item.get("itemId")
                mc = item.get("matchCount", 0)
                wc = item.get("winCount", 0)
                wa = item.get("winsAverage", 0)
                if item_id and mc >= 50:  # минимум 50 покупок
                    if item_id not in all_items or all_items[item_id]["matchCount"] < mc:
                        all_items[item_id] = {
                            "itemId": item_id,
                            "matchCount": mc,
                            "winCount": wc,
                            "winRate": round((wc / mc * 100) if mc > 0 else 0, 1)
                        }

    # Сортируем по винрейту и популярности
    items = list(all_items.values())
    items.sort(key=lambda x: (x["winRate"], x["matchCount"]), reverse=True)
    return items[:15]  # топ-15 предметов


# Item ID -> Name mapping (will be populated from STRATZ)
ITEM_NAMES_DRAFT = {}

def get_item_names():
    """Получает список всех предметов с ID и именами из STRATZ"""
    global ITEM_NAMES_DRAFT
    if ITEM_NAMES_DRAFT:
        return ITEM_NAMES_DRAFT

    q = """
    query {
      constants {
        items {
          id
          displayName
        }
      }
    }
    """
    data = stratz_request(q, {})
    if data and data.get("constants", {}).get("items"):
        for item in data["constants"]["items"]:
            ITEM_NAMES_DRAFT[item["id"]] = item["displayName"]
    return ITEM_NAMES_DRAFT


def get_item_name(item_id: int) -> str:
    """Получает название предмета по ID"""
    names = get_item_names()
    return names.get(item_id, f"Item {item_id}")


def calculate_win_probability(draft_state: dict, user_role: str = None):
    """
    Вычисляет вероятность победы на основе текущего драфта.
    draft_state: результат get_draft_state
    user_role: 'carry', 'mid', 'offlane', 'support', 'hard_support', 'flex', 'any'
    """
    if not draft_state:
        return 50.0

    pick_bans = draft_state.get("pickBans", [])
    players = draft_state.get("players", [])

    # Определяем команду пользователя (пока простая эвристика)
    # В реальности нужно знать, за какую сторону играет пользователь
    # Пока возвращаем базовый расчет

    radiant_heroes = []
    dire_heroes = []

    for pb in pick_bans:
        if pb.get("isPick") and pb.get("heroId"):
            if pb.get("isRadiant"):
                radiant_heroes.append(pb["heroId"])
            else:
                dire_heroes.append(pb["heroId"])

    # Простая формула: сумма винрейтов пиков каждой команды
    # (в будущем добавить контрпики, синергию, мета)
    def team_strength(heroes):
        total = 0
        for h in heroes:
            # Можно добавить базовый винрейт героя из мета
            total += 50  # заглушка
        return total

    rad_str = team_strength(radiant_heroes)
    dire_str = team_strength(dire_heroes)

    if rad_str + dire_str == 0:
        return 50.0

    return round(rad_str / (rad_str + dire_str) * 100, 1)


def format_draft_advice(draft_state: dict, user_role: str, user_team_is_radiant: bool = None, is_live: bool = True):
    """
    Формирует текстовый совет для текущего хода.
    is_live: True - матч в реальном времени (показывать советы), False - исторический матч (только анализ)
    """
    if not draft_state:
        return "Не удалось получить состояние драфта."

    pick_bans = draft_state.get("pickBans", [])
    if not pick_bans:
        return "Драфт ещё не начался."

    # Определяем статус матча
    from logic import is_match_live, get_match_date_str
    match_is_live = is_match_live(draft_state)
    match_date = get_match_date_str(draft_state)
    match_id = draft_state.get('id', '?')

    # Упрощенно: считаем количество пиков/банов каждой команды
    radiant_picks_count = sum(1 for pb in pick_bans if pb.get("isPick") and pb.get("isRadiant"))
    dire_picks_count = sum(1 for pb in pick_bans if pb.get("isPick") and not pb.get("isRadiant"))
    radiant_bans_count = sum(1 for pb in pick_bans if not pb.get("isPick") and pb.get("isRadiant"))
    dire_bans_count = sum(1 for pb in pick_bans if not pb.get("isPick") and not pb.get("isRadiant"))

    total_picks = radiant_picks_count + dire_picks_count
    total_bans = radiant_bans_count + dire_bans_count

    # Определяем всех пикнутых героев с порядком
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

    # Сортируем по порядку пика
    radiant_picks.sort(key=lambda x: x[0])
    dire_picks.sort(key=lambda x: x[0])

    # Captain's Mode порядок
    # Определяем фазу
    if total_bans < 8:
        phase = "bans_1"
    elif total_picks < 5:
        phase = "picks_1"
    elif total_bans < 10:
        phase = "bans_2"
    elif total_picks < 10:
        phase = "picks_2"
    else:
        phase = "done"

    # Определяем, чей ход
    def whose_turn(picks_done, bans_done):
        if bans_done < 8:
            return "Radiant" if bans_done % 2 == 0 else "Dire"
        elif picks_done < 5:
            return "Radiant" if picks_done % 2 == 0 else "Dire"
        elif bans_done < 10:
            return "Radiant" if bans_done % 2 == 0 else "Dire"
        elif picks_done < 10:
            second_pick_idx = picks_done - 5
            return "Dire" if second_pick_idx % 2 == 0 else "Radiant"
        return "Done"

    current_turn = whose_turn(total_picks, total_bans)
    user_is_radiant = user_team_is_radiant if user_team_is_radiant is not None else False
    is_our_turn = (current_turn == "Radiant" and user_is_radiant) or (current_turn == "Dire" and not user_is_radiant)

    # Находим последний пик врага для контрпиков
    enemy_last_pick = None
    for pb in reversed(pick_bans):
        if pb.get("isPick") and pb.get("heroId"):
            pb_is_radiant = pb.get("isRadiant", False)
            if user_team_is_radiant is not None:
                if pb_is_radiant != user_team_is_radiant:
                    enemy_last_pick = pb
                    break
            else:
                enemy_last_pick = pb
                break

    # Получаем контрпики против последнего пика врага
    counterpicks = []
    counter_target_name = ""
    if enemy_last_pick:
        enemy_hero_id = enemy_last_pick["heroId"]
        counter_target_name = get_hero_name(enemy_hero_id)
        counterpicks = get_counterpicks(enemy_hero_id, user_role)

    # Находим наш пикнутый герой
    our_hero_id = None
    for pb in pick_bans:
        if pb.get("isPick") and pb.get("heroId"):
            pb_is_radiant = pb.get("isRadiant", False)
            if user_team_is_radiant is not None:
                if pb_is_radiant == user_is_radiant:
                    our_hero_id = pb["heroId"]
                    break

    # Предметы для нашего героя или лучшего контрпика
    items_hero_id = our_hero_id if our_hero_id else (counterpicks[0]["heroId"] if counterpicks else enemy_last_pick["heroId"] if enemy_last_pick else None)
    items = get_item_builds(items_hero_id, user_role) if items_hero_id else []

    # ========== ФОРМИРУЕМ ОТВЕТ ==========
    role_name = ROLE_NAMES.get(user_role, user_role)
    team_emoji = "☀️" if user_is_radiant else "🌙"
    team_name = "Radiant" if user_is_radiant else "Dire"

    # Заголовок с инфо о матче
    if match_is_live:
        text = f"🔴 **LIVE МАТЧ** #{match_id} | Ты: {team_emoji} **{team_name}** ({role_name})\n"
        text += f"{'🟢 **ТВОЙ ХОД**' if is_our_turn else '🔴 Ход противника'} | Фаза: {phase.replace('_', ' ').title()}\n\n"
    else:
        text = f"📜 **ИСТОРИЧЕСКИЙ МАТЧ** #{match_id} | {match_date}\n"
        text += f"Ты: {team_emoji} **{team_name}** ({role_name}) | Результат: "
        did_radiant_win = draft_state.get("didRadiantWin")
        if did_radiant_win is True:
            text += "☀️ Radiant победа"
        elif did_radiant_win is False:
            text += "🌙 Dire победа"
        else:
            text += "Неизвестно"
        text += "\n\n"

    # Показываем все пики
    text += "📋 **ДРАФТ:**\n"
    text += "☀️ **Radiant:**\n"
    if radiant_picks:
        for order, name in radiant_picks:
            marker = " ⭐" if (user_is_radiant and our_hero_id and get_hero_name(our_hero_id) == name) else ""
            text += f"  {order}. {name}{marker}\n"
    else:
        text += "  (пусто)\n"
    if radiant_bans:
        text += f"  🚫 Баны: {', '.join(radiant_bans)}\n"

    text += "\n🌙 **Dire:**\n"
    if dire_picks:
        for order, name in dire_picks:
            marker = " ⭐" if (not user_is_radiant and our_hero_id and get_hero_name(our_hero_id) == name) else ""
            text += f"  {order}. {name}{marker}\n"
    else:
        text += "  (пусто)\n"
    if dire_bans:
        text += f"  🚫 Баны: {', '.join(dire_bans)}\n"

    text += "\n" + "─" * 30 + "\n"

    # Для исторических матчей - только пост-анализ
    if not match_is_live:
        text += "📊 **ПОСТ-АНАЛИЗ ДРАФТА:**\n\n"

        # Анализ контрпиков для каждого вражеского героя
        enemy_heroes = [name for _, name in dire_picks] if user_is_radiant else [name for _, name in radiant_picks]
        if enemy_heroes:
            text += "🎯 **Контрпики против врагов:**\n"
            for enemy_hero in enemy_heroes[:5]:
                enemy_id = None
                for k, v in get_hero_names().items():
                    if v.lower() == enemy_hero.lower():
                        enemy_id = k
                        break
                if enemy_id:
                    enemy_counters = get_counterpicks(enemy_id, user_role)
                    if enemy_counters:
                        top = enemy_counters[0]
                        text += f"  vs {enemy_hero}: **{top['heroName']}** ({top['winRate']}% WR)\n"
                    else:
                        text += f"  vs {enemy_hero}: данных нет\n"
            text += "\n"

        # Геймплан
        gameplan = get_gameplan_for_role(user_role, counter_target_name, radiant_picks, dire_picks, user_is_radiant)
        text += f"📝 **Геймплан ({role_name}):**\n{gameplan}\n\n"

        # Ключевые тайминги
        timings = get_key_timings(user_role, items_hero_id)
        if timings:
            text += f"⏰ **Ключевые тайминги:**\n{timings}\n\n"

        # Прогноз (ретроспективный)
        win_prob = calculate_win_probability(draft_state, user_role)
        text += f"📊 **Ретроспективный прогноз:** ~{win_prob}%\n"

        # Итог
        actual_result = ""
        if did_radiant_win is not None:
            user_won = (did_radiant_win and user_is_radiant) or (not did_radiant_win and not user_is_radiant)
            actual_result = "✅ ПОБЕДА" if user_won else "❌ ПОРАЖЕНИЕ"
            text += f"\n{actual_result} (реальный результат)"

        return text

    # Для лайв матчей - показываем советы если наш ход
    if is_our_turn and total_picks < 10:
        text += "🧠 **АНАЛИЗ И ПЛАН:**\n\n"

        # 1. Контрпики
        if counterpicks:
            text += f"🎯 **Контрпики против {counter_target_name}:**\n"
            for i, cp in enumerate(counterpicks[:4], 1):
                synergy_emoji = "🤝" if cp.get("synergy", 0) > 0.1 else ("⚔️" if cp.get("synergy", 0) < -0.1 else "")
                text += f"  {i}. **{cp['heroName']}** — {cp['winRate']}% WR ({cp['matchCount']} игр) {synergy_emoji}\n"
        else:
            text += f"🎯 Контрпики против {counter_target_name}: данных недостаточно\n"

        text += "\n"

        # 2. Предметы
        if items:
            hero_for_items = get_hero_name(items_hero_id) if items_hero_id else "твоего героя"
            text += f"🛡 **Кор айтемы на {hero_for_items}:**\n"
            core_items = [i for i in items if i["matchCount"] > 200][:3]
            situational = [i for i in items if i["matchCount"] <= 200][:3]
            if core_items:
                text += "  Кор: " + ", ".join([f"**{get_item_name(i['itemId'])}** ({i['winRate']}% WR)" for i in core_items]) + "\n"
            if situational:
                text += "  Ситуативные: " + ", ".join([f"**{get_item_name(i['itemId'])}** ({i['winRate']}% WR)" for i in situational]) + "\n"
        text += "\n"

        # 3. Геймплан по роли
        gameplan = get_gameplan_for_role(user_role, counter_target_name, radiant_picks, dire_picks, user_is_radiant)
        text += f"📝 **Геймплан ({role_name}):**\n{gameplan}\n"

        # 4. Ключевые тайминги/уровни
        timings = get_key_timings(user_role, items_hero_id)
        if timings:
            text += f"⏰ **Ключевые тайминги:**\n{timings}\n"

    elif total_picks >= 10:
        text += "✅ **ДРАФТ ЗАВЕРШЕН!** Матч скоро начнется.\n\n"
        win_prob = calculate_win_probability(draft_state, user_role)
        text += f"📊 Прогноз победы: ~{win_prob}%\n\n"
        text += "Удачи в игре! 🎮"

    else:
        text += f"⏳ Ожидаем пик {current_turn}...\n"
        if enemy_last_pick:
            text += f"Последний пик врага: **{counter_target_name}**\n"

    return text


def get_gameplan_for_role(role: str, enemy_last_pick: str, radiant_picks: list, dire_picks: list, user_is_radiant: bool) -> str:
    """Возвращает геймплан в зависимости от роли"""
    plans = {
        "carry": (
            "• **Early (0-10 мин):** Фокус на ласте, безопасный фарм, не умирай.\n"
            "• **Mid (10-20 мин):** Первый большой айтем (Battle Fury / Maelstrom / Yasha), фарм джунглей между волнами.\n"
            "• **Late (20+ мин):** Вступай в файты ПОСЛЕ инита, таргет приоритет — их бэклайн.\n"
            "• **vs " + enemy_last_pick + ":** Избегай 1v1 ранним, фарми под башней, жди поддержку."
        ),
        "mid": (
            "• **Early (0-10 мин):** Контроллируй руну, дави линию, ищи килл на 6 лвл.\n"
            "• **Mid (10-20 мин):** Роам на сайдлайны после пуша, фарм эншеинт/треугольник.\n"
            "• **Late (20+ мин):** Инициируй файты, таргет — ключевые враги (supports/carry).\n"
            "• **vs " + enemy_last_pick + ":** Играй за контрлиния, пуш и роам, не давай фармить свободно."
        ),
        "offlane": (
            "• **Early (0-10 мин):** Выживание > фарм. Пулл хардкемп, забирай опыт.\n"
            "• **Mid (10-20 мин):** Активность по карте — смоки, тауэр дайвы, создание пространства.\n"
            "• **Late (20+ мин):** Танк/инициатор — вступай первым, задизайбливай их кари.\n"
            "• **vs " + enemy_last_pick + ":** Не лезь в трейды ранним, юзай фог/треесы для опыта."
        ),
        "support": (
            "• **Early (0-10 мин):** Защита кари, пуллы, стаки, заворды для visión.\n"
            "• **Mid (10-20 мин):** Роам с мидом, смоки, контроль Рошана,deep wards.\n"
            "• **Late (20+ мин):** Сейвим кари, инициация/контр-инициация, айтемы для команды (Glimmer, Force, Lotus).\n"
            "• **vs " + enemy_last_pick + ":** Фокус на зонинге их оффлейнера, дай кари свободный фарм."
        ),
        "hard_support": (
            "• **Early (0-10 мин):** Макс заворды, пуллы, забирай только опыт. Не тач крипов!\n"
            "• **Mid (10-20 мин):** Полный контроль мапы — обс на хайграунды, смоки каждые 2-3 мин.\n"
            "• **Late (20+ мин):** Активные айтемы (Glimmer, Force, Lotus, Aeon), сейвы, дисейблы.\n"
            "• **vs " + enemy_last_pick + ":** Приоритет — вижн и защита кари. Не умирай зря за варды."
        ),
        "flex": (
            "• Адаптируйся под драфт: если нужен дамаг — билди дамаг, если танк — танк.\n"
            "• Коммуницируй с командой: кто инициирует, кто фармит, кто сейвит.\n"
            "• **vs " + enemy_last_pick + ":** Выбирай героя, который закрывает их пики."
        )
    }
    return plans.get(role, "Играй умно, смотри за минимапом, не умирай зря!")


def get_key_timings(role: str, hero_id: int = None) -> str:
    """Ключевые тайминги/уровни для роли"""
    base = {
        "carry": "• 6 лвл — первый пауэрспайк (ульт/ключевой скилл)\n• 12-15 мин — первый большой айтем\n• 20-25 мин — 2-3 слота, активные файты\n• 30+ мин — 5-6 слотов, хайграунд дайвы",
        "mid": "• 6 лвл — ульт, поиск киллов\n• 10-12 мин — первый коре айтем (Blink/Eul/Orchid)\n• 15-18 мин — 2 айтема, контроль Рошана\n• 25+ мин — 4+ слотов, решающие файты",
        "offlane": "• 6 лвл — ульт, тауэр дайв потенциал\n• 12-15 мин — Blink/Force/BKB, создание пространства\n• 20 мин — 2-3 айтема, инициирование файтов\n• 30+ мин — танк/ауры, защита кари",
        "support": "• 6 лвл — ульт/ключевой скилл для командных файтов\n• 10-12 мин — Boots + первый активный айтем\n• 15-18 мин — Force/Glimmer/Lotus, контроль Рошана\n• 25+ мин — 3-4 активных айтема для команды",
        "hard_support": "• 6 лвл — ульт для командных файтов\n• 12-15 мин — Tranquil/Greaves + Glimmer/Force\n• 20 мин — Lotus/Aeon/Refresher, полная вижн-контроль\n• 30+ мин — 4+ активных айтема, спасение кари"
    }
    return base.get(role, "")