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
        account_id = steamid64 - 76561197960265728
        return account_id
        
    match_id = re.search(r'steamcommunity\.com/id/([a-zA-Z0-9_-]+)', steam_url)
    if match_id:
        vanity_url = match_id.group(1)
        api_url = f"http://api.steampowered.com/ISteamUser/ResolveVanityURL/v0001/?key={STEAM_API_KEY}&vanityurl={vanity_url}"
        
        try:
            response = requests.get(api_url, timeout=10).json()
            if response.get("response", {}).get("success") == 1:
                steamid64 = int(response["response"]["steamid"])
                account_id = steamid64 - 76561197960265728
                return account_id
        except Exception as e:
            print(f"DEBUG: Ошибка при запросе к Steam API: {e}", flush=True)
            
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
            print(f"DEBUG: Код ответа STRATZ: {response.status_code}", flush=True)
            print(f"DEBUG: Текст ошибки STRATZ: {response.text[:300]}", flush=True)
    except Exception as e:
        print(f"DEBUG: Ошибка при запросе к STRATZ: {e}", flush=True)
    return None

def get_player_info(account_id: int):
    # 1. Запрос на профиль и последние матчи. 
    # Победа игрока теперь берется из players { isVictory }
    query_profile = """
    query GetPlayer($steamAccountId: Long!) {
      player(steamAccountId: $steamAccountId) {
        steamAccount {
          name
        }
        matchCount
        winCount
        matches(request: {take: 20}) {
          players {
            isVictory
          }
        }
      }
    }
    """
    data_profile = stratz_request(query_profile, {"steamAccountId": account_id})
    if not data_profile or not data_profile.get("player"):
        return None

    player = data_profile["player"]

    # 2. Запрос на топ героев. 
    # Поле heroes с параметром take должно работать (в отличие от предыдущей ошибки, 
    # которая была из-за того, что мы запросили его в одном запросе с неправильным match)
    query_heroes = """
    query GetHeroes($steamAccountId: Long!) {
      player(steamAccountId: $steamAccountId) {
        heroes(take: 3) {
          heroId
          matchCount
          winCount
        }
      }
    }
    """
    data_heroes = stratz_request(query_heroes, {"steamAccountId": account_id})
    if data_heroes and data_heroes.get("player") and data_heroes["player"].get("heroes"):
        player["heroes"] = data_heroes["player"]["heroes"]
    else:
        player["heroes"] = []

    return player