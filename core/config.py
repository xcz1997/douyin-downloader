"""Configuration loader for the Douyin downloader.

Handles YAML loading, old-format migration, validation, and default generation.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

from core.models import AppConfig, DownloadOptions


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

_DEFAULTS: dict[str, Any] = {
    "links": [],
    "save_path": "./downloads",
    "cookie": None,
    "cookie_mode": "none",
    "mode": ["post"],
    "limit": {"post": 0},
    "time_range": {"start": "", "end": ""},
    "download": {"music": True, "cover": True, "metadata": True},
    "incremental": {"post": False},
    "concurrency": 5,
    "retry": 3,
    "database": True,
    "log_level": "INFO",
}


# ---------------------------------------------------------------------------
# Old → new field migration map (applied before merging with defaults)
# ---------------------------------------------------------------------------

_FIELD_RENAMES: dict[str, str] = {
    "link": "links",
    "path": "save_path",
    "output_dir": "save_path",
    # "cookies" is handled specially (also drives cookie_mode detection)
    # "thread" → "concurrency"
    "thread": "concurrency",
    # "retry_times" → "retry"
    "retry_times": "retry",
    # "number" → "limit"
    "number": "limit",
    # "increase" → "incremental"
    "increase": "incremental",
}


def _detect_cookie_mode(cookie_value: Any) -> str:
    """Derive cookie_mode from the raw cookie value.

    Args:
        cookie_value: The raw value from the config file.

    Returns:
        One of "dict", "auto", "string", or "none".
    """
    if isinstance(cookie_value, dict):
        return "dict"
    if cookie_value == "auto":
        return "auto"
    if isinstance(cookie_value, str) and cookie_value.strip():
        return "string"
    return "none"


def _migrate_old_format(raw: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of *raw* with all legacy field names rewritten to new names.

    Also promotes top-level ``music``, ``cover``, ``json`` into the nested
    ``download`` block, and flattens ``start_time`` / ``end_time`` into
    ``time_range``.

    Args:
        raw: Raw dict loaded from YAML.

    Returns:
        Migrated dict using new field names.
    """
    data = copy.deepcopy(raw)

    # Simple renames
    for old_key, new_key in _FIELD_RENAMES.items():
        if old_key in data:
            # Don't overwrite if new key already present
            if new_key not in data:
                data[new_key] = data.pop(old_key)
            else:
                data.pop(old_key)

    # "cookies" (old name) → "cookie" (new name)
    if "cookies" in data and "cookie" not in data:
        data["cookie"] = data.pop("cookies")
    elif "cookies" in data:
        data.pop("cookies")

    # Promote top-level download booleans into nested download block
    old_download_keys = {"music", "cover"}
    old_metadata_key = "json"
    needs_promotion = any(k in data for k in old_download_keys) or old_metadata_key in data
    if needs_promotion:
        download_block = data.setdefault("download", {})
        for key in old_download_keys:
            if key in data and key not in download_block:
                download_block[key] = data.pop(key)
            elif key in data:
                data.pop(key)
        if old_metadata_key in data:
            if "metadata" not in download_block:
                download_block["metadata"] = data.pop(old_metadata_key)
            else:
                data.pop(old_metadata_key)

    # Promote top-level start_time / end_time into time_range block
    if "start_time" in data or "end_time" in data:
        time_range = data.setdefault("time_range", {})
        if "start_time" in data and "start" not in time_range:
            time_range["start"] = data.pop("start_time")
        elif "start_time" in data:
            data.pop("start_time")
        if "end_time" in data and "end" not in time_range:
            time_range["end"] = data.pop("end_time")
        elif "end_time" in data:
            data.pop("end_time")

    return data


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge *override* into *base*, returning a new dict.

    Args:
        base: Base dictionary (defaults).
        override: Override dictionary (user config).

    Returns:
        Merged dictionary with override values taking precedence.
    """
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


class ConfigLoader:
    """Load, migrate, validate, and save application configuration.

    Args:
        config_path: Path to the YAML configuration file.

    Example::

        loader = ConfigLoader("config.yml")
        config = loader.load()
    """

    def __init__(self, config_path: str) -> None:
        self._path = Path(config_path)
        self._raw: dict[str, Any] | None = None

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def load(self) -> AppConfig:
        """Load and parse the configuration file into an :class:`AppConfig`.

        Returns:
            Fully populated :class:`AppConfig` instance.

        Raises:
            FileNotFoundError: If the config file does not exist.
            yaml.YAMLError: If the file contains invalid YAML.
        """
        raw = self._read_yaml()
        migrated = _migrate_old_format(raw)
        merged = _deep_merge(_DEFAULTS, migrated)
        self._raw = merged
        return self._build_config(merged)

    def validate(self) -> list[str]:
        """Validate the configuration file and return a list of error messages.

        Loads the file if not already loaded. An empty list means the
        configuration is valid.

        Returns:
            List of human-readable error strings. Empty if valid.
        """
        if self._raw is None:
            try:
                raw = self._read_yaml()
                migrated = _migrate_old_format(raw)
                self._raw = _deep_merge(_DEFAULTS, migrated)
            except Exception as exc:  # noqa: BLE001
                return [f"Failed to load config: {exc}"]

        errors: list[str] = []
        links = self._raw.get("links", [])
        if not links or links == []:
            errors.append("links: no download links specified")

        return errors

    @staticmethod
    def generate_default(path: str) -> None:
        """Write a default configuration YAML file to *path*.

        Args:
            path: Destination file path. Parent directories are created
                  automatically.
        """
        dest = Path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)

        default_content: dict[str, Any] = {
            "links": [],
            "save_path": "./downloads",
            "cookie": None,
            "mode": ["post"],
            "limit": {"post": 0},
            "time_range": {"start": "", "end": ""},
            "download": {"music": True, "cover": True, "metadata": True},
            "incremental": {"post": False},
            "concurrency": 5,
            "retry": 3,
            "database": True,
            "log_level": "INFO",
        }
        with dest.open("w", encoding="utf-8") as fh:
            yaml.dump(default_content, fh, allow_unicode=True, default_flow_style=False, sort_keys=False)

    def save_cookie(self, cookie_str: str) -> None:
        """Update the ``cookie`` field in the config file on disk.

        Reads the file, updates the cookie value, and writes it back. Supports
        both old-style (``cookies``) and new-style (``cookie``) keys.

        Args:
            cookie_str: New cookie string value to persist.
        """
        raw = self._read_yaml()
        # Prefer the new key; fall back to whichever key exists
        if "cookies" in raw and "cookie" not in raw:
            raw["cookies"] = cookie_str
        else:
            raw["cookie"] = cookie_str

        with self._path.open("w", encoding="utf-8") as fh:
            yaml.dump(raw, fh, allow_unicode=True, default_flow_style=False, sort_keys=False)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _read_yaml(self) -> dict[str, Any]:
        """Read and parse the YAML file, returning a plain dict.

        Returns:
            Parsed YAML content as a dictionary.

        Raises:
            FileNotFoundError: If the config path does not exist.
        """
        with self._path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        return data if isinstance(data, dict) else {}

    def _build_config(self, data: dict[str, Any]) -> AppConfig:
        """Construct an :class:`AppConfig` from a fully-merged data dict.

        Handles all field-name translations between the config file schema
        and the :class:`AppConfig` dataclass fields:

        - ``concurrency`` → :attr:`AppConfig.thread`
        - ``retry`` → :attr:`AppConfig.retry_times`
        - ``limit`` → :attr:`AppConfig.number`
        - ``incremental`` → :attr:`AppConfig.increase`
        - ``download.metadata`` → :attr:`DownloadOptions.json`

        Args:
            data: Fully merged configuration dictionary.

        Returns:
            Populated :class:`AppConfig` instance.
        """
        # Normalise links: wrap bare string in a list
        links_raw = data.get("links", [])
        if isinstance(links_raw, str):
            links: list[str] = [links_raw] if links_raw else []
        else:
            links = list(links_raw) if links_raw else []

        # save_path
        save_path = Path(data.get("save_path", "./downloads"))

        # Cookie / cookie_mode
        cookie_value = data.get("cookie")
        cookie_mode = _detect_cookie_mode(cookie_value)

        # mode
        mode_raw = data.get("mode", ["post"])
        mode: list[str] = [mode_raw] if isinstance(mode_raw, str) else list(mode_raw)

        # time_range block (post-migration these live here)
        time_range = data.get("time_range", {})
        start_time: str | None = time_range.get("start") or None
        end_time: str | None = time_range.get("end") or None

        # If time values survived as top-level (shouldn't after migration, but be safe)
        if start_time is None:
            raw_start = data.get("start_time")
            start_time = raw_start if raw_start else None
        if end_time is None:
            raw_end = data.get("end_time")
            end_time = raw_end if raw_end else None

        # download sub-block → DownloadOptions
        dl_block = data.get("download", {})
        download_opts = DownloadOptions(
            music=bool(dl_block.get("music", True)),
            cover=bool(dl_block.get("cover", True)),
            json=bool(dl_block.get("metadata", True)),
        )

        return AppConfig(
            links=links,
            save_path=save_path,
            cookies=cookie_value,
            cookie_mode=cookie_mode,
            mode=mode,
            number=data.get("limit", {"post": 0}),
            start_time=start_time,
            end_time=end_time,
            download=download_opts,
            thread=int(data.get("concurrency", 5)),
            database=bool(data.get("database", True)),
            increase=data.get("incremental", {"post": False}),
            retry_times=int(data.get("retry", 3)),
            log_level=str(data.get("log_level", "INFO")),
        )
