"""
Resolve GameParams ids to readable names using replay_unpack's bundled BY_ID table
(no client extraction needed). The table's `index` is the internal code
(e.g. "PFSC109_Saint_Louis", "PCH016_FirstBlood"); we strip the prefix and
de-camel/underscore it into a display name ("Saint Louis", "First Blood").

These aren't the fully localized strings (those live in the client's .mo files),
but they read cleanly and cover every ship/achievement in the table.
"""
from __future__ import annotations

import importlib
import re

_PREFIX = re.compile(r"^[A-Z]{2,5}\d{2,4}[_-]")     # PFSC109_, PCH016_, PZSB709-
_CAMEL = re.compile(r"(?<=[a-z])(?=[A-Z])")
_BY_ID: dict[str, dict] = {}


def _table(version_parts: list[str]) -> dict:
    v = "_".join(version_parts[:3])
    if v not in _BY_ID:
        try:
            _BY_ID[v] = importlib.import_module(
                f"replay_unpack.clients.wows.versions.{v}.game_params").BY_ID
        except Exception:
            _BY_ID[v] = {}
    return _BY_ID[v]


def _display(index: str | None) -> str | None:
    if not index:
        return None
    name = _PREFIX.sub("", index).replace("_", " ")
    return _CAMEL.sub(" ", name).strip() or None


def ship_name(version_parts: list[str], param_id) -> str | None:
    e = _table(version_parts).get(param_id)
    return _display(e["index"]) if e else None


def achievement_name(version_parts: list[str], ach_id) -> str | None:
    e = _table(version_parts).get(ach_id)
    return _display(e["index"]) if e else None
