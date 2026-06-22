# Runtime Layout

- Skill root: `/Users/wuqifeng/Desktop/xmu-rollcall-helper/skills/manage-xmu-rollcall`
- Entry point: `scripts/xmu_rollcall`
- Python implementation: `scripts/xmu_rollcall_tool.py`
- Skill-local virtualenv: `.venv/`
- Skill-local data directory: `data/`
- Account store: `data/accounts.json`
- Session cache files: `data/account-<id>.json`
- Repository source imported by the script: `../../xmu-rollcall-cli/xmu_rollcall`

## Supported Commands

```bash
scripts/xmu_rollcall bootstrap
scripts/xmu_rollcall list-accounts
scripts/xmu_rollcall add-account --username <学号> --password <密码> [--name <备注名>] [--set-current]
scripts/xmu_rollcall switch-account --account-id <id>
scripts/xmu_rollcall delete-account --account-id <id>
scripts/xmu_rollcall check-rollcalls [--account-id <id>]
scripts/xmu_rollcall answer [--account-id <id>]
```

## Output Contract

- Every command prints JSON.
- Success responses always include `"ok": true`.
- Failure responses always include `"ok": false`, an `"error"` code, and a human-readable `"message"`.
- `list-accounts` never returns raw passwords.
- `add-account` validates credentials against XMU before writing the account store.
- `check-rollcalls` only queries and reports.
- `answer` performs one-shot answering and returns per-rollcall results, including `number_code` or `latitude` and `longitude` when available.

## Common Error Codes

- `venv-missing`: the skill-local `.venv` does not exist yet
- `repo-not-found`: the repository layout does not match the expected structure
- `state-corrupt`: `data/accounts.json` cannot be parsed
- `no-accounts`: the account store is empty
- `account-not-found`: the requested account ID does not exist
- `ambiguous-selection`: multiple accounts exist and no active account can be chosen safely
