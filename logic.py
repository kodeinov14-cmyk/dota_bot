import os
import re
import requests
from dotenv import load_dotenv

load_dotenv()

STEAM_API_KEY = os.getenv("STEAM_API_KEY")
STRATZ_TOKEN = os.getenv("STRATZ_TOKEN")


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
        ranks { seasonRank }
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
    result["seasonRank"] = ranks[0].get("seasonRank") if ranks else None

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