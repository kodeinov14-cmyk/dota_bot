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

    # 1. Профиль + топ героев (берём больше, отфильтруем и отсортируем в Python)
    # matchGroupOrderBy: MATCH_COUNT — чтобы пришли самые сыгранные герои
    q_profile = """
    query GetPlayer($id: Long!) {
      player(steamAccountId: $id) {
        steamAccount { name }
        matchCount
        winCount
        heroesPerformance(request: {take: 50, matchGroupOrderBy: MATCH_COUNT}) {
          heroId
          matchCount
          winCount
        }
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

    # Фильтруем героев: минимум 20 игр, сортируем по винрейту DESC, берём топ-3
    heroes = player.get("heroesPerformance", [])
    filtered_heroes = [
        h for h in heroes
        if h.get("matchCount", 0) >= 20
    ]
    # Сортируем по винрейту (winCount / matchCount) по убыванию
    filtered_heroes.sort(
        key=lambda h: h["winCount"] / h["matchCount"] if h["matchCount"] > 0 else 0,
        reverse=True
    )
    result["heroesPerformance"] = filtered_heroes[:3]

    # 2. Последние 20 матчей для формы (orderBy: DESC = самые свежие первыми)
    q_matches = """
    query GetMatches($id: Long!) {
      player(steamAccountId: $id) {
        matches(request: {take: 20, orderBy: DESC}) {
          id
          didRadiantWin
          players {
            steamAccountId
            heroId
            isVictory
            isRadiant
          }
        }
      }
    }
    """
    data_m = stratz_request(q_matches, {"id": account_id})
    if data_m and data_m.get("player") and data_m["player"].get("matches"):
        matches = data_m["player"]["matches"]
        # В app.py форма считается через any(p.get("isVictory") for p in m["players"])
        # Чтобы это работало правильно, оставим в players только нашего игрока
        for m in matches:
            m["players"] = [p for p in m.get("players", []) if p.get("steamAccountId") == account_id]
        result["matches"] = matches
    else:
        result["matches"] = []

    return result