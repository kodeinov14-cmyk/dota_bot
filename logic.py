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

def get_player_info(account_id: int):
    result = {}

    # 1. Профиль (player)
    q_profile = """
    query GetPlayer($id: Long!) {
      player(steamAccountId: $id) {
        steamAccount { name }
        matchCount
        winCount
      }
    }
    """
    data = stratz_request(q_profile, {"id": account_id})
    if not data or not data.get("player"):
        return None
    result.update(data["player"])

    # 2. Последние матчи (playerMatches) для формы
    q_matches = """
    query GetMatches($id: Long!) {
      playerMatches(steamAccountId: $id, request: {take: 20}) {
        isVictory
      }
    }
    """
    data_m = stratz_request(q_matches, {"id": account_id})
    if data_m and data_m.get("playerMatches"):
        result["matches"] = data_m["playerMatches"]
    else:
        result["matches"] = []

    # 3. Топ героев (playerHeroes)
    q_heroes = """
    query GetHeroes($id: Long!) {
      playerHeroes(steamAccountId: $id, request: {take: 3}) {
        heroId
        matchCount
        winCount
      }
    }
    """
    data_h = stratz_request(q_heroes, {"id": account_id})
    if data_h and data_h.get("playerHeroes"):
        result["heroes"] = data_h["playerHeroes"]
    else:
        result["heroes"] = []

    return result