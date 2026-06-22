---
name: manage-xmu-rollcall
description: Manage local XMU TronClass sign-in accounts and perform one-shot rollcall checks or answers without any WeChat integration. Use when Codex needs to bootstrap the repo-local sign-in skill, add or update stored XMU accounts, delete or switch the active account, inspect current rollcalls, or answer active XMU 签到 directly from an agent while keeping all runtime files inside this skill directory.
---

# Manage XMU Rollcall

Use the bundled scripts to keep a skill-local account store and run one-shot XMU rollcall operations from the repository directly.

## Quick Start

1. Run `scripts/xmu_rollcall bootstrap` once if the skill-local `.venv` does not exist yet.
2. Run `scripts/xmu_rollcall list-accounts` to inspect the current account store in `data/accounts.json`.
3. Add or update an account with `scripts/xmu_rollcall add-account --username <学号> --password <密码>`.
4. Switch or delete accounts with `scripts/xmu_rollcall switch-account --account-id <id>` or `scripts/xmu_rollcall delete-account --account-id <id>`.
5. Run `scripts/xmu_rollcall check-rollcalls` to inspect current rollcalls before answering unless the user explicitly asked to answer immediately.
6. Run `scripts/xmu_rollcall answer` to answer active rollcalls for the selected account.

Read [references/runtime.md](references/runtime.md) only when the repository layout, runtime files, or JSON output structure is unclear.

## Rules

- Use `scripts/xmu_rollcall` as the entrypoint. Do not edit `data/accounts.json` by hand unless the script itself is broken.
- Keep all runtime state inside this skill directory. The account store, session cache, and skill-local `.venv` all live here.
- Never repeat stored passwords in messages or summaries.
- If more than one account exists and there is no active account, ask a short clarification or switch the active account before answering.
- If `check-rollcalls` or `answer` returns zero rollcalls, say so plainly and stop.
- If `answer` succeeds for a number rollcall, report the `number_code`. If it succeeds for a radar rollcall, report the solved latitude and longitude.
- If bootstrap or login fails, surface the exact error message instead of paraphrasing it away.
