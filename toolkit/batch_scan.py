"""
Scan a folder of .wowsreplay files and rank them by best highlight score.

This is the "which battles do we even look at?" triage step: point it at a
dump of submitted/collected replays and it surfaces the few worth producing,
newest-version-first, with the best POV and moment for each.

    python batch_scan.py "<replays folder>"
"""
from __future__ import annotations

import sys
from pathlib import Path

from decode_replay import decode, build_vehicle_map, pick_focus, to_timeline
from events import Battle, BattleMeta
from scorer import score_battle


def scan_one(path: Path) -> dict:
    engine, gi, player = decode(str(path))
    vmap = build_vehicle_map(gi)
    focus_vid = pick_focus(vmap, None)
    focus = vmap[focus_vid]
    timeline = to_timeline(player, vmap, focus_vid)
    meta = BattleMeta(
        client_version=engine.get("clientVersionFromExe", "?"),
        map_name=engine.get("mapDisplayName", "?"),
        game_mode=engine.get("scenario", "?"),
        focus_player=focus["name"] or "?",
        focus_ship=engine.get("playerVehicle", "?"),
        duration_s=int(engine.get("duration", 1200)),
        full_hp=float(focus["max_hp"] or 0),
    )
    moments = score_battle(Battle.from_dicts(meta.__dict__, timeline))
    best = moments[0] if moments else None
    return {
        "file": path.name,
        "version": engine.get("clientVersionFromExe"),
        "pov": focus["name"],
        "frags": focus["frags"],
        "total_kills": len(player.kills),
        "best_score": best.score if best else 0.0,
        "best_kind": best.kind if best else "-",
        "best_when": f"{best.start_s:.0f}-{best.end_s:.0f}s" if best else "-",
    }


def main(argv: list[str]) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    folder = Path(argv[0]) if argv else Path(".")
    rows = []
    for p in sorted(folder.glob("*.wowsreplay")):
        try:
            rows.append(scan_one(p))
        except Exception as e:
            rows.append({"file": p.name, "version": "?", "pov": "ERROR",
                         "frags": 0, "total_kills": 0, "best_score": -1,
                         "best_kind": type(e).__name__, "best_when": str(e)[:40]})
    rows.sort(key=lambda r: r["best_score"], reverse=True)

    print(f"{'score':>6}  {'kind':<12}{'POV':<18}{'frg':>3}{'tk':>3}  {'when':<11}{'ver':<10}file")
    for r in rows:
        print(f"{r['best_score']:>6}  {r['best_kind']:<12}{str(r['pov'])[:17]:<18}"
              f"{r['frags']:>3}{r['total_kills']:>3}  {r['best_when']:<11}"
              f"{str(r['version'])[:9]:<10}{r['file']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
