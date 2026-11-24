import os
import re
import requests
from dotenv import load_dotenv

load_dotenv()

STEAM_API_KEY = os.getenv("STEAM_API_KEY")

def get_account_id(steam_url: str):
    steam_url = steam_url.strip().rstrip('/')
    
    match_profiles = re.search(r'steamcommunity\.com/profiles/(\d+)', steam_url)
    if match_profiles:
        steamid64 = int(match_profiles.group(1))
        account_id = steamid64 - 76561197960265728
        print(f"DEBUG: Ссылка типа profiles. AccountID: {account_id}")
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
                print(f"DEBUG: Ссылка типа id. AccountID: {account_id}")
                return account_id
        except Exception as e:
            print(f"DEBUG: Ошибка при запросе к Steam API: {e}")
            
    return None

def get_player_info(account_id: int):
    url = f"https://api.opendota.com/api/players/{account_id}"
    print(f"DEBUG: Отправляем запрос в OpenDota: {url}")
    
    try:
        response = requests.get(url, timeout=15)
        print(f"DEBUG: Код ответа OpenDota: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            if "profile" in data:
                return data
        else:
            print(f"DEBUG: Текст ошибки OpenDota: {response.text[:200]}")
            
    except Exception as e:
        print(f"DEBUG: Ошибка при запросе к OpenDota: {e}")
        
    return None