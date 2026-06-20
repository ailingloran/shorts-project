"""
Discovery runner: .wowsreplay  ->  ranked Highlight Cards.

Pipeline:
    1. parse_header  -> real battle metadata (version, map, mode, player, ship)
    2. event timeline -> from a decoder's JSON (--events) OR a labeled --demo
    3. score_battle  -> ranked clip-ready candidate windows
    4. generate_card -> hook / title / caption / hashtags per top candidate

Usage:
    python run_discovery.py <replay.wowsreplay> --demo
    python run_discovery.py <replay.wowsreplay> --events timeline.json
    python run_discovery.py <replay.wowsreplay> --events timeline.json --top 3 --out cards.json

The --events JSON is a list of {t,type,value,target,label} objects matching
events.Event. That is exactly what a decoder (replays_unpack / WG-internal /
server telemetry) needs to emit — the only integration contract.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from parse_header import parse_metadata, summarize
from events import Battle, BattleMeta
from scorer import score_battle
from card import generate_card


def _meta_from_header(replay_path: str) -> BattleMeta:
    s = summarize(parse_metadata(replay_path))
    return BattleMeta(
        client_version=s["client_version"] or "unknown",
        map_name=s["map_display_name"] or "unknown",
        game_mode=s["game_mode"] or "unknown",
        focus_player=s["player_name"] or "unknown",
        focus_ship=s["player_ship"] or "unknown",
        duration_s=int(s["duration_s"] or 1200),
        full_hp=21500.0,  # decoder fills the real value; DD-ish default for demo
    )


def _demo_timeline(meta: BattleMeta) -> list[dict]:
    """A representative La-Pampa-style timeline. CLEARLY synthetic — used only to
    exercise the scorer end-to-end until a real decoder is wired in."""
    return [
        {"t": 240, "type": "torpedo_hit", "value": 0, "target": "navypg81"},
        {"t": 242, "type": "damage", "value": 12000, "target": "navypg81"},
        {"t": 246, "type": "kill", "value": 0, "target": "navypg81"},
        {"t": 690, "type": "hp", "value": 2600},                       # critically low
        {"t": 712, "type": "citadel", "value": 0, "target": "enemyBB"},
        {"t": 713, "type": "damage", "value": 18500, "target": "enemyBB"},
        {"t": 715, "type": "kill", "value": 0, "target": "enemyBB"},
        {"t": 721, "type": "torpedo_hit", "value": 0, "target": "enemyCA"},
        {"t": 724, "type": "kill", "value": 0, "target": "enemyCA"},
        {"t": 730, "type": "kill", "value": 0, "target": "enemyDD"},
        {"t": 733, "type": "detonation", "value": 0, "target": "enemyDD2"},
        {"t": 733, "type": "kill", "value": 0, "target": "enemyDD2"},
        {"t": 1180, "type": "kill", "value": 0, "target": "lastEnemy"},
        {"t": 1181, "type": "achievement", "value": 0, "label": "Kraken"},
    ]


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="WoWS replay -> ranked highlight cards")
    ap.add_argument("replay", help="path to .wowsreplay")
    ap.add_argument("--events", help="decoder-produced event timeline JSON")
    ap.add_argument("--demo", action="store_true", help="use a synthetic demo timeline")
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--out", help="write full cards JSON here")
    ap.add_argument("--claude", action="store_true", help="force Claude copy (needs API key + SDK)")
    args = ap.parse_args(argv)

    # Windows consoles default to cp1252; cards use em-dashes etc.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    meta = _meta_from_header(args.replay)
    print(f"# {Path(args.replay).name}")
    print(f"  version={meta.client_version}  map={meta.map_name}  mode={meta.game_mode}")
    print(f"  player={meta.focus_player}  ship={meta.focus_ship}  duration={meta.duration_s}s\n")

    if args.events:
        events = json.loads(Path(args.events).read_text(encoding="utf-8"))
    elif args.demo:
        events = _demo_timeline(meta)
        print("  [!] using SYNTHETIC demo timeline (no real packet decode)\n")
    else:
        print("  no --events and no --demo: nothing to score.\n"
              "  provide a decoder timeline with --events, or try --demo.")
        return 1

    battle = Battle.from_dicts(meta.__dict__, events)
    moments = score_battle(battle)
    if not moments:
        print("  no highlight candidates found.")
        return 0

    use_claude = True if args.claude else None
    cards = []
    for i, m in enumerate(moments[: args.top], 1):
        card = generate_card(m, meta, use_claude=use_claude)
        cards.append(card)
        print(f"  #{i}  score={m.score:>5}  [{m.start_s:.0f}s-{m.end_s:.0f}s]  ({card.source})")
        print(f"      kind   : {m.kind}")
        print(f"      hook   : {card.hook}")
        print(f"      title  : {card.title}")
        print(f"      caption: {card.caption}")
        print(f"      why    : {m.narrative}\n")

    if args.out:
        from dataclasses import asdict
        Path(args.out).write_text(
            json.dumps([asdict(c) for c in cards], indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"  wrote {len(cards)} cards -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
