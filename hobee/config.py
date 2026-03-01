"""统一配置加载模块。

加载优先级：环境变量 > config.json > 默认值。
敏感值（API 密钥等）只从环境变量或配置文件读取，代码中不含默认值。

Usage:
    config = HobbyConfig("podcast")
    whisper_url = config.get("whisper_endpoint")
    storage = config.create_storage()
"""

import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

# OpenClaw workspace 默认根目录
DEFAULT_WORKSPACE_ROOT = Path.home() / ".openclaw" / "workspace"


class HobbyConfig:
    """兴趣系统统一配置。

    Args:
        hobby_name: 兴趣源名称（podcast / youtube / twitter）。
        workspace_root: OpenClaw workspace 根目录，默认 ~/.openclaw/workspace。
        env_file: .env 文件路径，默认为项目根目录的 .env。
    """

    def __init__(
        self,
        hobby_name: str,
        workspace_root: Optional[Path] = None,
        env_file: Optional[Path] = None,
    ):
        self.hobby_name = hobby_name
        self.workspace_root = workspace_root or Path(
            os.environ.get("OPENCLAW_WORKSPACE", str(DEFAULT_WORKSPACE_ROOT))
        )
        self.workspace = self.workspace_root / f"{hobby_name}-hobby"
        self.hobby_dir = self.workspace_root / "hobby"

        # Load .env file if exists (simple key=value parsing)
        if env_file and env_file.exists():
            self._load_env_file(env_file)

        # Load hobby-specific config.json
        self._config: dict = {}
        config_file = self.workspace / "config.json"
        if config_file.exists():
            with open(config_file) as f:
                self._config = json.load(f)
            log.info("Loaded config from %s", config_file)

    def _load_env_file(self, env_file: Path) -> None:
        """从 .env 文件加载环境变量（不覆盖已有值）。"""
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, _, value = line.partition("=")
                    key = key.strip()
                    value = value.strip().strip("'\"")
                    if key and key not in os.environ:
                        os.environ[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        """获取配置值。优先级：环境变量 > config.json > default。"""
        # 1. 环境变量（大写，下划线分隔）
        env_key = key.upper().replace(".", "_")
        env_val = os.environ.get(env_key)
        if env_val is not None:
            return env_val

        # 2. config.json
        if key in self._config:
            return self._config[key]

        return default

    def require(self, key: str) -> str:
        """获取必需的配置值，缺失则报错退出。"""
        val = self.get(key)
        if val is None:
            raise ValueError(
                f"Missing required config: '{key}'. "
                f"Set env var '{key.upper()}' or add to config.json."
            )
        return val

    @property
    def feishu_app_id(self) -> str:
        return self.require("feishu_app_id")

    @property
    def feishu_app_secret(self) -> str:
        return self.require("feishu_app_secret")

    @property
    def feishu_app_token(self) -> str:
        return self.require("feishu_app_token")

    @property
    def feishu_table_id(self) -> str:
        """从 {HOBBY}_TABLE_ID 或 config 中获取表格 ID。"""
        env_key = f"{self.hobby_name.upper()}_TABLE_ID"
        val = os.environ.get(env_key) or self._config.get("feishu_table_id")
        if not val:
            raise ValueError(
                f"Missing table ID. Set env var '{env_key}' or config key 'feishu_table_id'."
            )
        return val

    @property
    def feishu_chat_id(self) -> Optional[str]:
        return self.get("feishu_chat_id")

    @property
    def llm_endpoint(self) -> str:
        return self.require("llm_endpoint")

    @property
    def llm_api_key(self) -> str:
        return self.require("llm_api_key")

    @property
    def llm_model(self) -> str:
        return self.get("llm_model", "claude-sonnet-4-20250514")

    def create_storage(self):
        """根据配置创建存储后端实例。

        默认使用 SQLite（零配置）。如果设置了飞书凭证，可切换到飞书后端。
        """
        backend_type = self.get("storage_backend")

        # 自动检测：如果未显式设置 backend 但飞书凭证齐全，使用飞书
        if backend_type is None:
            if os.environ.get("FEISHU_APP_ID") and os.environ.get("FEISHU_APP_SECRET"):
                backend_type = "feishu"
            else:
                backend_type = "sqlite"

        if backend_type == "sqlite":
            from .storage.sqlite import SQLiteBackend

            db_path = str(self.workspace / "data.db")
            return SQLiteBackend(db_path)
        elif backend_type == "feishu":
            from .storage.feishu import FeishuBitableBackend

            return FeishuBitableBackend(
                app_id=self.feishu_app_id,
                app_secret=self.feishu_app_secret,
                app_token=self.feishu_app_token,
                table_id=self.feishu_table_id,
            )
        else:
            raise ValueError(
                f"Unknown storage backend: '{backend_type}'. "
                "Supported: 'sqlite' (default), 'feishu'. "
                "See docs/customization.md for adding your own backend."
            )

    # ------------------------------------------------------------------
    # Workspace paths
    # ------------------------------------------------------------------

    @property
    def pending_shares_file(self) -> Path:
        return self.workspace / "pending-shares.json"

    @property
    def config_file(self) -> Path:
        return self.workspace / "config.json"

    @property
    def log_dir(self) -> Path:
        return self.hobby_dir / "logs"

    @property
    def mind_state_file(self) -> Path:
        return self.hobby_dir / "mind-state.json"

    @property
    def user_signals_file(self) -> Path:
        return self.hobby_dir / "user-signals.json"

    @property
    def alerts_file(self) -> Path:
        return self.hobby_dir / "watchdog-alerts.json"
