from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional, Tuple

from .config import CONFIG_DIR, ensure_config_dir

WECHAT_BOT_CONFIG_FILE = CONFIG_DIR / "wechat_bot_config.json"
DEFAULT_WECHAT_BOT_CONFIG = {
    "version": 1,
    "users": {},
}


def load_wechat_bot_config() -> Dict[str, Any]:
    ensure_config_dir()
    if WECHAT_BOT_CONFIG_FILE.exists():
        try:
            with open(WECHAT_BOT_CONFIG_FILE, "r", encoding="utf-8") as file_obj:
                payload = json.load(file_obj)
                if isinstance(payload, dict):
                    payload.setdefault("version", 1)
                    payload.setdefault("users", {})
                    return payload
        except Exception:
            pass
    return {
        "version": DEFAULT_WECHAT_BOT_CONFIG["version"],
        "users": {},
    }


def save_wechat_bot_config(config: Dict[str, Any]) -> None:
    ensure_config_dir()
    with open(WECHAT_BOT_CONFIG_FILE, "w", encoding="utf-8") as file_obj:
        json.dump(config, file_obj, indent=2, ensure_ascii=False)


def _ensure_user(config: Dict[str, Any], user_id: str) -> Dict[str, Any]:
    users = config.setdefault("users", {})
    user_config = users.setdefault(
        user_id,
        {
            "accounts": [],
            "current_account_id": None,
            "context_token": "",
            "cron": None,
            "cron_jobs": [],
        },
    )
    user_config.setdefault("accounts", [])
    user_config.setdefault("current_account_id", None)
    user_config.setdefault("context_token", "")
    user_config.setdefault("cron", None)
    user_config.setdefault("cron_jobs", [])
    return user_config


def _get_next_account_id(accounts: List[Dict[str, Any]]) -> int:
    if not accounts:
        return 1
    return max(int(account.get("id", 0)) for account in accounts) + 1


def _normalize_cron_job(payload: Dict[str, Any], cron_id: int) -> Dict[str, Any]:
    return {
        "id": int(payload.get("id", cron_id)),
        "weekday": int(payload.get("weekday", 0)),
        "hour": int(payload.get("hour", 0)),
        "minute": int(payload.get("minute", 0)),
        "time_text": payload.get("time_text") or f"{int(payload.get('hour', 0)):02d}:{int(payload.get('minute', 0)):02d}",
        "last_triggered_key": payload.get("last_triggered_key"),
    }


def _normalize_cron_jobs(user_config: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw_jobs = user_config.get("cron_jobs")
    jobs: List[Dict[str, Any]] = []
    if isinstance(raw_jobs, list):
        for index, item in enumerate(raw_jobs, start=1):
            if isinstance(item, dict):
                jobs.append(_normalize_cron_job(item, index))

    if not jobs and isinstance(user_config.get("cron"), dict):
        jobs.append(_normalize_cron_job(user_config["cron"], 1))

    jobs.sort(key=lambda item: int(item.get("id", 0)))
    user_config["cron_jobs"] = jobs
    return jobs


def _get_next_cron_job_id(jobs: List[Dict[str, Any]]) -> int:
    if not jobs:
        return 1
    return max(int(job.get("id", 0)) for job in jobs) + 1


def get_user_accounts(user_id: str) -> List[Dict[str, Any]]:
    config = load_wechat_bot_config()
    user_config = _ensure_user(config, user_id)
    return list(user_config.get("accounts", []))


def get_user_account_by_id(user_id: str, account_id: int) -> Optional[Dict[str, Any]]:
    for account in get_user_accounts(user_id):
        if int(account.get("id", 0)) == int(account_id):
            return account
    return None


def get_current_user_account(user_id: str) -> Optional[Dict[str, Any]]:
    config = load_wechat_bot_config()
    user_config = _ensure_user(config, user_id)
    current_account_id = user_config.get("current_account_id")
    if current_account_id is None:
        return None
    for account in user_config.get("accounts", []):
        if int(account.get("id", 0)) == int(current_account_id):
            return account
    return None


def add_or_update_user_account(
    user_id: str,
    username: str,
    password: str,
    name: str,
) -> Tuple[Dict[str, Any], bool]:
    config = load_wechat_bot_config()
    user_config = _ensure_user(config, user_id)
    accounts = user_config.get("accounts", [])

    for account in accounts:
        if account.get("username") == username:
            account["password"] = password
            account["name"] = name
            user_config["current_account_id"] = account.get("id")
            save_wechat_bot_config(config)
            return account, False

    account = {
        "id": _get_next_account_id(accounts),
        "name": name,
        "username": username,
        "password": password,
    }
    accounts.append(account)
    user_config["current_account_id"] = account["id"]
    save_wechat_bot_config(config)
    return account, True


def set_current_user_account(user_id: str, account_id: int) -> Optional[Dict[str, Any]]:
    config = load_wechat_bot_config()
    user_config = _ensure_user(config, user_id)
    for account in user_config.get("accounts", []):
        if int(account.get("id", 0)) == int(account_id):
            user_config["current_account_id"] = int(account_id)
            save_wechat_bot_config(config)
            return account
    return None


def save_user_context_token(user_id: str, context_token: str) -> None:
    if not context_token:
        return
    config = load_wechat_bot_config()
    user_config = _ensure_user(config, user_id)
    if user_config.get("context_token") == context_token:
        return
    user_config["context_token"] = context_token
    save_wechat_bot_config(config)


def load_all_user_context_tokens() -> Dict[str, str]:
    config = load_wechat_bot_config()
    tokens: Dict[str, str] = {}
    for user_id, user_config in config.get("users", {}).items():
        token = str(user_config.get("context_token") or "").strip()
        if token:
            tokens[user_id] = token
    return tokens


def get_user_cron_schedules(user_id: str) -> List[Dict[str, Any]]:
    config = load_wechat_bot_config()
    user_config = _ensure_user(config, user_id)
    jobs = _normalize_cron_jobs(user_config)
    return [dict(job) for job in jobs]


def add_user_cron_schedule(user_id: str, weekday: int, hour: int, minute: int) -> Tuple[Dict[str, Any], bool]:
    config = load_wechat_bot_config()
    user_config = _ensure_user(config, user_id)
    jobs = _normalize_cron_jobs(user_config)

    for job in jobs:
        if (
            int(job.get("weekday", -1)) == int(weekday)
            and int(job.get("hour", -1)) == int(hour)
            and int(job.get("minute", -1)) == int(minute)
        ):
            return dict(job), False

    cron = {
        "id": _get_next_cron_job_id(jobs),
        "weekday": int(weekday),
        "hour": int(hour),
        "minute": int(minute),
        "time_text": f"{int(hour):02d}:{int(minute):02d}",
        "last_triggered_key": None,
    }
    jobs.append(cron)
    user_config["cron_jobs"] = sorted(jobs, key=lambda item: int(item.get("id", 0)))
    user_config["cron"] = None
    save_wechat_bot_config(config)
    return dict(cron), True


def delete_user_cron_schedule(user_id: str, cron_id: int) -> Optional[Dict[str, Any]]:
    config = load_wechat_bot_config()
    user_config = _ensure_user(config, user_id)
    jobs = _normalize_cron_jobs(user_config)
    for index, job in enumerate(jobs):
        if int(job.get("id", 0)) != int(cron_id):
            continue
        removed = jobs.pop(index)
        user_config["cron_jobs"] = jobs
        user_config["cron"] = None
        save_wechat_bot_config(config)
        return dict(removed)
    return None


def clear_user_cron_schedules(user_id: str) -> int:
    config = load_wechat_bot_config()
    user_config = _ensure_user(config, user_id)
    jobs = _normalize_cron_jobs(user_config)
    cleared = len(jobs)
    if cleared == 0:
        return 0
    user_config["cron"] = None
    user_config["cron_jobs"] = []
    save_wechat_bot_config(config)
    return cleared


def mark_user_cron_triggered(user_id: str, cron_id: int, slot_key: str) -> None:
    config = load_wechat_bot_config()
    user_config = _ensure_user(config, user_id)
    jobs = _normalize_cron_jobs(user_config)
    for job in jobs:
        if int(job.get("id", 0)) == int(cron_id):
            job["last_triggered_key"] = slot_key
            user_config["cron_jobs"] = jobs
            user_config["cron"] = None
            save_wechat_bot_config(config)
            return


def list_user_cron_jobs() -> List[Dict[str, Any]]:
    config = load_wechat_bot_config()
    jobs: List[Dict[str, Any]] = []
    for user_id, user_config in config.get("users", {}).items():
        cron_jobs = _normalize_cron_jobs(user_config)
        for cron in cron_jobs:
            jobs.append(
                {
                    "user_id": user_id,
                    "context_token": str(user_config.get("context_token") or ""),
                    "cron": dict(cron),
                }
            )
    return jobs


def build_user_session_cache_key(user_id: str, account_id: int) -> str:
    user_hash = hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:16]
    return f"wechat_{user_hash}_{account_id}"
