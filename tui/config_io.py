"""Minimal config.yml field write-back for the Settings panel.

Mirrors ConfigLoader.save_cookie's existing yaml.dump approach (which
also drops comments — known pre-existing project behavior; not "fixed"
here). Top-level keys are replaced wholesale by `updates` (shallow
merge): a dict value under a key fully replaces that key's value.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def save_config_fields(path: str, updates: dict[str, Any]) -> None:
    p = Path(path)
    if p.exists():
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    else:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    raw.update(updates)
    p.write_text(
        yaml.dump(raw, allow_unicode=True, default_flow_style=False,
                  sort_keys=False),
        encoding="utf-8",
    )
