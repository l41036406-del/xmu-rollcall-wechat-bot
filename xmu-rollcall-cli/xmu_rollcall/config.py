import os
import re
from pathlib import Path


def get_config_dir() -> Path:
    if env_path := os.environ.get("XMU_ROLLCALL_CONFIG_DIR"):
        return Path(env_path)

    try:
        home_config_dir = Path.home() / ".xmu_rollcall"
        home_config_dir.mkdir(parents=True, exist_ok=True)
        test_file = home_config_dir / ".test_write"
        test_file.touch()
        test_file.unlink()
        return home_config_dir
    except (OSError, PermissionError, RuntimeError):
        pass

    return Path.cwd() / ".xmu_rollcall"


CONFIG_DIR = get_config_dir()


def ensure_config_dir() -> None:
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    except (OSError, PermissionError) as exc:
        raise RuntimeError(
            f"无法创建配置目录 {CONFIG_DIR}: {exc}\n"
            "提示：可以设置环境变量 XMU_ROLLCALL_CONFIG_DIR 指定配置目录位置"
        ) from exc


def get_session_cache_path(cache_key) -> str:
    ensure_config_dir()
    safe_key = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(cache_key)).strip("._")
    if not safe_key:
        safe_key = "session"
    return str(CONFIG_DIR / f"{safe_key}.json")
