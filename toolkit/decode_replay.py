"""
Real packet-stream decoder: .wowsreplay  ->  events.py timeline.

Wraps Monstrofil's `replay_unpack` and adapts its battle-controller callbacks into
our normalized event schema, WITH timestamps — which the stock controller does not
retain. We do that by subclassing the player to stash the current packet time and
subscribing our own timed handlers.

Current-version support: replay_unpack lags the live patch, but minor patches keep
the packet protocol stable, so `_ensure_version_support()` clones the newest shipped
schema for the current version if it's missing, and `decode()` tolerates new-content
ids that only break the optional battle-report. Net: the live patch's replays decode.

Key product behaviour proven here: the *recording* player is often not the star.
We map every vehicle to its player, then pick the focus POV (default: the real
top-fragger) — because in replay playback you can spectate any player, so one
replay can yield a clip from whichever POV is most clippable.

CLI:
    python decode_replay.py <replay.wowsreplay>                 # auto-pick best POV
    python decode_replay.py <replay.wowsreplay> --focus mythic37
    python decode_replay.py <replay.wowsreplay> --emit timeline.json   # schema only
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path

import replay_unpack
from replay_unpack.replay_reader import ReplayReader
from replay_unpack.clients.wows import ReplayPlayer
from replay_unpack.clients.wows.network.packets import Position, PlayerPosition
from replay_unpack.core.entity import Entity

from events import Battle, BattleMeta
from scorer import score_battle
from card import generate_card

# replay_unpack death-type ids we care about (from v15.4 constants.DEATH_TYPES)
DEATH_DETONATE = 15

_VERSIONS_DIR = os.path.join(os.path.dirname(replay_unpack.__file__),
                             "clients", "wows", "versions")


def _ensure_version_support(version_parts: list[str]) -> str:
    """Current-version assumption: we only target the live patch's replays.

    replay_unpack ships hand-made schemas per game version and lags the newest
    patch. But WoWS minor patches keep the BigWorld *packet protocol* stable —
    only content id-tables (ships/achievements in game_params.py) change, and
    those are handled by the get_info() fallback in decode(). So if the exact
    version's schema is missing, clone the newest one replay_unpack does have.
    Writes into the installed package (local, idempotent); never overwrites an
    existing version dir, so a real future schema takes precedence.
    """
    target = "_".join(version_parts[:3])               # e.g. "15_5_0"
    tdir = os.path.join(_VERSIONS_DIR, target)
    if os.path.isdir(tdir):
        return target

    def parse(name):
        m = re.match(r"^(\d+)_(\d+)_(\d+)$", name)
        return tuple(map(int, m.groups())) if m else None

    cands = [(parse(d), d) for d in os.listdir(_VERSIONS_DIR) if parse(d)]
    if not cands:
        return target
    newest = max(cands)[1]
    shutil.copytree(os.path.join(_VERSIONS_DIR, newest), tdir,
                    ignore=shutil.ignore_patterns("__pycache__"))
    sys.stderr.write(f"[decode] version {target} not shipped; reusing newest schema {newest}\n")
    return target


class _TimedPlayer(ReplayPlayer):
    """ReplayPlayer that records the packet time of each battle event."""

    def __init__(self, version, capture_positions: bool = False):
        self.clock = 0.0
        self.kills: list[tuple] = []         # (t, killed_vid, fragger_vid, death_type)
        self.damage: list[tuple] = []        # (t, attacker_vid, victim_vid, amount)
        self.achievements: list[tuple] = []  # (t, avatar_id, achievement_id)
        self.positions: list[tuple] = []     # (t, entity_id, x, z)  [only if enabled]
        self._capture_positions = capture_positions
        super().__init__(version)
        Entity.subscribe_method_call("Avatar", "receiveVehicleDeath", self._on_death)
        Entity.subscribe_method_call("Avatar", "onAchievementEarned", self._on_ach)
        Entity.subscribe_method_call("Vehicle", "receiveDamagesOnShip", self._on_dmg)

    def _process_packet(self, time, packet):
        self.clock = time
        if self._capture_positions:
            if isinstance(packet, Position):
                p = packet.position
                self.positions.append((round(time, 2), packet.entityId,
                                       getattr(p, "x", 0.0), getattr(p, "z", 0.0)))
            elif isinstance(packet, PlayerPosition) and packet.entityId1 and not packet.entityId2:
                # the controlled/recording player reports via PlayerPosition (absolute
                # when entityId2 == 0), not Position — capture it so the focus POV shows up
                p = packet.position
                self.positions.append((round(time, 2), packet.entityId1,
                                       getattr(p, "x", 0.0), getattr(p, "z", 0.0)))
        super()._process_packet(time, packet)

    def _on_death(self, avatar, killed, fragger, type_death):
        self.kills.append((round(self.clock, 1), killed, fragger, type_death))

    def _on_ach(self, avatar, avatar_id, achievement_id):
        self.achievements.append((round(self.clock, 1), avatar_id, achievement_id))

    def _on_dmg(self, vehicle, damages):
        for d in damages:
            self.damage.append((round(self.clock, 1), vehicle.id, d["vehicleID"], d["damage"]))


def decode(path: str, capture_positions: bool = False):
    """Return (engine_data, info_dict, timed_player)."""
    replay = ReplayReader(path).get_replay_data()
    version = replay.engine_data["clientVersionFromExe"].replace(" ", "").split(",")
    _ensure_version_support(version)
    player = _TimedPlayer(version, capture_positions=capture_positions)
    player.play(replay.decrypted_data)
    ctrl = player._battle_controller
    try:
        gi = ctrl.get_info()
    except Exception:
        # New-content ids (e.g. a just-released ship) can break the optional
        # battle-report. We don't use it — fall back to the data we do need.
        gi = {"players": ctrl._players.get_info(),
              "player_id": ctrl._player_id,
              "death_map": list(getattr(ctrl, "_death_map", []))}
    return replay.engine_data, gi, player


def build_vehicle_map(gi: dict) -> dict[int, dict]:
    """vehicle_id -> {name, team, frags, alive, max_hp}. vehicle_id == avatarId+1."""
    vmap = {}
    for pl in gi["players"].values():
        vmap[pl["shipId"]] = {
            "name": pl.get("name"),
            "team": pl.get("teamId"),
            "frags": pl.get("fragsCount", 0),
            "alive": pl.get("isAlive"),
            "max_hp": pl.get("maxHealth", 0),
            "avatar_id": pl.get("avatarId"),
            "ship_param_id": pl.get("shipParamsId"),   # -> GameParams BY_ID for the ship name
        }
    return vmap


def to_timeline(player: _TimedPlayer, vmap: dict, focus_vid: int) -> list[dict]:
    """Project all timed events onto the chosen focus vehicle, in events.py schema."""
    out: list[dict] = []
    for t, killed, fragger, td in player.kills:
        if fragger == focus_vid:
            victim = vmap.get(killed, {}).get("name") or str(killed)
            out.append({"t": t, "type": "kill", "target": victim})
            if td == DEATH_DETONATE:
                out.append({"t": t, "type": "detonation", "target": victim})
        if killed == focus_vid:
            out.append({"t": t, "type": "death"})
    for t, attacker, victim, amount in player.damage:
        if attacker == focus_vid and amount > 0:
            out.append({"t": t, "type": "damage", "value": amount,
                        "target": vmap.get(victim, {}).get("name") or str(victim)})
    focus_avatar = vmap.get(focus_vid, {}).get("avatar_id")
    for t, avatar_id, ach_id in player.achievements:
        # achievements arrive keyed by an account id; match via the players table
        out.append({"t": t, "type": "achievement", "label": f"achievement:{ach_id}",
                    "_avatar_hint": avatar_id})
    out.sort(key=lambda e: e["t"])
    return out


def pick_focus(vmap: dict, prefer_name: str | None) -> int:
    if prefer_name:
        for vid, v in vmap.items():
            if str(v["name"]).lower() == prefer_name.lower():
                return vid
        raise SystemExit(f"player '{prefer_name}' not found in this replay")
    # default: the real top-fragger (the clippable POV)
    return max(vmap, key=lambda vid: (vmap[vid]["frags"], -vid))


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Decode a .wowsreplay into a scored timeline")
    ap.add_argument("replay")
    ap.add_argument("--focus", help="player name to use as POV (default: top fragger)")
    ap.add_argument("--emit", help="write the events.py timeline JSON here and stop")
    ap.add_argument("--top", type=int, default=5)
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    engine, gi, player = decode(args.replay)
    vmap = build_vehicle_map(gi)
    focus_vid = pick_focus(vmap, args.focus)
    focus = vmap[focus_vid]

    print(f"# {Path(args.replay).name}")
    print(f"  version={engine.get('clientVersionFromExe')}  map={engine.get('mapDisplayName')}  "
          f"mode={engine.get('scenario')}")
    print(f"  total kills in battle={len(player.kills)}  damage events={len(player.damage)}")
    print(f"  focus POV = {focus['name']}  (team {focus['team']}, {focus['frags']} frags, "
          f"alive={focus['alive']})\n")

    timeline = to_timeline(player, vmap, focus_vid)
    if args.emit:
        Path(args.emit).write_text(json.dumps(timeline, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"  wrote {len(timeline)} events -> {args.emit}")
        return 0

    meta = BattleMeta(
        client_version=engine.get("clientVersionFromExe", "?"),
        map_name=engine.get("mapDisplayName", "?"),
        game_mode=engine.get("scenario", "?"),
        focus_player=focus["name"] or "?",
        focus_ship=engine.get("playerVehicle", "?"),  # ship code is for the recorder; label-only
        duration_s=int(engine.get("duration", 1200)),
        full_hp=float(focus["max_hp"] or 0),
    )
    battle = Battle.from_dicts(meta.__dict__, timeline)
    moments = score_battle(battle)
    if not moments:
        print("  No highlight candidates — this was a quiet game for the focus POV.")
        print("  (Correct behaviour: the scorer does not manufacture hype.)")
        return 0
    for i, m in enumerate(moments[: args.top], 1):
        card = generate_card(m, meta)
        print(f"  #{i}  score={m.score}  [{m.start_s:.0f}-{m.end_s:.0f}s]  {m.kind}")
        print(f"      hook : {card.hook}")
        print(f"      why  : {m.narrative}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
