#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple


SKILL_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = SKILL_ROOT.parent.parent
DATA_DIR = SKILL_ROOT / "data"
ACCOUNTS_PATH = DATA_DIR / "accounts.json"
STATE_VERSION = 1


def print_json(payload: Dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def fail(code: str, message: str, **extra: Any) -> None:
    payload = {
        "ok": False,
        "error": code,
        "message": message,
    }
    payload.update(extra)
    print_json(payload)
    raise SystemExit(1)


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def mask_username(username: str) -> str:
    if len(username) <= 2:
        return "*" * len(username)
    if len(username) <= 5:
        return username[0] + ("*" * (len(username) - 2)) + username[-1]
    return username[:3] + ("*" * (len(username) - 5)) + username[-2:]


def ensure_runtime_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def load_state() -> Dict[str, Any]:
    ensure_runtime_dirs()
    if not ACCOUNTS_PATH.exists():
        return {
            "version": STATE_VERSION,
            "current_account_id": None,
            "next_account_id": 1,
            "accounts": [],
        }

    try:
        state = json.loads(ACCOUNTS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail("state-corrupt", "Unable to parse data/accounts.json.", path=str(ACCOUNTS_PATH), detail=str(exc))

    if not isinstance(state, dict) or not isinstance(state.get("accounts", []), list):
        fail("state-corrupt", "data/accounts.json has an unexpected structure.", path=str(ACCOUNTS_PATH))

    state.setdefault("version", STATE_VERSION)
    state.setdefault("current_account_id", None)
    state.setdefault("next_account_id", 1)
    state.setdefault("accounts", [])
    return state


def save_state(state: Dict[str, Any]) -> None:
    ensure_runtime_dirs()
    ACCOUNTS_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def account_cache_key(account_id: int) -> str:
    return f"account-{account_id}"


def account_session_path(get_session_cache_path, account_id: int) -> Path:
    return Path(get_session_cache_path(account_cache_key(account_id)))


def account_summary(account: Dict[str, Any], current_account_id: int | None) -> Dict[str, Any]:
    return {
        "id": int(account["id"]),
        "name": account.get("name") or "",
        "username_masked": mask_username(account.get("username", "")),
        "is_current": int(account["id"]) == int(current_account_id or 0),
        "created_at": account.get("created_at"),
        "updated_at": account.get("updated_at"),
    }


def summarize_accounts(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    accounts = sorted(state["accounts"], key=lambda item: int(item["id"]))
    return [account_summary(account, state.get("current_account_id")) for account in accounts]


def bootstrap_project() -> Tuple[Any, Any, Any]:
    package_dir = REPO_ROOT / "xmu-rollcall-cli" / "xmu_rollcall"
    if not package_dir.exists():
        fail(
            "repo-not-found",
            "Unable to locate xmu-rollcall-cli next to this skill.",
            repo_root=str(REPO_ROOT),
        )

    os.environ["XMU_ROLLCALL_CONFIG_DIR"] = str(DATA_DIR)
    sys.path.insert(0, str(REPO_ROOT / "xmu-rollcall-cli"))

    try:
        from xmu_rollcall.config import get_session_cache_path
        from xmu_rollcall.rollcall_service import RollcallService
        from xmu_rollcall.utils import save_session
    except ModuleNotFoundError as exc:
        fail(
            "dependency-missing",
            "Skill runtime dependencies are missing. Run scripts/xmu_rollcall bootstrap first.",
            missing_module=exc.name,
        )

    return RollcallService, save_session, get_session_cache_path


RollcallService, save_session, get_session_cache_path = bootstrap_project()


def find_account_by_id(state: Dict[str, Any], account_id: int) -> Dict[str, Any]:
    for account in state["accounts"]:
        if int(account["id"]) == int(account_id):
            return account
    fail("account-not-found", "The requested account ID was not found.", account_id=account_id)


def resolve_active_account(state: Dict[str, Any], account_id: int | None) -> Dict[str, Any]:
    accounts = state["accounts"]
    if not accounts:
        fail("no-accounts", "No stored XMU accounts were found.")

    if account_id is not None:
        return find_account_by_id(state, account_id)

    current_account_id = state.get("current_account_id")
    if current_account_id is not None:
        for account in accounts:
            if int(account["id"]) == int(current_account_id):
                return account

    if len(accounts) == 1:
        return accounts[0]

    fail(
        "ambiguous-selection",
        "Multiple accounts exist and no active account is set. Switch the current account or specify --account-id.",
        accounts=summarize_accounts(state),
    )


def build_service(account: Dict[str, Any]) -> Any:
    return RollcallService(account, session_cache_key=account_cache_key(int(account["id"])))


def rollcall_payload(rollcall: Any) -> Dict[str, Any]:
    can_answer = (
        not rollcall.is_expired
        and rollcall.status != "on_call_fine"
        and (rollcall.is_radar or (rollcall.is_number and rollcall.status == "absent"))
    )
    return {
        "rollcall_id": int(rollcall.rollcall_id),
        "course_title": rollcall.course_title,
        "type": rollcall.type_label,
        "status": rollcall.status,
        "is_expired": bool(rollcall.is_expired),
        "can_answer": can_answer,
        "created_by_name": rollcall.created_by_name,
        "department_name": rollcall.department_name,
    }


def command_list_accounts(_args: argparse.Namespace) -> None:
    state = load_state()
    print_json(
        {
            "ok": True,
            "skill_root": str(SKILL_ROOT),
            "data_dir": str(DATA_DIR),
            "current_account_id": state.get("current_account_id"),
            "account_count": len(state["accounts"]),
            "accounts": summarize_accounts(state),
        }
    )


def command_add_account(args: argparse.Namespace) -> None:
    username = args.username.strip()
    password = args.password
    name_override = (args.name or "").strip()
    if not username:
        fail("invalid-username", "Username cannot be empty.")
    if not password:
        fail("invalid-password", "Password cannot be empty.")

    validation = RollcallService.validate_credentials(username=username, password=password)
    state = load_state()
    matched_account = next((item for item in state["accounts"] if item.get("username") == username), None)
    timestamp = now_iso()
    display_name = name_override or validation.name or username

    if matched_account is None:
        account_id = int(state.get("next_account_id") or 1)
        matched_account = {
            "id": account_id,
            "username": username,
            "password": password,
            "name": display_name,
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        state["accounts"].append(matched_account)
        state["next_account_id"] = account_id + 1
        action = "created"
    else:
        matched_account["password"] = password
        matched_account["name"] = display_name
        matched_account["updated_at"] = timestamp
        action = "updated"

    if args.set_current or state.get("current_account_id") is None or len(state["accounts"]) == 1:
        state["current_account_id"] = int(matched_account["id"])

    save_session(validation.session, str(account_session_path(get_session_cache_path, int(matched_account["id"]))))
    save_state(state)

    print_json(
        {
            "ok": True,
            "action": action,
            "current_account_id": state.get("current_account_id"),
            "account": account_summary(matched_account, state.get("current_account_id")),
        }
    )


def command_switch_account(args: argparse.Namespace) -> None:
    state = load_state()
    account = find_account_by_id(state, args.account_id)
    state["current_account_id"] = int(account["id"])
    save_state(state)
    print_json(
        {
            "ok": True,
            "action": "switched",
            "current_account_id": state["current_account_id"],
            "account": account_summary(account, state["current_account_id"]),
        }
    )


def command_delete_account(args: argparse.Namespace) -> None:
    state = load_state()
    account = find_account_by_id(state, args.account_id)
    state["accounts"] = [item for item in state["accounts"] if int(item["id"]) != int(args.account_id)]

    cache_path = account_session_path(get_session_cache_path, int(account["id"]))
    if cache_path.exists():
        cache_path.unlink()

    if state.get("current_account_id") == int(account["id"]):
        if state["accounts"]:
            state["current_account_id"] = int(sorted(state["accounts"], key=lambda item: int(item["id"]))[0]["id"])
        else:
            state["current_account_id"] = None

    save_state(state)
    print_json(
        {
            "ok": True,
            "action": "deleted",
            "deleted_account_id": int(account["id"]),
            "current_account_id": state.get("current_account_id"),
            "remaining_accounts": summarize_accounts(state),
        }
    )


def command_check_rollcalls(args: argparse.Namespace) -> None:
    state = load_state()
    account = resolve_active_account(state, args.account_id)
    service = build_service(account)
    session = service.get_session()
    rollcalls = service.fetch_rollcalls(session=session)

    payload_rollcalls = [rollcall_payload(rollcall) for rollcall in rollcalls]
    print_json(
        {
            "ok": True,
            "account": account_summary(account, state.get("current_account_id")),
            "queried_at": now_iso(),
            "rollcall_count": len(payload_rollcalls),
            "answerable_count": sum(1 for item in payload_rollcalls if item["can_answer"]),
            "rollcalls": payload_rollcalls,
        }
    )


def command_answer(args: argparse.Namespace) -> None:
    state = load_state()
    account = resolve_active_account(state, args.account_id)
    service = build_service(account)
    batch = service.answer_active_rollcalls()

    results = []
    for outcome in batch.outcomes:
        results.append(
            {
                "rollcall_id": int(outcome.rollcall.rollcall_id),
                "course_title": outcome.rollcall.course_title,
                "type": outcome.rollcall.type_label,
                "status": outcome.rollcall.status,
                "action": outcome.action,
                "success": bool(outcome.success),
                "message": outcome.message,
                "number_code": outcome.number_code,
                "latitude": outcome.latitude,
                "longitude": outcome.longitude,
                "response_status": outcome.response_status,
            }
        )

    print_json(
        {
            "ok": True,
            "account": account_summary(account, state.get("current_account_id")),
            "queried_at": batch.queried_at.isoformat(timespec="seconds"),
            "rollcall_count": len(batch.rollcalls),
            "results": results,
        }
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage skill-local XMU accounts and answer one-shot rollcalls.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list-accounts", help="List stored XMU accounts.")

    add_parser = subparsers.add_parser("add-account", help="Add or update an XMU account.")
    add_parser.add_argument("--username", required=True, help="XMU username or student number.")
    add_parser.add_argument("--password", required=True, help="XMU password.")
    add_parser.add_argument("--name", help="Optional local display name override.")
    add_parser.add_argument("--set-current", action="store_true", help="Switch to this account after saving.")

    switch_parser = subparsers.add_parser("switch-account", help="Switch the active account.")
    switch_parser.add_argument("--account-id", required=True, type=int)

    delete_parser = subparsers.add_parser("delete-account", help="Delete a stored account.")
    delete_parser.add_argument("--account-id", required=True, type=int)

    check_parser = subparsers.add_parser("check-rollcalls", help="Inspect current rollcalls without answering.")
    check_parser.add_argument("--account-id", type=int, help="Stored account ID to use instead of the active account.")

    answer_parser = subparsers.add_parser("answer", help="Answer active rollcalls once.")
    answer_parser.add_argument("--account-id", type=int, help="Stored account ID to use instead of the active account.")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    try:
        if args.command == "list-accounts":
            command_list_accounts(args)
        elif args.command == "add-account":
            command_add_account(args)
        elif args.command == "switch-account":
            command_switch_account(args)
        elif args.command == "delete-account":
            command_delete_account(args)
        elif args.command == "check-rollcalls":
            command_check_rollcalls(args)
        elif args.command == "answer":
            command_answer(args)
        else:
            parser.error(f"Unsupported command: {args.command}")
    except RuntimeError as exc:
        fail("runtime-error", str(exc))


if __name__ == "__main__":
    main()
