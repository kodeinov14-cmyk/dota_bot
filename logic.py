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


def get_counterpicks(enemy_hero_id: int, role: str = None, take: int = 10):
    """
    Получает контрпики против указанного героя.
    role: 'carry', 'mid', 'offlane', 'support', 'hard_support', 'flex', 'any'
    """
    position = ROLE_TO_POSITION.get(role)

    # Используем matchUp для получения винрейтов против героя
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
    data = stratz_request(q, {"heroId": enemy_hero_id, "take": take * 2})
    if not data or not data.get("heroStats", {}).get("matchUp"):
        return []

    results = []
    for matchup in data["heroStats"]["matchUp"].get("vs", []):
        # heroId1 - это наш герой, heroId2 - враг (или наоборот, смотрим synergy)
        # winRateHeroId1 - винрейт heroId1 против heroId2
        hero_id = matchup.get("heroId1")
        win_rate = matchup.get("winRateHeroId1", 0)
        match_count = matchup.get("matchCountVs", 0)
        win_count = matchup.get("winCountVs", 0)
        synergy = matchup.get("synergy", 0)

        if hero_id and match_count >= 10:  # минимум 10 матчей для статистики
            results.append({
                "heroId": hero_id,
                "heroName": get_hero_name(hero_id),
                "winRate": round(win_rate * 100, 1),
                "matchCount": match_count,
                "winCount": win_count,
                "synergy": round(synergy, 2)
            })

    # Сортируем по винрейту DESC
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


def format_draft_advice(draft_state: dict, user_role: str, user_team_is_radiant: bool = None):
    """
    Формирует текстовый совет для текущего хода.
    """
    if not draft_state:
        return "Не удалось получить состояние драфта."

    pick_bans = draft_state.get("pickBans", [])
    if not pick_bans:
        return "Драфт ещё не начался."

    # Находим последний пик/бан
    last_action = pick_bans[-1]

    # Определяем, чей ход сейчас
    # Порядок драфта в Доте 2 (Captain's Mode):
    # Ban 1-2-3-4-5-6-7-8-9-10 (Radiant, Dire, Radiant, Dire...)
    # Pick 1-2-3-4-5 (Radiant, Dire, Radiant, Dire, Radiant)
    # Ban 11-12 (Radiant, Dire)
    # Pick 6-7-8-9-10 (Dire, Radiant, Dire, Radiant, Dire)

    # Упрощенно: считаем количество пиков каждой команды
    radiant_picks = sum(1 for pb in pick_bans if pb.get("isPick") and pb.get("isRadiant"))
    dire_picks = sum(1 for pb in pick_bans if pb.get("isPick") and not pb.get("isRadiant"))
    radiant_bans = sum(1 for pb in pick_bans if not pb.get("isPick") and pb.get("isRadiant"))
    dire_bans = sum(1 for pb in pick_bans if not pb.get("isPick") and not pb.get("isRadiant"))

    total_actions = len(pick_bans)

    # Определяем, чей ход (упрощенно)
    # В Captain's Mode порядок фиксированный
    # Можно использовать order поле

    # Определяем вражеский последний пик для контрпиков
    enemy_last_pick = None
    for pb in reversed(pick_bans):
        if pb.get("isPick") and pb.get("heroId"):
            # Если это пик врага (не нашей команды)
            # Пока просто берем последний пик врага
            if user_team_is_radiant is not None:
                if pb.get("isRadiant") != user_team_is_radiant:
                    enemy_last_pick = pb
                    break
            else:
                # Если не знаем команду, берем последний пик
                enemy_last_pick = pb
                break

    if not enemy_last_pick:
        return "Ждём первый пик врага..."

    enemy_hero_id = enemy_last_pick["heroId"]
    enemy_hero_name = get_hero_name(enemy_hero_id)

    # Получаем контрпики
    counterpicks = get_counterpicks(enemy_hero_id, user_role)

    # Получаем предметы против этого героя
    items = get_item_builds(enemy_hero_id, user_role)

    # Формируем ответ
    role_name = ROLE_NAMES.get(user_role, user_role)

    text = f"🟢 **ТВОЙ ХОД** ({role_name})\n\n"
    text += f"🔴 Противник взял: **{enemy_hero_name}**\n\n"

    if counterpicks:
        text += "🎯 **Лучшие контрпики:**\n"
        for i, cp in enumerate(counterpicks[:3], 1):
            text += f"{i}. **{cp['heroName']}** — {cp['winRate']}% WR ({cp['matchCount']} игр)\n"
    else:
        text += "🎯 Контрпики: данные недоступны\n"

    if items:
        text += f"\n🛡 **Кор айтемы против {enemy_hero_name}:**\n"
        # Группируем по типам (базовые/кор/люкс) - пока просто топ-5
        core_items = [i for i in items if i["matchCount"] > 200][:3]
        situational = [i for i in items if i["matchCount"] <= 200][:3]
        if core_items:
            text += "Кор: " + ", ".join([f"**{get_item_name(i['itemId'])}** ({i['winRate']}% WR)" for i in core_items]) + "\n"
        if situational:
            text += "Ситуативные: " + ", ".join([f"**{get_item_name(i['itemId'])}** ({i['winRate']}% WR)" for i in situational]) + "\n"

    # Прогноз победы
    win_prob = calculate_win_probability(draft_state, user_role)
    text += f"\n📊 **Шанс победы: ~{win_prob}%**"

    return text