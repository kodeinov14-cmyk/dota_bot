import os
import re
import requests
import time
import logging
import asyncio
from datetime import datetime
from typing import Optional, Dict, List, Any, Tuple
from dotenv import load_dotenv

load_dotenv()

STEAM_API_KEY = os.getenv("STEAM_API_KEY")
STRATZ_TOKEN = os.getenv("STRATZ_TOKEN")

# ==================== LOGGING SETUP ====================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("draft_bot")

# ==================== CONSTANTS ====================
ROLE_TO_POSITION: Dict[str, Optional[str]] = {
    "carry": "POSITION_1",
    "mid": "POSITION_2",
    "offlane": "POSITION_3",
    "support": "POSITION_4",
    "hard_support": "POSITION_5",
    "flex": None,
    "any": None,
}

ROLE_NAMES: Dict[str, str] = {
    "carry": "Кэрри (1-я позиция)",
    "mid": "Мид (2-я позиция)",
    "offlane": "Оффлайн (3-я позиция)",
    "support": "Саппорт (4-я позиция)",
    "hard_support": "Хард саппорт (5-я позиция)",
    "flex": "Флекс",
    "any": "Любая роль",
}

# OpenDota rate limit: 60 requests/minute
OPENDOTA_RATE_LIMIT = 60
_opendota_semaphore: Optional[asyncio.Semaphore] = None
_opendota_last_reset = time.time()
_opendota_request_count = 0


def get_opendota_semaphore() -> asyncio.Semaphore:
    """Возвращает семафор для rate limiting OpenDota (ленивая инициализация)"""
    global _opendota_semaphore
    if _opendota_semaphore is None:
        _opendota_semaphore = asyncio.Semaphore(OPENDOTA_RATE_LIMIT)
    return _opendota_semaphore


async def opendota_rate_limit():
    """Простой rate limiter для OpenDota (60 req/min)"""
    global _opendota_last_reset, _opendota_request_count
    now = time.time()
    if now - _opendota_last_reset >= 60:
        _opendota_last_reset = now
        _opendota_request_count = 0
    _opendota_request_count += 1
    if _opendota_request_count > OPENDOTA_RATE_LIMIT:
        wait_time = 60 - (now - _opendota_last_reset)
        if wait_time > 0:
            logger.warning(f"OpenDota rate limit reached, waiting {wait_time:.1f}s")
            await asyncio.sleep(wait_time)
        _opendota_last_reset = time.time()
        _opendota_request_count = 1


# ==================== UTILS ====================
def get_account_id(steam_url: str) -> Optional[int]:
    """Извлекает account_id из Steam URL"""
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
            logger.error(f"Steam API error: {e}")
    return None


# ==================== STRATZ API ====================
def stratz_request(query: str, variables: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Выполняет GraphQL запрос к STRATZ API"""
    url = "https://api.stratz.com/graphql"
    headers = {
        "Authorization": f"Bearer {STRATZ_TOKEN}",
        "Content-Type": "application/json",
        "User-Agent": "STRATZ_API",
    }
    try:
        response = requests.post(url, json={"query": query, "variables": variables}, headers=headers, timeout=15)
        if response.status_code == 200:
            data = response.json()
            if "errors" in data:
                logger.error(f"STRATZ GraphQL errors: {data['errors']}")
                return None
            return data.get("data")
        else:
            logger.warning(f"STRATZ HTTP {response.status_code}: {response.text[:200]}")
    except Exception as e:
        logger.error(f"STRATZ request error: {e}")
    return None


# ==================== OPENDOTA API (SYNC) ====================
def opendota_get_sync(url: str, params: Optional[Dict[str, Any]] = None) -> Optional[Any]:
    """Синхронный запрос к OpenDota (для не-async контекстов)"""
    try:
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code == 200:
            return resp.json()
        else:
            logger.warning(f"OpenDota HTTP {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        logger.error(f"OpenDota sync request error: {e}")
    return None


async def opendota_get_async(url: str, params: Optional[Dict[str, Any]] = None) -> Optional[Any]:
    """Асинхронный запрос к OpenDota с rate limiting"""
    await opendota_rate_limit()
    semaphore = get_opendota_semaphore()
    async with semaphore:
        try:
            # requests не асинхронный, но для простоты используем синхронный вызов в executor
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, lambda: opendota_get_sync(url, params))
        except Exception as e:
            logger.error(f"OpenDota async request error: {e}")
    return None


def get_player_info(account_id: int) -> Optional[Dict[str, Any]]:
    """Получает информацию об игроке: профиль, MMR, топ герои, последние матчи"""
    result: Dict[str, Any] = {}

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
    heroes = opendota_get_sync(f"https://api.opendota.com/api/players/{account_id}/heroes")
    if heroes:
        public_total = sum(h.get("games", 0) for h in heroes)
        result["publicMatchesTotal"] = public_total
        result["hiddenMatches"] = max(0, player["matchCount"] - public_total)
        heroes_sorted = sorted(heroes, key=lambda h: h.get("games", 0), reverse=True)[:3]
        result["heroesPerformance"] = [
            {
                "heroId": h["hero_id"],
                "matchCount": h["games"],
                "winCount": h["win"]
            }
            for h in heroes_sorted
        ]
        logger.info(f"[OpenDota] top-3 heroes: {[(h['hero_id'], h['games'], round(h['win']/h['games']*100,1)) for h in heroes_sorted]}")
        logger.info(f"[OpenDota] public matches: {public_total}, total: {player['matchCount']}, hidden: {result['hiddenMatches']}")
    else:
        result["heroesPerformance"] = []
        result["publicMatchesTotal"] = 0
        result["hiddenMatches"] = player["matchCount"]
        logger.info("[OpenDota] heroes: empty")

    # 3. Последние 20 матчей для формы — OpenDota
    matches = opendota_get_sync(
        f"https://api.opendota.com/api/players/{account_id}/matches",
        params={"limit": 20}
    )
    if matches:
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
        logger.info(f"[OpenDota] matches for form: {len(result['matches'])}")
    else:
        result["matches"] = []
        logger.info("[OpenDota] matches: empty")

    return result


# ==================== TTL CACHE IMPLEMENTATION ====================
class TTLCache:
    """Простой TTL кэш с автоматическим удалением просроченных записей"""

    def __init__(self, ttl_seconds: int, max_size: int = 1000):
        self.ttl = ttl_seconds
        self.max_size = max_size
        self._data: Dict[str, Tuple[Any, float]] = {}  # key -> (value, timestamp)

    def get(self, key: str) -> Optional[Any]:
        if key not in self._data:
            return None
        value, timestamp = self._data[key]
        if time.time() - timestamp > self.ttl:
            del self._data[key]
            return None
        return value

    def set(self, key: str, value: Any) -> None:
        # Простая очистка если превышен max_size
        if len(self._data) >= self.max_size:
            self._cleanup_expired()
            # Если все еще переполнен, удаляем старые
            if len(self._data) >= self.max_size:
                oldest_key = min(self._data.keys(), key=lambda k: self._data[k][1])
                del self._data[oldest_key]
        self._data[key] = (value, time.time())

    def _cleanup_expired(self) -> None:
        now = time.time()
        expired = [k for k, (_, ts) in self._data.items() if now - ts > self.ttl]
        for k in expired:
            del self._data[k]

    def clear(self) -> None:
        self._data.clear()

    def __len__(self) -> int:
        return len(self._data)


# Кэши с TTL
# ITEM_BUILDS_CACHE: TTL 1 час, макс 200 героев
ITEM_BUILDS_CACHE = TTLCache(ttl_seconds=3600, max_size=200)

# PLAYER_WINRATE_CACHE: TTL 6 часов, макс 500 игроков
PLAYER_WINRATE_CACHE = TTLCache(ttl_seconds=21600, max_size=500)

# MATCHUP_CACHE: кэш для /heroes/{id}/matchups, TTL 1 час, макс 150 героев
MATCHUP_CACHE = TTLCache(ttl_seconds=3600, max_size=150)


# ==================== HERO NAMES ====================
HERO_NAMES_DRAFT: Dict[int, str] = {}

FALLBACK_HERO_NAMES: Dict[int, str] = {
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
    136: "Marci", 137: "Primal Beast", 138: "Muerta",
    139: "Kez", 140: "Hoodwink"
}


def get_hero_names() -> Dict[int, str]:
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
    else:
        HERO_NAMES_DRAFT.update(FALLBACK_HERO_NAMES)
    return HERO_NAMES_DRAFT


def get_hero_name(hero_id: int) -> str:
    """Получает имя героя по ID"""
    names = get_hero_names()
    return names.get(hero_id, f"Hero {hero_id}")


def get_hero_id_by_name(hero_name: str) -> Optional[int]:
    """Получает ID героя по имени (case-insensitive)"""
    names = get_hero_names()
    hero_name_lower = hero_name.lower()
    for k, v in names.items():
        if v.lower() == hero_name_lower:
            return k
    return None


# ==================== PLAYER WINRATE ====================
def get_player_winrate(account_id: int) -> Optional[Dict[str, Any]]:
    """Получает общий винрейт игрока по account_id через OpenDota.
    Возвращает dict с win, lose, matches, winrate или None при ошибке."""
    if not account_id:
        return None

    cache_key = str(account_id)
    cached = PLAYER_WINRATE_CACHE.get(cache_key)
    if cached is not None:
        return cached

    url = f"https://api.opendota.com/api/players/{account_id}"
    data = opendota_get_sync(url)

    if not data:
        return None

    win = data.get("win", 0)
    lose = data.get("lose", 0)

    if win == 0 and lose == 0:
        profile = data.get("profile", {})
        win = profile.get("win", 0)
        lose = profile.get("lose", 0)

    matches = win + lose
    if matches == 0:
        return None

    winrate = round((win / matches) * 100, 1)

    result = {
        "win": win,
        "lose": lose,
        "matches": matches,
        "winrate": winrate
    }

    PLAYER_WINRATE_CACHE.set(cache_key, result)
    return result


def get_players_winrates(match_id: int) -> Optional[Dict[str, Any]]:
    """Получает винрейты всех 10 игроков матча.
    Возвращает dict: {radiant: [winrates], dire: [winrates], radiant_avg, dire_avg}"""
    url = f"https://api.opendota.com/api/matches/{match_id}"
    data = opendota_get_sync(url)

    if not data or not data.get("players"):
        return None

    radiant_winrates: List[float] = []
    dire_winrates: List[float] = []

    for player in data["players"]:
        account_id = player.get("account_id")
        is_radiant = player.get("isRadiant", False) or player.get("player_slot", 0) < 128

        if not account_id or account_id == 4294967295:
            continue

        winrate_data = get_player_winrate(account_id)
        if winrate_data and winrate_data["matches"] >= 50:
            if is_radiant:
                radiant_winrates.append(winrate_data["winrate"])
            else:
                dire_winrates.append(winrate_data["winrate"])

    if not radiant_winrates and not dire_winrates:
        return None

    radiant_avg = round(sum(radiant_winrates) / len(radiant_winrates), 1) if radiant_winrates else 0
    dire_avg = round(sum(dire_winrates) / len(dire_winrates), 1) if dire_winrates else 0

    return {
        "radiant": radiant_winrates,
        "dire": dire_winrates,
        "radiant_avg": radiant_avg,
        "dire_avg": dire_avg,
        "radiant_count": len(radiant_winrates),
        "dire_count": len(dire_winrates)
    }


# ==================== MATCH DATA ====================
# Captain's Mode game modes where pick/ban data is valid
CAPTAINS_MODE_GAME_MODES = {2, 9, 17}  # 2=Captain's Mode, 9=Reverse Captain's Mode, 17=Captain's Draft

def get_draft_state(match_id: int) -> Optional[Dict[str, Any]]:
    """Получает текущее состояние драфта по match ID.
    Сначала пробует OpenDota (публичный API), затем STRATZ (если нужен доп. контент)"""

    # --- OpenDota (публичный, не требует токена) ---
    try:
        url = f"https://api.opendota.com/api/matches/{match_id}"
        data = opendota_get_sync(url)
        if data:
            game_mode = data.get("game_mode")
            is_captains_mode = game_mode in CAPTAINS_MODE_GAME_MODES

            pick_bans = []
            if data.get("picks_bans"):
                for i, pb in enumerate(data["picks_bans"]):
                    hero_id = pb.get("hero_id")
                    # Пропускаем записи без hero_id (hero_id = 0 или None)
                    if not hero_id or hero_id <= 0:
                        continue
                    pick_bans.append({
                        "isPick": pb.get("is_pick", False),
                        "heroId": hero_id,
                        "isRadiant": pb.get("team", 0) == 0,
                        "order": pb.get("order", i),
                        "wasBannedSuccessfully": True
                    })

            players_data = []
            if data.get("players"):
                for p in data["players"]:
                    players_data.append({
                        "account_id": p.get("account_id"),
                        "heroId": p.get("hero_id"),
                        "isRadiant": p.get("isRadiant", False) or p.get("player_slot", 0) < 128,
                        "personaname": p.get("personaname"),
                        "player_slot": p.get("player_slot"),
                    })

            # FIX: Для не-Captain's Mode матчей picks_bans может быть некорректным
            # (показывает не те герои, что играли). Берём реальный состав из players.
            if not is_captains_mode and players_data:
                radiant_heroes = [p for p in players_data if p.get("isRadiant") and p.get("heroId")]
                dire_heroes = [p for p in players_data if not p.get("isRadiant") and p.get("heroId")]

                # Сортируем по player_slot (0-4 = Radiant pos 1-5, 128-132 = Dire pos 1-5)
                radiant_heroes.sort(key=lambda x: x.get("player_slot", 0))
                dire_heroes.sort(key=lambda x: x.get("player_slot", 0))

                # Берём первых 5 на команду
                radiant_heroes = radiant_heroes[:5]
                dire_heroes = dire_heroes[:5]

                # Перестраиваем pick_bans: только пики, в порядке слотов
                pick_bans = []
                order = 0
                for p in radiant_heroes:
                    pick_bans.append({
                        "isPick": True,
                        "heroId": p["heroId"],
                        "isRadiant": True,
                        "order": order,
                        "wasBannedSuccessfully": True
                    })
                    order += 1
                for p in dire_heroes:
                    pick_bans.append({
                        "isPick": True,
                        "heroId": p["heroId"],
                        "isRadiant": False,
                        "order": order,
                        "wasBannedSuccessfully": True
                    })
                    order += 1

            did_radiant_win = data.get("radiant_win")

            return {
                "id": match_id,
                "didRadiantWin": did_radiant_win,
                "startDateTime": data.get("start_time"),
                "gameMode": game_mode,
                "lobbyType": data.get("lobby_type"),
                "pickBans": pick_bans,
                "players": players_data,
                "radiant_score": data.get("radiant_score"),
                "dire_score": data.get("dire_score"),
                "duration": data.get("duration"),
            }
    except Exception as e:
        logger.error(f"OpenDota error for match {match_id}: {e}")

    # --- Fallback: STRATZ ---
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

    match = data["match"]
    # Тоже фильтруем STRATZ: только валидные heroId
    if match.get("pickBans"):
        filtered_pick_bans = []
        for pb in match["pickBans"]:
            hero_id = pb.get("heroId")
            if hero_id and hero_id > 0:
                filtered_pick_bans.append(pb)
        match["pickBans"] = filtered_pick_bans

    return match


def is_match_live(draft_state: Dict[str, Any]) -> bool:
    """Определяет, идет ли матч в реальном времени"""
    if not draft_state:
        return False
    did_radiant_win = draft_state.get("didRadiantWin")
    start_date = draft_state.get("startDateTime")
    if start_date and did_radiant_win is None:
        return True
    return False


def get_match_date_str(draft_state: Dict[str, Any]) -> str:
    """Возвращает строку с датой матча"""
    if not draft_state:
        return "Неизвестно"
    start_date = draft_state.get("startDateTime")
    if not start_date:
        return "Дата неизвестна"
    try:
        dt = datetime.fromtimestamp(start_date)
        return dt.strftime("%d.%m.%Y %H:%M")
    except Exception:
        return "Неверная дата"


# ==================== COUNTERPICKS & MATCHUPS ====================
async def get_counterpicks_async(enemy_hero_id: int, role: Optional[str] = None, take: int = 10) -> Optional[List[Dict[str, Any]]]:
    """Асинхронная версия get_counterpicks с кэшированием"""
    cache_key = f"{enemy_hero_id}:{role or 'any'}"
    cached = MATCHUP_CACHE.get(cache_key)
    if cached is not None:
        return cached[:take]

    url = f"https://api.opendota.com/api/heroes/{enemy_hero_id}/matchups"
    data = await opendota_get_async(url)

    if not data:
        return None

    results = []
    for matchup in data:
        hero_id = matchup.get("hero_id")
        games_played = matchup.get("games_played", 0)
        wins = matchup.get("wins", 0)

        if hero_id and games_played >= 50:
            win_rate = (wins / games_played) * 100 if games_played > 0 else 0
            results.append({
                "heroId": hero_id,
                "heroName": get_hero_name(hero_id),
                "winRate": round(win_rate, 1),
                "matchCount": games_played,
                "winCount": wins,
                "synergy": 0
            })

    results.sort(key=lambda x: x["winRate"], reverse=True)
    MATCHUP_CACHE.set(cache_key, results)
    return results[:take]


def get_counterpicks(enemy_hero_id: int, role: Optional[str] = None, take: int = 10) -> Optional[List[Dict[str, Any]]]:
    """Синхронная версия get_counterpicks (для обратной совместимости)"""
    cache_key = f"{enemy_hero_id}:{role or 'any'}"
    cached = MATCHUP_CACHE.get(cache_key)
    if cached is not None:
        return cached[:take]

    url = f"https://api.opendota.com/api/heroes/{enemy_hero_id}/matchups"
    data = opendota_get_sync(url)

    if not data:
        return None

    results = []
    for matchup in data:
        hero_id = matchup.get("hero_id")
        games_played = matchup.get("games_played", 0)
        wins = matchup.get("wins", 0)

        if hero_id and games_played >= 50:
            win_rate = (wins / games_played) * 100 if games_played > 0 else 0
            results.append({
                "heroId": hero_id,
                "heroName": get_hero_name(hero_id),
                "winRate": round(win_rate, 1),
                "matchCount": games_played,
                "winCount": wins,
                "synergy": 0
            })

    results.sort(key=lambda x: x["winRate"], reverse=True)
    MATCHUP_CACHE.set(cache_key, results)
    return results[:take]


def get_heroes_countering(hero_id: int, role: Optional[str] = None, min_games: int = 50) -> List[Dict[str, Any]]:
    """Возвращает героев, которые хорошо выигрывают против данного героя.
    Использует тот же кэш matchups: если у героя Y высокий winrate против X, то Y контрпикает X.
    winRate в результате — это винрейт контрпика ПРОТИВ данного героя (100 - винрейт данного героя)."""
    counters = get_counterpicks(hero_id, role)
    if not counters:
        return []
    filtered = [c for c in counters if c.get("matchCount", 0) >= min_games]
    # get_counterpicks возвращает винрейт hero_id против других героев.
    # Для "герои, которые контрпикают hero_id" нужен обратный винрейт (100 - winRate).
    for c in filtered:
        c["winRate"] = round(100 - c["winRate"], 1)
    # Сортируем по винрейту контрпика (убывание)
    filtered.sort(key=lambda x: x["winRate"], reverse=True)
    return filtered


# ==================== ITEM BUILDS ====================
def get_item_builds(hero_id: int, role: Optional[str] = None) -> Optional[List[Dict[str, Any]]]:
    """Получает рекомендуемые предметы для героя через OpenDota.
    Использует /heroes/{hero_id}/item_popularity - один запрос на героя.
    role игнорируется (OpenDota не дает разделение по позициям)."""
    cache_key = str(hero_id)
    cached = ITEM_BUILDS_CACHE.get(cache_key)
    if cached is not None:
        return cached

    url = f"https://api.opendota.com/api/heroes/{hero_id}/item_popularity"
    data = opendota_get_sync(url)

    if not data:
        return None

    results = []
    for item_data in data:
        item_id = item_data.get("item_id")
        games_played = item_data.get("games_played", 0)
        wins = item_data.get("wins", 0)

        if item_id and games_played >= 100:
            win_rate = (wins / games_played) * 100 if games_played > 0 else 0
            results.append({
                "itemId": item_id,
                "matchCount": games_played,
                "winCount": wins,
                "winRate": round(win_rate, 1)
            })

    results.sort(key=lambda x: (x["winRate"], x["matchCount"]), reverse=True)
    ITEM_BUILDS_CACHE.set(cache_key, results[:15])
    return ITEM_BUILDS_CACHE.get(cache_key)


# ==================== ITEM NAMES ====================
ITEM_NAMES_DRAFT: Dict[int, str] = {}

FALLBACK_ITEM_NAMES: Dict[int, str] = {
    1: "Blink Dagger", 2: "Blades of Attack", 3: "Broadsword", 4: "Chainmail",
    5: "Claymore", 6: "Helm of Iron Will", 7: "Javelin", 8: "Mitesser",
    9: "Mystic Staff", 10: "Platemail", 11: "Quarterstaff", 12: "Quelling Blade",
    13: "Ring of Protection", 14: "Gauntlets of Strength", 15: "Slippers of Agility",
    16: "Mantle of Intelligence", 17: "Circlet", 18: "Belt of Strength",
    19: "Boots of Speed", 20: "Gloves of Haste", 21: "Ring of Regen",
    22: "Sage's Mask", 23: "Tome of Knowledge", 24: "Clarity", 25: "Healing Salve",
    26: "Dust of Appearance", 27: "Bottle", 28: "Ward Observer", 29: "Sentry Ward",
    30: "Tango", 31: "Courier", 32: "Town Portal Scroll", 33: "Magic Wand",
    34: "Ghost Scepter", 35: "Null Talisman", 36: "Wraith Band", 37: "Bracer",
    38: "Power Treads", 39: "Phase Boots", 40: "Hand of Midas", 41: "Oblivion Staff",
    42: "Perseverance", 43: "Yasha", 44: "Sange", 45: "Crimson Guard",
    46: "Pipe of Insight", 47: "Mekansm", 48: "Vladmir's Offering", 49: "Spirit Vessel",
    50: "Urn of Shadows", 51: "Heaven's Halberd", 52: "Satanic", 53: "Black King Bar",
    54: "Silver Edge", 55: "Monkey King Bar", 56: "Butterfly", 57: "Daedalus",
    58: "Mjollnir", 59: "Divine Rapier", 60: "Hurricane Pike", 61: "Dragon Lance",
    62: "Nullifier", 63: "Glimmer Cape", 64: "Force Staff", 65: "Aether Lens",
    66: "Aeon Disk", 67: "Bloodthorn", 68: "Scythe of Vyse", 69: "Orchid Malevolence",
    70: "Refresher Orb", 71: "Dagon", 72: "Necronomicon", 73: "Radiance",
    74: "Abyssal Blade", 75: "Assault Cuirass", 76: "Shiva's Guard",
    77: "Heart of Tarrasque", 78: "Eye of Skadi", 79: "Linken's Sphere",
    80: "Lotus Orb", 81: "Guardian Greaves", 82: "Crimson Guard", 83: "Pipe of Insight",
    84: "Eul's Scepter of Divinity", 85: "Rod of Atos", 86: "Gungir",
    87: "Bloodstone", 88: "Kaya", 89: "Kaya and Sange", 90: "Yasha and Kaya",
    91: "Aghanim's Scepter", 92: "Aghanim's Shard", 93: "Moon Shard",
    94: "Ultimate Orb", 95: "Shadow Blade", 96: "Solar Crest",
    97: "Falcon Blade", 98: "Mage Slayer", 99: "Pupils Gift", 100: "Witch Blade",
    101: "Ascetic's Cap", 102: "Quickening Charm", 103: "Ogre Seal Totem",
    104: "Elven Tunic", 105: "Psychic Headband", 106: "Broom Handle",
    107: "Trusty Shovel", 108: "Nether Ward", 109: "Fallen Sky",
    110: "Gleipnir", 111: "Arcane Boots", 112: "Tranquil Boots",
    113: "Octarine Core", 114: "Meteor Hammer", 115: "Fae Wings",
    116: "Bulwark", 117: "Apex", 118: "Royal Jelly", 119: "Stormcrafter",
    120: "Mirror Shield", 121: "Swift Blink", 122: "Overwhelming Blink",
    123: "Arcane Blink", 124: "Maniac's Axe", 125: "Mystic Flare",
    126: "Timeless Relic", 127: "Gossamer Cape", 128: "Titan Sliver",
    129: "Spell Prism", 130: "Fusion Rune", 131: "Ballista",
    132: "Aether Remnant", 133: "Kinetic Gem", 134: "Ogre Seal Totem",
    135: "Ascetic's Cap", 136: "Quickening Charm", 137: "Ogre Seal Totem",
    138: "Elven Tunic", 139: "Psychic Headband", 140: "Broom Handle",
    141: "Trusty Shovel", 142: "Nether Ward", 143: "Fallen Sky",
    144: "Gleipnir", 145: "Arcane Boots", 146: "Tranquil Boots",
    147: "Octarine Core", 148: "Meteor Hammer", 149: "Fae Wings",
    150: "Bulwark", 151: "Apex", 152: "Royal Jelly", 153: "Stormcrafter",
    154: "Mirror Shield", 155: "Swift Blink", 156: "Overwhelming Blink",
    157: "Arcane Blink", 158: "Maniac's Axe", 159: "Mystic Flare",
    160: "Timeless Relic", 161: "Gossamer Cape", 162: "Titan Sliver",
    163: "Spell Prism", 164: "Fusion Rune", 165: "Ballista",
    166: "Aether Remnant", 167: "Kinetic Gem", 168: "Ogre Seal Totem",
    169: "Ascetic's Cap", 170: "Quickening Charm", 171: "Ogre Seal Totem",
    172: "Elven Tunic", 173: "Psychic Headband", 174: "Broom Handle",
    175: "Trusty Shovel", 176: "Nether Ward", 177: "Fallen Sky",
    178: "Gleipnir", 179: "Arcane Boots", 180: "Tranquil Boots",
    181: "Octarine Core", 182: "Meteor Hammer", 183: "Fae Wings",
    184: "Bulwark", 185: "Apex", 186: "Royal Jelly", 187: "Stormcrafter",
    188: "Mirror Shield", 189: "Swift Blink", 190: "Overwhelming Blink",
    191: "Arcane Blink", 192: "Maniac's Axe", 193: "Mystic Flare",
    194: "Timeless Relic", 195: "Gossamer Cape", 196: "Titan Sliver",
    197: "Spell Prism", 198: "Fusion Rune", 199: "Ballista",
    200: "Aether Remnant", 201: "Kinetic Gem",
}


def get_item_names() -> Dict[int, str]:
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
    else:
        ITEM_NAMES_DRAFT.update(FALLBACK_ITEM_NAMES)
    return ITEM_NAMES_DRAFT


def get_item_name(item_id: int) -> str:
    """Получает название предмета по ID"""
    names = get_item_names()
    return names.get(item_id, f"Item {item_id}")


# ==================== WIN PROBABILITY ====================
def calculate_win_probability(draft_state: Dict[str, Any], user_role: Optional[str] = None) -> float:
    """Вычисляет вероятность победы на основе текущего драфта."""
    if not draft_state:
        return 50.0

    pick_bans = draft_state.get("pickBans", [])

    radiant_heroes = []
    dire_heroes = []

    for pb in pick_bans:
        if pb.get("isPick") and pb.get("heroId"):
            if pb.get("isRadiant"):
                radiant_heroes.append(pb["heroId"])
            else:
                dire_heroes.append(pb["heroId"])

    def team_strength(heroes: List[int]) -> int:
        total = 0
        for h in heroes:
            total += 50
        return total

    rad_str = team_strength(radiant_heroes)
    dire_str = team_strength(dire_heroes)

    if rad_str + dire_str == 0:
        return 50.0

    return round(rad_str / (rad_str + dire_str) * 100, 1)


# ==================== ROLE DETECTION ====================
def detect_hero_role(draft_state: Dict[str, Any], hero_id: int) -> str:
    """Определяет роль героя на основе данных матча (player_slot, порядок пика).
    Возвращает одну из: carry, mid, offlane, support, hard_support, flex"""
    pick_bans = draft_state.get("pickBans", [])
    players = draft_state.get("players", [])

    # 1. Пытаемся найти по player_slot (точный способ)
    for p in players:
        if p.get("heroId") == hero_id:
            slot = p.get("player_slot", 0)
            # OpenDota: 0-4 = Radiant (1-5 pos), 128-132 = Dire (1-5 pos)
            if slot < 5:
                pos = slot + 1
            elif 128 <= slot < 133:
                pos = slot - 127
            else:
                pos = None

            if pos == 1:
                return "carry"
            elif pos == 2:
                return "mid"
            elif pos == 3:
                return "offlane"
            elif pos == 4:
                return "support"
            elif pos == 5:
                return "hard_support"

    # 2. Фоллбэк: по порядку пика в команде
    team_picks = []
    for pb in pick_bans:
        if pb.get("isPick") and pb.get("heroId") == hero_id:
            is_radiant = pb.get("isRadiant", False)
            break
    else:
        return "flex"

    for pb in pick_bans:
        if pb.get("isPick") and pb.get("isRadiant") == is_radiant:
            team_picks.append(pb)

    team_picks.sort(key=lambda x: x.get("order", 0))
    for idx, pb in enumerate(team_picks):
        if pb.get("heroId") == hero_id:
            pick_index = idx + 1
            if pick_index <= 2:
                return "carry"
            elif pick_index == 3:
                return "mid"
            elif pick_index == 4:
                return "offlane"
            elif pick_index >= 5:
                return "hard_support"

    return "flex"


# ==================== FORMAT DRAFT ADVICE ====================
def format_draft_advice(
    draft_state: Dict[str, Any],
    user_role: str,
    user_team_is_radiant: Optional[bool] = None,
    is_live: bool = True
) -> str:
    """Формирует текстовый совет для текущего хода."""
    if not draft_state:
        return "Не удалось получить состояние драфта."

    pick_bans = draft_state.get("pickBans", [])
    if not pick_bans:
        return "Драфт ещё не начался."

    match_is_live = is_match_live(draft_state)
    match_date = get_match_date_str(draft_state)
    match_id = draft_state.get('id', '?')

    radiant_picks_count = sum(1 for pb in pick_bans if pb.get("isPick") and pb.get("isRadiant"))
    dire_picks_count = sum(1 for pb in pick_bans if pb.get("isPick") and not pb.get("isRadiant"))
    radiant_bans_count = sum(1 for pb in pick_bans if not pb.get("isPick") and pb.get("isRadiant"))
    dire_bans_count = sum(1 for pb in pick_bans if not pb.get("isPick") and not pb.get("isRadiant"))

    total_picks = radiant_picks_count + dire_picks_count
    total_bans = radiant_bans_count + dire_bans_count

    radiant_picks: List[Tuple[int, str]] = []
    dire_picks: List[Tuple[int, str]] = []
    radiant_bans: List[str] = []
    dire_bans: List[str] = []

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

    def whose_turn(picks_done: int, bans_done: int) -> str:
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

    counterpicks = []
    counter_target_name = ""
    if enemy_last_pick:
        enemy_hero_id = enemy_last_pick["heroId"]
        counter_target_name = get_hero_name(enemy_hero_id)
        counterpicks = get_counterpicks(enemy_hero_id, user_role)

    our_hero_id = None
    for pb in pick_bans:
        if pb.get("isPick") and pb.get("heroId"):
            pb_is_radiant = pb.get("isRadiant", False)
            if user_team_is_radiant is not None:
                if pb_is_radiant == user_is_radiant:
                    our_hero_id = pb["heroId"]
                    break

    items_hero_id = our_hero_id if our_hero_id else (counterpicks[0]["heroId"] if counterpicks else enemy_last_pick["heroId"] if enemy_last_pick else None)
    items = get_item_builds(items_hero_id, user_role) if items_hero_id else []

    role_name = ROLE_NAMES.get(user_role, user_role)
    team_emoji = "☀️" if user_is_radiant else "🌙"
    team_name = "Radiant" if user_is_radiant else "Dire"

    if match_is_live:
        text = f"🔴 <b>LIVE МАТЧ</b> #{match_id} | Ты: {team_emoji} <b>{team_name}</b> ({role_name})\n"
        text += f"{'🟢 <b>ТВОЙ ХОД</b>' if is_our_turn else '🔴 Ход противника'} | Фаза: {phase.replace('_', ' ').title()}\n\n"
    else:
        text = f"📜 <b>ИСТОРИЧЕСКИЙ МАТЧ</b> #{match_id} | {match_date}\n"
        text += f"Ты: {team_emoji} <b>{team_name}</b> ({role_name}) | Результат: "
        did_radiant_win = draft_state.get("didRadiantWin")
        if did_radiant_win is True:
            text += "☀️ Radiant победа"
        elif did_radiant_win is False:
            text += "🌙 Dire победа"
        else:
            text += "Неизвестно"
        text += "\n\n"

    text += "📋 <b>ДРАФТ:</b>\n"
    text += "☀️ <b>Radiant:</b>\n"
    if radiant_picks:
        for order, name in radiant_picks:
            marker = " ⭐" if (user_is_radiant and our_hero_id and get_hero_name(our_hero_id) == name) else ""
            text += f"  {order}. {name}{marker}\n"
    else:
        text += "  (пусто)\n"
    if radiant_bans:
        text += f"  🚫 Баны: {', '.join(radiant_bans)}\n"

    text += "\n🌙 <b>Dire:</b>\n"
    if dire_picks:
        for order, name in dire_picks:
            marker = " ⭐" if (not user_is_radiant and our_hero_id and get_hero_name(our_hero_id) == name) else ""
            text += f"  {order}. {name}{marker}\n"
    else:
        text += "  (пусто)\n"
    if dire_bans:
        text += f"  🚫 Баны: {', '.join(dire_bans)}\n"

    text += "\n" + "─" * 30 + "\n"

    if match_is_live:
        players_wr = get_players_winrates(draft_state.get("id"))
        if players_wr and (players_wr["radiant_count"] > 0 or players_wr["dire_count"] > 0):
            text += "👥 <b>ПРОГНОЗ ПО ВИНРЕЙТАМ ИГРОКОВ:</b>\n"
            text += f"  ☀️ Radiant: {players_wr['radiant_avg']}% (игроков: {players_wr['radiant_count']}/5)\n"
            text += f"  🌙 Dire: {players_wr['dire_avg']}% (игроков: {players_wr['dire_count']}/5)\n"
            if players_wr["radiant_avg"] > 0 and players_wr["dire_avg"] > 0:
                total = players_wr["radiant_avg"] + players_wr["dire_avg"]
                radiant_prob = round(players_wr["radiant_avg"] / total * 100, 1)
                dire_prob = round(players_wr["dire_avg"] / total * 100, 1)
                favorite = "☀️ Radiant" if radiant_prob > dire_prob else "🌙 Dire"
                text += f"  📈 Шанс победы: {favorite} {max(radiant_prob, dire_prob)}%\n"
            text += "\n"

    if not match_is_live:
        text += "📊 <b>ПОСТ-АНАЛИЗ ДРАФТА:</b>\n\n"
        enemy_heroes = [name for _, name in dire_picks] if user_is_radiant else [name for _, name in radiant_picks]
        if enemy_heroes:
            text += "🎯 <b>Контрпики против врагов:</b>\n"
            for enemy_hero in enemy_heroes[:5]:
                enemy_id = get_hero_id_by_name(enemy_hero)
                if enemy_id:
                    enemy_counters = get_counterpicks(enemy_id, user_role)
                    if enemy_counters is None:
                        text += f"  vs {enemy_hero}: 🔒 данные временно недоступны\n"
                    elif enemy_counters:
                        top = enemy_counters[0]
                        text += f"  vs {enemy_hero}: <b>{top['heroName']}</b> ({top['winRate']}% WR)\n"
                    else:
                        text += f"  vs {enemy_hero}: данных нет\n"
            text += "\n"

        gameplan = get_gameplan_for_role(user_role, counter_target_name, radiant_picks, dire_picks, user_is_radiant)
        text += f"📝 <b>Геймплан ({role_name}):</b>\n{gameplan}\n\n"

        timings = get_key_timings(user_role, items_hero_id)
        if timings:
            text += f"⏰ <b>Ключевые тайминги:</b>\n{timings}\n\n"

        win_prob = calculate_win_probability(draft_state, user_role)
        text += f"📊 <b>Ретроспективный прогноз:</b> ~{win_prob}%\n"

        if did_radiant_win is not None:
            user_won = (did_radiant_win and user_is_radiant) or (not did_radiant_win and not user_is_radiant)
            actual_result = "✅ ПОБЕДА" if user_won else "❌ ПОРАЖЕНИЕ"
            text += f"\n{actual_result} (реальный результат)"

        return text

    if is_our_turn and total_picks < 10:
        text += "🧠 <b>АНАЛИЗ И ПЛАН:</b>\n\n"
        if counterpicks is None:
            text += f"🎯 <b>Контрпики против {counter_target_name}:</b>\n"
            text += "  🔒 данные временно недоступны\n"
        elif counterpicks:
            text += f"🎯 <b>Контрпики против {counter_target_name}:</b>\n"
            for i, cp in enumerate(counterpicks[:4], 1):
                synergy_emoji = "🤝" if cp.get("synergy", 0) > 0.1 else ("⚔️" if cp.get("synergy", 0) < -0.1 else "")
                text += f"  {i}. <b>{cp['heroName']}</b> — {cp['winRate']}% WR ({cp['matchCount']} игр) {synergy_emoji}\n"
        else:
            text += f"🎯 Контрпики против {counter_target_name}: данных недостаточно\n"

        text += "\n"

        if items is None:
            hero_for_items = get_hero_name(items_hero_id) if items_hero_id else "твоего героя"
            text += f"🛡 <b>Кор айтемы на {hero_for_items}:</b>\n"
            text += "  🔒 данные временно недоступны\n"
        elif items:
            hero_for_items = get_hero_name(items_hero_id) if items_hero_id else "твоего героя"
            text += f"🛡 <b>Кор айтемы на {hero_for_items}:</b>\n"
            core_items = [i for i in items if i["matchCount"] > 200][:3]
            situational = [i for i in items if i["matchCount"] <= 200][:3]
            if core_items:
                text += "  Кор: " + ", ".join([f"<b>{get_item_name(i['itemId'])}</b> ({i['winRate']}% WR)" for i in core_items]) + "\n"
            if situational:
                text += "  Ситуативные: " + ", ".join([f"<b>{get_item_name(i['itemId'])}</b> ({i['winRate']}% WR)" for i in situational]) + "\n"
        text += "\n"

        gameplan = get_gameplan_for_role(user_role, counter_target_name, radiant_picks, dire_picks, user_is_radiant)
        text += f"📝 <b>Геймплан ({role_name}):</b>\n{gameplan}\n"

        timings = get_key_timings(user_role, items_hero_id)
        if timings:
            text += f"⏰ <b>Ключевые тайминги:</b>\n{timings}\n"

    elif total_picks >= 10:
        text += "✅ <b>ДРАФТ ЗАВЕРШЁН!</b> Матч скоро начнется.\n\n"
        win_prob = calculate_win_probability(draft_state, user_role)
        text += f"📊 Прогноз победы: ~{win_prob}%\n\n"
        text += "Удачи в игре! 🎮"

    else:
        text += f"⏳ Ожидаем пик {current_turn}...\n"
        if enemy_last_pick:
            text += f"Последний пик врага: <b>{counter_target_name}</b>\n"

    return text


# ==================== MANUAL DRAFT ADVICE ====================
def format_manual_draft_advice(
    radiant_picks: List[str],
    dire_picks: List[str],
    user_role: str,
    user_is_radiant: bool,
    next_pick_is_radiant: bool
) -> str:
    """Формирует совет для ручного драфта (только пики, без банов)"""
    role_name = ROLE_NAMES.get(user_role, user_role)
    team_emoji = "☀️" if user_is_radiant else "🌙"
    team_name = "Radiant" if user_is_radiant else "Dire"
    next_emoji = "☀️" if next_pick_is_radiant else "🌙"
    next_team = "Radiant" if next_pick_is_radiant else "Dire"
    is_our_turn = (next_pick_is_radiant == user_is_radiant)

    # Счётчики
    r_count = len(radiant_picks)
    d_count = len(dire_picks)
    total_picks = r_count + d_count

    # Формируем текст
    text = f"🎮 <b>MANUAL DRAFT</b> | Ты: {team_emoji} <b>{team_name}</b> ({role_name})\n"
    text += f"{'🟢 <b>ТВОЙ ХОД</b>' if is_our_turn else '🔴 Ход противника'} | Пик #{total_picks + 1} для {next_emoji} <b>{next_team}</b>\n\n"

    # Драфт
    text += "📋 <b>ТЕКУЩИЙ ДРАФТ:</b>\n"
    text += "☀️ <b>Radiant:</b>\n"
    if radiant_picks:
        for i, name in enumerate(radiant_picks, 1):
            marker = " ⭐" if (user_is_radiant and i <= 5) else ""
            text += f"  {i}. {name}{marker}\n"
    else:
        text += "  (пусто)\n"

    text += "\n🌙 <b>Dire:</b>\n"
    if dire_picks:
        for i, name in enumerate(dire_picks, 1):
            marker = " ⭐" if (not user_is_radiant and i <= 5) else ""
            text += f"  {i}. {name}{marker}\n"
    else:
        text += "  (пусто)\n"

    text += "\n" + "─" * 30 + "\n"

    # Советы по пику
    if is_our_turn and total_picks < 10:
        # Вражеские герои
        enemy_heroes = dire_picks if user_is_radiant else radiant_picks
        our_heroes = radiant_picks if user_is_radiant else dire_picks

        text += "🧠 <b>АНАЛИЗ И СОВЕТЫ:</b>\n\n"

        # 1. Контрпики против врагов
        if enemy_heroes:
            text += "🎯 <b>Контрпики против врагов:</b>\n"
            for enemy_hero in enemy_heroes[:5]:
                enemy_id = get_hero_id_by_name(enemy_hero)
                if enemy_id:
                    enemy_counters = get_counterpicks(enemy_id, user_role)
                    if enemy_counters:
                        top = enemy_counters[0]
                        text += f"  vs <b>{enemy_hero}</b>: {top['heroName']} ({top['winRate']}% WR, {top['matchCount']} игр)\n"
                    else:
                        text += f"  vs <b>{enemy_hero}</b>: данных нет\n"
            text += "\n"

        # 2. Общие рекомендации по винрейту (мета пики для роли)
        text += "📊 <b>Топ мета-пики для твоей роли (общий WR):</b>\n"
        meta_picks = get_meta_picks_for_role(user_role, our_heroes, enemy_heroes)
        for i, mp in enumerate(meta_picks[:5], 1):
            text += f"  {i}. <b>{mp['heroName']}</b> — {mp['winRate']}% WR ({mp['matchCount']} игр)\n"
        text += "\n"

        # 3. Геймплан
        enemy_list = ", ".join(enemy_heroes[:5]) if enemy_heroes else "неизвестны"
        gameplan = (
            "• <b>Early (0-10 мин):</b> Контроллируй линию, не умирай зря, собирай опыт/голду.\n"
            "  Следи за рунами, ставь обсы, реагируй на дымы.\n"
            "• <b>Mid (10-20 мин):</b> Группируйся за объектами (тауэры, Рошан).\n"
            f"  Ищи пики на одиночках, создавай пространство для кори.\n"
            f"• <b>Late (20+ мин):</b> Играй за команде, не лезь один.\n"
            f"  Таргет приоритет: саппорты → мид → кари. Контроль хайграунда.\n"
            f"• <b>Против {enemy_list}:</b> Адаптируй пик/айтемы под их драфт. Не давай фармить комфортно."
        )
        text += f"📝 <b>ГЕЙМПЛАН:</b>\n{gameplan}\n\n"

        # 4. Опасности и возможности
        threats = analyze_threats(enemy_heroes, our_heroes, user_role)
        if threats and threats != "  Прямых угроз твоей роли пока нет.":
            text += f"⚠️ <b>ОПАСНОСТИ:</b>\n{threats}\n\n"

        opportunities = analyze_opportunities(enemy_heroes, our_heroes, user_role)
        if opportunities and opportunities != "  Ищи контрпики в следующем ходе.":
            text += f"✅ <b>ВОЗМОЖНОСТИ:</b>\n{opportunities}\n"

    elif total_picks >= 10:
        text += "✅ <b>ДРАФТ ЗАВЕРШЁН!</b> (10 пиков)\n\n"
        win_prob = calculate_win_probability_from_picks(radiant_picks, dire_picks)
        text += f"📊 <b>Прогноз победы:</b> ~{win_prob}%\n\n"
        text += "Удачи в игре! 🎮"

    else:
        text += f"⏳ Ожидаем пик {next_team}...\n"
        if enemy_heroes := (dire_picks if next_pick_is_radiant else radiant_picks):
            last = enemy_heroes[-1]
            text += f"Последний пик {next_emoji}: <b>{last}</b>\n"

    return text


def get_meta_picks_for_role(role: str, our_heroes: List[str], enemy_heroes: List[str]) -> List[Dict]:
    """Возвращает общие топ-пики для роли (по винрейту), исключая уже взятых"""
    taken = set(our_heroes + enemy_heroes)
    role_position = ROLE_TO_POSITION.get(role)

    # Берём данные из кэша или делаем общий запрос
    # Для простоты: возвращаем популярные пики для роли из общего пула
    # В идеале — запрос к OpenDota /heroes с фильтром по позиции
    results = []

    # Список популярных героев по ролям (упрощённый)
    role_heroes = {
        "carry": ["Anti-Mage", "Phantom Assassin", "Spectre", "Terrorblade", "Faceless Void", "Juggernaut", "Luna", "Morphling", "Naga Siren", "Medusa"],
        "mid": ["Templar Assassin", "Storm Spirit", "Ember Spirit", "Invoker", "Puck", "Shadow Fiend", "Queen of Pain", "Lina", "Kunkka", "Void Spirit"],
        "offlane": ["Mars", "Axe", "Centaur Warrunner", "Bristleback", "Timbersaw", "Tidehunter", "Underlord", "Dark Seer", "Beastmaster", "Centaur Warrunner"],
        "support": ["Earthshaker", "Lion", "Shadow Shaman", "Grimstroke", "Bane", "Crystal Maiden", "Witch Doctor", "Ogre Magi", "Undying", "Treant Protector"],
        "hard_support": ["Crystal Maiden", "Witch Doctor", "Ogre Magi", "Treant Protector", "Undying", "Bane", "Shadow Shaman", "Keeper of the Light", "Io", "Dazzle"],
        "flex": ["Puck", "Mirana", "Vengeful Spirit", "Windranger", "Kunkka", "Pangolier", "Void Spirit", "Hoodwink", "Marci", "Muerta"],
    }

    heroes_for_role = role_heroes.get(role, role_heroes["flex"])

    for hero_name in heroes_for_role:
        if hero_name in taken:
            continue
        hero_id = get_hero_id_by_name(hero_name)
        if not hero_id:
            continue
        # Получаем общий винрейт героя (упрощённый запрос)
        try:
            url = f"https://api.opendota.com/api/heroes/{hero_id}"
            data = opendota_get_sync(url)
            if data:
                wr = data.get("winrate", 0) * 100 if data.get("winrate") else 0
                pick_rate = data.get("pickrate", 0) * 100 if data.get("pickrate") else 0
                results.append({
                    "heroName": hero_name,
                    "winRate": round(wr, 1),
                    "matchCount": int(pick_rate * 10000) if pick_rate else 0  # примерная оценка
                })
        except:
            pass

    results.sort(key=lambda x: x["winRate"], reverse=True)
    return results[:5]


def analyze_threats(enemy_heroes: List[str], our_heroes: List[str], user_role: str) -> str:
    """Анализирует главные угрозы от вражеского драфта"""
    threats = []
    for hero in enemy_heroes[:3]:
        hero_id = get_hero_id_by_name(hero)
        if not hero_id:
            continue
        # Угроза: вражеский герой имеет высокий общий винрейт (сильный мета пик)
        # или контрпикает наших уже выбранных союзников
        try:
            url = f"https://api.opendota.com/api/heroes/{hero_id}"
            data = opendota_get_sync(url)
            if data:
                overall_wr = data.get("winrate", 0) * 100 if data.get("winrate") else 0
                if overall_wr > 53:  # Сильный мета герой
                    threats.append(f"  • <b>{hero}</b> — сильный мета пик ({overall_wr:.1f}% WR). Играй осторожно.")
        except:
            pass

        # Проверяем, контрпикает ли враг наших союзников
        for ally in our_heroes[:3]:
            ally_id = get_hero_id_by_name(ally)
            if ally_id:
                # get_counterpicks(ally_id) возвращает героев, которых ally имеет высокий WR
                # Нам нужно обратное: героев, у которых высокий WR против ally
                ally_counters = get_heroes_countering(ally_id, "flex")
                if ally_counters:
                    top = ally_counters[0]
                    if top['heroName'] == hero and top['winRate'] > 55:
                        threats.append(f"  • <b>{hero}</b> контрпикает твоего союзника <b>{ally}</b> ({top['winRate']}% WR)")
    return "\n".join(threats) if threats else "  Прямых угроз твоей роли пока нет."


def analyze_opportunities(enemy_heroes: List[str], our_heroes: List[str], user_role: str) -> str:
    """Анализирует возможности (слабые места врагов)"""
    opps = []
    for hero in enemy_heroes[:3]:
        hero_id = get_hero_id_by_name(hero)
        if not hero_id:
            continue
        countered_by = get_heroes_countering(hero_id, user_role)
        if countered_by:
            top = countered_by[0]
            if top['winRate'] > 55:
                # Проверяем, не взят ли уже этот контрпик
                if top['heroName'] not in enemy_heroes and top['heroName'] not in our_heroes:
                    opps.append(f"  • <b>{hero}</b> уязвим к <b>{top['heroName']}</b> ({top['winRate']}% WR) — забирай, если свободен")
                else:
                    opps.append(f"  • <b>{hero}</b> уязвим к <b>{top['heroName']}</b> ({top['winRate']}% WR) — но уже взят")
    return "\n".join(opps) if opps else "  Ищи контрпики в следующем ходе."


def calculate_win_probability_from_picks(radiant_picks: List[str], dire_picks: List[str]) -> float:
    """Упрощённый расчёт винрейта по пикам"""
    # Заглушка — можно улучшить через hero winrates
    return 50.0


# ==================== GAMEPLAN & TIMINGS ====================
def get_gameplan_for_role(role: str, enemy_last_pick: str, radiant_picks: List[Tuple[int, str]], dire_picks: List[Tuple[int, str]], user_is_radiant: bool) -> str:
    """Возвращает общий геймплан (независимо от роли)"""
    # Собираем всех врагов для контекста
    enemy_heroes = [name for _, name in (dire_picks if user_is_radiant else radiant_picks)]
    enemy_list = ", ".join(enemy_heroes[:5]) if enemy_heroes else "неизвестны"

    return (
        "• <b>Early (0-10 мин):</b> Контроллируй свою линию, не умирай зря, собирай опыт и голду.\n"
        "  Следи за рунами, ставь обсы, реагируй на дымы врага.\n"
        "• <b>Mid (10-20 мин):</b> Группируйся с командой для взятия объектов (тауэры, Рошан).\n"
        "  Ищи пики на одиночных врагах, создавай пространство для своих кори.\n"
        f"• <b>Late (20+ мин):</b> Играй за команде — не лезь один, используй смоки и инициацию.\n"
        f"  Таргет приоритет: их саппорты → мид → кари. Контролируй хайграунд.\n"
        f"• <b>Против {enemy_list}:</b> Адаптируй пик и айтемы под их драфт. Не давай им комфортно фармить."
    )


def get_key_timings(role: str, hero_id: Optional[int] = None) -> str:
    """Ключевые тайминги/уровни для игры (общие, без привязки к айтемам)"""
    return (
        "• <b>6 лвл</b> — первый пауэрспайк (ульт/ключевой скилл), ищи файты\n"
        "• <b>10-12 мин</b> — первые коре айтемы, контроль Рошана, первые тауэр дайвы\n"
        "• <b>15-18 мин</b> — 2-3 слота, активные командные файты, битва за Рошана\n"
        "• <b>20-25 мин</b> — ключевые предметы на позициях, хайграунд дайвы/оборона\n"
        "• <b>30+ мин</b> — полные билды, решающие файты, бэкдоры, Трон"
    )


# ==================== CACHE STATS (для диагностики) ====================
def get_cache_stats() -> Dict[str, Any]:
    """Возвращает статистику по кэшам для мониторинга"""
    return {
        "hero_names": len(HERO_NAMES_DRAFT),
        "item_names": len(ITEM_NAMES_DRAFT),
        "item_builds": len(ITEM_BUILDS_CACHE),
        "player_winrates": len(PLAYER_WINRATE_CACHE),
        "matchups": len(MATCHUP_CACHE),
    }


def clear_all_caches() -> None:
    """Полная очистка всех кэшей"""
    HERO_NAMES_DRAFT.clear()
    ITEM_NAMES_DRAFT.clear()
    ITEM_BUILDS_CACHE.clear()
    PLAYER_WINRATE_CACHE.clear()
    MATCHUP_CACHE.clear()
    logger.info("All caches cleared")