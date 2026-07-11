"""Configuration loader: merges config.yaml with environment variables (.env)."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

try:
    from dotenv import load_dotenv
except ImportError:  # dotenv is optional at runtime
    def load_dotenv(*_a, **_k):  # type: ignore
        return False

# Code root = parent of the src/ directory that contains this file.
ROOT = Path(__file__).resolve().parent.parent

# Base directory for config-scoped data (.env, secrets, Mascot, output, data).
# Defaults to ROOT for the Histold channel. Set by load_config().
_BASE = ROOT


def base_dir() -> Path:
    """The active channel's base directory (for output/secrets/token/data/mascot)."""
    return _BASE


class Config:
    """Lightweight dotted-access wrapper around the parsed YAML config."""

    def __init__(self, data: dict[str, Any]):
        self._data = data

    def get(self, path: str, default: Any = None) -> Any:
        """Fetch a nested value via dotted path, e.g. cfg.get('video.short.fps')."""
        node: Any = self._data
        for key in path.split("."):
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    @property
    def raw(self) -> dict[str, Any]:
        return self._data


def load_config(config_path: str | os.PathLike | None = None) -> Config:
    """Load a channel's .env then config.yaml. Sets the channel base directory.

    The base dir is the config file's folder, so each channel's secrets, output,
    token, and mascot art are isolated. Defaults to ROOT/config.yaml (Histold).
    """
    global _BASE
    path = Path(config_path).resolve() if config_path else ROOT / "config.yaml"
    _BASE = path.parent
    load_dotenv(_BASE / ".env")
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return Config(data)


def env(key: str, default: str | None = None) -> str | None:
    """Convenience accessor for environment variables."""
    return os.environ.get(key, default)


def require_env(key: str) -> str:
    """Fetch a required env var or raise a clear error."""
    val = os.environ.get(key)
    if not val:
        raise RuntimeError(
            f"Missing required environment variable {key!r}. "
            f"Add it to your .env file (see .env.example)."
        )
    return val
