"""Configuration loader for the Douyin downloader.

Handles YAML loading, old-format migration, validation, and default generation.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

from core.models import AppConfig, DownloadOptions, SubtitleConfig, TranscribeConfig, XHSConfig


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
    "subtitle": {
        "enabled": False,
        "sources": ["track", "ocr", "asr"],
        "asr": {"model": "0.6b"},
        "ocr": {"interval": 0.5, "similarity": 0.7},
    },
    "transcribe": {
        "enabled": False,
        "auto_after_download": False,
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-vl-max",
        "api_key_env": "DASHSCOPE_API_KEY",
        "api_key": "",
        "max_images": 0,
        "overwrite": False,
        "timeout": 60,
        "retry": 2,
    },
    "xhs": {"profile_dir": ""},
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


def _cookie_mode_from_dict(
    cookies: dict[str, str], raw_data: dict[str, object],
) -> str:
    """Derive a legacy-compatible cookie_mode from the final cookies dict.

    Args:
        cookies: Post-migration ``{platform: cookie_string}`` dict.
        raw_data: The full (post-migration) config dict, used for future
            extensibility.

    Returns:
        One of ``"dict"``, ``"auto"``, ``"string"``, ``"none"``.
    """
    if not cookies:
        return "none"
    # Explicit multi-platform dict → "dict"; single-platform (from old
    # format migration) → "string" so downstream CookieManager keeps
    # its single-cookie behavior unless it opts in.
    if len(cookies) > 1:
        return "dict"
    sole_value = next(iter(cookies.values()), "")
    if sole_value == "auto":
        return "auto"
    return "string"


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

    # Cookie handling: three supported forms
    # (a) new multi-platform:   cookies: {douyin: "...", xhs: "..."}
    # (b) old single string:    cookie: "msToken=abc; ..."
    # (c) legacy plural string: cookies: "msToken=abc; ..." (pre-v4.0)
    # New (a) always wins; (b) and (c) migrate to {"douyin": "..."}.
    new_cookies: dict[str, str] = {}

    if "cookies" in data and isinstance(data["cookies"], dict):
        # form (a) — keep as-is, drop legacy singular if it sneaked in
        new_cookies = {k: v for k, v in data["cookies"].items() if v}
        data.pop("cookie", None)
    elif "cookies" in data and isinstance(data["cookies"], str):
        # form (c) — plural string, migrate to douyin slot
        if data["cookies"].strip():
            new_cookies = {"douyin": data["cookies"]}
        data.pop("cookie", None)
    elif "cookie" in data and isinstance(data["cookie"], str):
        # form (b) — singular string, migrate to douyin slot
        if data["cookie"].strip():
            new_cookies = {"douyin": data["cookie"]}
    elif "cookie" in data and isinstance(data["cookie"], dict):
        # tolerate someone who wrote dict under `cookie:` singular
        new_cookies = {k: v for k, v in data["cookie"].items() if v}

    data.pop("cookies", None)
    data.pop("cookie", None)
    data["cookies"] = new_cookies

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
            # 字幕提取（默认关闭，改 enabled: true 并按需安装依赖后生效）
            # sources 可选: track（平台字幕轨，无需额外依赖）、
            #   ocr（硬字幕识别，需 opencv-python + rapidocr-onnxruntime）、
            #   asr（语音转写，需 mlx-qwen3-asr，仅 Apple Silicon）
            "subtitle": {
                "enabled": False,
                "sources": ["track", "ocr", "asr"],
                "asr": {"model": "0.6b"},
                "ocr": {"interval": 0.5, "similarity": 0.7},
            },
            # 图片转录（默认关闭；改 enabled: true 并设置环境变量
            #   <api_key_env> 指定的 key 后生效）。走 OpenAI-compatible
            #   vision 协议，base_url/model 可换任意兼容服务。
            #   auto_after_download: true 时下载图文笔记后自动转录。
            "transcribe": {
                "enabled": False,
                "auto_after_download": False,
                "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "model": "qwen-vl-max",
                "api_key_env": "DASHSCOPE_API_KEY",
                "api_key": "",
                "max_images": 0,
                "overwrite": False,
                "timeout": 60,
                "retry": 2,
            },
            "xhs": {"profile_dir": ""},
        }
        with dest.open("w", encoding="utf-8") as fh:
            yaml.dump(default_content, fh, allow_unicode=True, default_flow_style=False, sort_keys=False)

    def save_cookie(self, cookie_str: str, platform: str = "douyin") -> None:
        """Update the cookie for *platform* in the config file on disk.

        Prefers the new ``cookies: {platform: ...}`` nested form when
        the file already has it; falls back to singular ``cookie:`` form
        only when the file has no ``cookies:`` block AND *platform* is
        ``"douyin"`` (the historical default). Other platforms always
        write under ``cookies.{platform}``.

        Args:
            cookie_str: New cookie string value to persist.
            platform: Target platform identifier (default ``"douyin"``).
        """
        raw = self._read_yaml()

        if "cookies" in raw and isinstance(raw["cookies"], dict):
            raw["cookies"][platform] = cookie_str
        elif platform == "douyin" and isinstance(raw.get("cookie"), str):
            raw["cookie"] = cookie_str
        else:
            cookies_block = raw.setdefault("cookies", {})
            if not isinstance(cookies_block, dict):
                cookies_block = {}
                raw["cookies"] = cookies_block
            cookies_block[platform] = cookie_str
            if platform == "douyin" and "cookie" in raw:
                del raw["cookie"]

        with self._path.open("w", encoding="utf-8") as fh:
            yaml.dump(
                raw, fh, allow_unicode=True,
                default_flow_style=False, sort_keys=False,
            )

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
        # After migration, cookies is always dict[str, str] (possibly empty).
        cookies_dict = data.get("cookies", {})
        if not isinstance(cookies_dict, dict):
            cookies_dict = {}
        cookie_mode = _cookie_mode_from_dict(cookies_dict, data)

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

        # subtitle sub-block → SubtitleConfig
        _sub = data.get("subtitle", {}) or {}
        subtitle = SubtitleConfig(
            enabled=bool(_sub.get("enabled", False)),
            sources=list(_sub.get("sources", ["track", "ocr", "asr"])),
            asr_model=str((_sub.get("asr", {}) or {}).get("model", "0.6b")),
            ocr_interval=float((_sub.get("ocr", {}) or {}).get("interval", 0.5)),
            ocr_similarity=float((_sub.get("ocr", {}) or {}).get("similarity", 0.7)),
        )

        # transcribe sub-block → TranscribeConfig
        _tr = data.get("transcribe", {}) or {}
        transcribe = TranscribeConfig(
            enabled=bool(_tr.get("enabled", False)),
            auto_after_download=bool(_tr.get("auto_after_download", False)),
            base_url=str(_tr.get("base_url", TranscribeConfig.base_url)),
            model=str(_tr.get("model", TranscribeConfig.model)),
            api_key_env=str(_tr.get("api_key_env", TranscribeConfig.api_key_env)),
            api_key=str(_tr.get("api_key", "")),
            max_images=int(_tr.get("max_images", 0)),
            overwrite=bool(_tr.get("overwrite", False)),
            timeout=int(_tr.get("timeout", 60)),
            retry=int(_tr.get("retry", 2)),
        )

        # xhs sub-block → XHSConfig
        _xhs = data.get("xhs", {}) or {}
        xhs = XHSConfig(profile_dir=str(_xhs.get("profile_dir", "") or ""))

        return AppConfig(
            links=links,
            save_path=save_path,
            cookies=cookies_dict,
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
            subtitle=subtitle,
            transcribe=transcribe,
            xhs=xhs,
        )
