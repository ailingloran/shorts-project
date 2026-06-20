"""
Bridge from a .wowsreplay to the data the editor's counters/markers need.

Reuses the toolkit decoder (current-version-safe). Returns the focus player's
timed kills (t, victim, weapon) and a cumulative-damage curve, in battle-relative
seconds. The editor maps these onto footage via a per-source battle offset.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

_TOOLKIT = str(Path(__file__).resolve().parents[2] / "toolkit")
if _TOOLKIT not in sys.path:
    sys.path.insert(0, _TOOLKIT)

import decode_replay as dr          # toolkit (bare-imports resolve via _TOOLKIT on path)
from card import map_name
from events import Battle, BattleMeta
from scorer import score_battle

try:
    from . import names               # imported as editor.backend.replay_data
except ImportError:
    import names                      # run standalone (script dir on path)


def _death_types(version_parts: list[str]) -> dict:
    try:
        v = "_".join(version_parts[:3])
        return importlib.import_module(
            f"replay_unpack.clients.wows.versions.{v}.constants").DEATH_TYPES
    except Exception:
        return {}


def decode_for_editor(replay_path: str, focus: str | None = None) -> dict:
    """Decode a replay into counter/marker data for the editor."""
    eng, gi, p = dr.decode(replay_path)
    vmap = dr.build_vehicle_map(gi)
    fv = dr.pick_focus(vmap, focus)
    fo = vmap[fv]
    ver = eng["clientVersionFromExe"].replace(" ", "").split(",")
    DT = _death_types(ver)

    kills = []
    for t, killed, fr, td in p.kills:
        if fr == fv:
            v = vmap.get(killed, {})
            kills.append({"t": round(t, 2),
                          "victim": v.get("name", str(killed)),
                          "victim_ship": names.ship_name(ver, v.get("ship_param_id")),
                          "weapon": DT.get(td, {}).get("name", "")})
    run, curve = 0.0, []
    for t, att, vic, amt in sorted((d for d in p.damage if d[1] == fv)):
        run += amt
        curve.append({"t": round(t, 2), "cum": int(run)})

    options = sorted(({"name": v["name"], "frags": v["frags"]}
                      for v in vmap.values() if v.get("name")),
                     key=lambda x: -x["frags"])[:8]

    # scored highlight moments (battle-time) via the toolkit scorer
    timeline = dr.to_timeline(p, vmap, fv)
    meta = BattleMeta(
        client_version=eng.get("clientVersionFromExe", "?"),
        map_name=eng.get("mapDisplayName", "?"),
        game_mode=eng.get("scenario", "?"),
        focus_player=fo.get("name") or "?",
        focus_ship=eng.get("playerVehicle", "?"),
        duration_s=int(eng.get("duration", 1200)),
        full_hp=float(fo.get("max_hp") or 0),
    )
    moments = [{"start": round(m.start_s, 2), "end": round(m.end_s, 2),
                "score": m.score, "kind": m.kind, "narrative": m.narrative}
               for m in score_battle(Battle.from_dicts(meta.__dict__, timeline))]
    return {
        "version": eng.get("clientVersionFromExe"),
        "map": map_name(eng.get("mapDisplayName", "")),
        "mode": eng.get("scenario") or eng.get("gameMode"),
        "duration_s": int(eng.get("duration", 1200)),
        "focus_player": fo["name"],
        "focus_ship": names.ship_name(ver, fo.get("ship_param_id")),
        "focus_max_hp": fo.get("max_hp", 0),
        "frags_total": fo["frags"],
        "total_damage": int(run),
        "kills": kills,
        "damage_curve": curve,
        "focus_options": options,
        "moments": moments,
    }


if __name__ == "__main__":
    import json
    print(json.dumps(decode_for_editor(sys.argv[1],
                                       sys.argv[2] if len(sys.argv) > 2 else None),
                     indent=2, ensure_ascii=False)[:1500])
