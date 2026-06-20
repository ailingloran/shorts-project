"""
Normalized event-timeline schema for a single WoWS battle.

This is the seam between the *decoder* (whatever turns a .wowsreplay packet
stream into events: replays_unpack, WG-internal tooling, or server telemetry)
and the *scorer* (our IP). Any decoder that can emit this schema plugs straight
into the highlight scorer — so we are not locked to one parser.

A decoder is expected to produce a list[Event] (as dicts or Event objects) plus
a small BattleMeta. The scorer consumes only this schema, never raw packets.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class EventType(str, Enum):
    DAMAGE = "damage"          # damage dealt by the focus player in one salvo/hit
    KILL = "kill"              # focus player killed an enemy (a "frag")
    CITADEL = "citadel"        # citadel hit ribbon
    TORPEDO_HIT = "torpedo_hit"
    DETONATION = "detonation"  # focus player detonated an enemy
    ACHIEVEMENT = "achievement"  # Kraken, Dreadnought, Devastating Strike, ...
    HP = "hp"                  # focus player HP sample (for comeback/low-HP arcs)
    CAP = "cap"                # base capture / defend contribution
    DEATH = "death"            # focus player died


@dataclass
class Event:
    t: float                   # seconds since battle start
    type: EventType
    value: float = 0.0         # damage amount, hp value, etc. (event-dependent)
    target: str | None = None  # victim/target player name where relevant
    label: str | None = None   # e.g. achievement name "Kraken"


@dataclass
class BattleMeta:
    """The subset of header metadata the scorer/card needs."""
    client_version: str
    map_name: str
    game_mode: str
    focus_player: str          # whose POV the replay is (the recording player)
    focus_ship: str            # e.g. "PVSD710-La-Pampa"
    duration_s: int
    result: str | None = None  # "win" | "loss" | "draw" | None if unknown
    full_hp: float = 0.0       # focus ship max HP, for HP-fraction math


@dataclass
class Battle:
    meta: BattleMeta
    events: list[Event] = field(default_factory=list)

    @classmethod
    def from_dicts(cls, meta: dict, events: list[dict]) -> "Battle":
        return cls(
            meta=BattleMeta(**meta),
            events=[
                Event(
                    t=float(e["t"]),
                    type=EventType(e["type"]),
                    value=float(e.get("value", 0.0)),
                    target=e.get("target"),
                    label=e.get("label"),
                )
                for e in events
            ],
        )
