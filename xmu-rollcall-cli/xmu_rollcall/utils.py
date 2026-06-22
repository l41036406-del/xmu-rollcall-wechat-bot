import json

import requests

BASE_URL = "https://lnt.xmu.edu.cn"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


def save_session(session: requests.Session, path: str) -> None:
    try:
        cookie_dict = requests.utils.dict_from_cookiejar(session.cookies)
        with open(path, "w", encoding="utf-8") as file_obj:
            json.dump(cookie_dict, file_obj)
    except Exception:
        pass


def load_session(session: requests.Session, path: str) -> bool:
    try:
        with open(path, "r", encoding="utf-8") as file_obj:
            cookie_dict = json.load(file_obj)
        session.cookies = requests.utils.cookiejar_from_dict(cookie_dict)
        return True
    except Exception:
        return False


def verify_session(session: requests.Session) -> dict:
    try:
        response = session.get(f"{BASE_URL}/api/profile", headers=HEADERS, timeout=15)
        if response.status_code != 200:
            return {}
        payload = response.json()
        if isinstance(payload, dict) and "name" in payload:
            return payload
    except Exception:
        pass
    return {}
