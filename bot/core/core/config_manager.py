from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)\}")


class ConfigManager:
    def __init__(self, config_path: str | Path = "config.yaml") -> None:
        self.config_path = Path(config_path)
        env_path = self.config_path.parent / ".env"
        load_dotenv(dotenv_path=env_path, override=True)

    def load(self) -> dict[str, Any]:
        with self.config_path.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        return self._expand_env(raw)

    def _expand_env(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {k: self._expand_env(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._expand_env(v) for v in value]
        if isinstance(value, str):
            match = ENV_PATTERN.fullmatch(value.strip())
            if match:
                return os.getenv(match.group(1), "")
        return value
