# WoWS Discovery Layer (Phase 1 prototype)

The clientless, cheap half of the pipeline: turn a `.wowsreplay` into **ranked,
clip-ready highlight candidates** with a draft script — *without* launching the
game. This is the WoWS-specific IP. Full-fidelity capture of the chosen moments
happens downstream (and only for moments that scored well).

```
.wowsreplay ──► parse_header ──► metadata (version/map/mode/player/ship)
                                      │
            decoder (events) ─────────┤
                                      ▼
                                 score_battle ──► ranked HighlightMoments
                                      │
                                 generate_card ──► hook / title / caption / hashtags
```

## Files

| File | Role |
|------|------|
| `parse_header.py` | Zero-dependency reader of the **unencrypted** metadata block. Proven against a live v15.4 replay. Gives version (for ingestion gating), map, mode, player, ship, duration, roster. |
| `events.py` | The normalized event-timeline **schema** — the single contract a decoder must emit. Scorer never touches raw packets. |
| `scorer.py` | Deterministic highlight scorer: multi-kill clusters, achievements (Kraken…), detonations, citadels, + narrative bonuses (low-HP clutch, closing-seconds). All weights in `SCORING` for the Phase-3 feedback loop. |
| `card.py` | Highlight Card generator. Template by default (no deps); optional Claude path for punchier copy when `ANTHROPIC_API_KEY` + `anthropic` SDK are present. |
| `run_discovery.py` | CLI tying it together. `--demo` runs end-to-end with a synthetic timeline; `--events x.json` uses a real decoder's output. |
| `decode_replay.py` | **Real** packet-stream decoder. Wraps `replay_unpack`, instruments it to keep timestamps, maps every vehicle→player, auto-picks the best POV, emits the `events.py` schema. Proven on a live v15.4 replay. |
| `batch_scan.py` | Library triage: scan a folder of replays, rank by best highlight score. Answers "which battles do we even look at?" |
| `render_clip.py` | **Production**: renders a real 1080x1920 MP4 — top-down tactical animation (team-colored ship tracks, kill flashes, live frag+damage counters) with burned-in hook/title/branding — plus a `script.json`. Clientless proxy render; in-game capture swaps into the same overlay/encode step. |

## Produce a video (end-to-end, no game client, no API key)

```bash
python render_clip.py "<replay.wowsreplay>"                  # auto best POV -> clip.mp4 + clip.script.json
python render_clip.py "<replay>" --focus mythic37 --duration 24 --out clip.mp4
```

Output is a vertical MP4 + a `script.json` carrying hook/title/caption/hashtags
**and the exact kill timestamps** — the latter doubles as the capture instruction
set for swapping in real in-game footage later. Deps: `imageio-ffmpeg` (bundled
ffmpeg), `Pillow`. Note: the *recording* player's own ship has no position track
in the packet stream (the client doesn't log its own position), so the gold focus
marker only shows for non-recorder POVs — a non-issue once real footage is used.

## Run

```bash
python run_discovery.py "<path>/...La-Pampa....wowsreplay" --demo --top 4
python run_discovery.py "<replay>" --events timeline.json --out cards.json
ANTHROPIC_API_KEY=... python run_discovery.py "<replay>" --events timeline.json --claude
```

## The one integration contract

A decoder must emit a JSON list of events:

```json
[ {"t": 715.0, "type": "kill", "target": "enemyBB"},
  {"t": 713.0, "type": "damage", "value": 18500, "target": "enemyBB"},
  {"t": 1181.0, "type": "achievement", "label": "Kraken"} ]
```

`type` ∈ `damage | kill | citadel | torpedo_hit | detonation | achievement | hp | cap | death`
(see `events.EventType`). Any of these can produce it: `replays_unpack`,
WG-internal tooling, or server-side battle telemetry.

## Status — real decoding WORKS

Run on a folder of live replays:

```bash
python decode_replay.py "<replay.wowsreplay>"          # score one, auto POV
python decode_replay.py "<replay>" --focus mythic37     # a specific player's POV
python batch_scan.py    "<replays folder>"              # rank a whole library
```

Validated end-to-end on ~30 current replays: decodes frags (with weapon type),
per-shot damage, achievements, all timestamped; ranks a 5-frag Kraken to the top
and centres the clip on the densest kill burst.

### Dependencies (installed)
`replay_unpack` (from GitHub) + `lxml`, `packaging`, `pycryptodomex`. Core
modules (`parse_header`/`events`/`scorer`/`card`) remain stdlib-only; only the
real decoder needs these.

### Current-version decode (solved)
`replay_unpack` ships schemas per version and lags the live patch, but WoWS minor
patches keep the packet protocol stable. `decode_replay._ensure_version_support()`
clones the newest shipped schema to the current version when missing, and `decode()`
tolerates new-content ids that only break the optional battle-report. Result: the
**current patch's replays decode** (verified on a 15.5 replay via the 15.4 schema),
and PvE "operation" replays decode too (same fallback). If a future patch changes the
protocol itself, decode errors loudly → add a real schema then.

### Remaining: `GameParams` gap (names only)
Frags/damage/achievement **counts** decode fine (counters need nothing else). Naming
*other* players' ships or achievements by id still needs the client's packed
`GameParams.data`; the recording player's ship name comes free from the replay header.
