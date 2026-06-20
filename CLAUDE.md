# CLAUDE.md — WoWS Shorts

Guide for working in this repo. Read this first; it encodes domain facts that are
expensive to rediscover.

## What this is

A toolset to turn **World of Warships replays** into **vertical short-form videos**
for the official WoWS social channels. The current focus (after a pivot) is an
**editor app**: the user records a replay through OBS, then assembles a vertical
short by clicking — trim/select, 9:16 reframe, audio + music, and (rolling out)
animated counters, subtitles, transitions, and effects.

The full design + roadmap lives in the plan file:
`C:\Users\kuba_\.claude\plans\as-world-of-warships-sleepy-eagle.md`.

## Critical domain facts (do not relearn these the hard way)

1. **A `.wowsreplay` is NOT a video.** It's a JSON header + a serialized packet
   stream the game engine re-simulates. To get footage you must **play it back in
   the matching client and screen-record**. There is no offline renderer for the
   real 3D look.
2. **Replays are version-locked.** A 15.4 replay only plays in a 15.4 client. The
   live client is **15.5**. Old builds sit in the game's `bin/<id>/`.
3. **You cannot launch a replay programmatically** — Steam DRM blocks running the
   exe directly, and file-association launch didn't work. **The user launches it**;
   our tools record and edit.
4. **Capture = OBS.** The user runs **OBS** (and **VoiceMeeter**). OBS captures game
   video + audio cleanly and supports multi-track audio. `gdigrab` by **window
   title returns BLACK** for DirectX games; only **desktop-region** capture works —
   so prefer OBS, and if ever using ffmpeg capture, grab the desktop region at the
   window's rect (game runs borderless 2560×1440 at (0,0) on the primary monitor).
5. **Audio stems = game-audio (music+SFX) vs mic.** WoWS mixes its own music and SFX
   into one app stream; **music and SFX cannot be split** from the live client
   (isolating music would need Wwise extraction from game files — not done).
6. **Current-version decode works (auto schema-reuse).** WoWS minor patches keep the
   BigWorld *packet protocol* stable; only content id-tables change. `replay_unpack`
   ships hand-made schemas per version and lags the live patch, so
   `toolkit/decode_replay.py:_ensure_version_support()` **clones the newest shipped
   schema to the current version if missing** (verified: a 15.5 replay decodes via the
   15.4 schema). New-content ids (a just-released ship) only break an *optional*
   battle-report, which `decode()` catches with a fallback. **Assume current-version
   replays only** — never juggle old builds. If a future patch ever changes the
   protocol itself, decode will error loudly → time to add a real schema.
7. **Names resolve from replay_unpack's bundled GameParams (`game_params.BY_ID`, ~4.7k
   entries).** `editor/backend/names.py` maps a ship/achievement param id → display name
   by stripping the index prefix (e.g. `PFSC109_Saint_Louis` → "Saint Louis",
   `PCH016_FirstBlood` → "First Blood"). Players carry `shipParamsId`; `build_vehicle_map`
   exposes it. So every player's ship + achievements are nameable with NO client extraction.
   (These are the internal codes, not the fully-localized .mo strings, but read cleanly.)
8. **The recording player's own ship has no position track** in the packet stream —
   irrelevant to the editor (we composite over real footage), but don't rely on it.

## Environment

- **OS:** Windows 11. **Python 3.14** (`python` / `python -m pip`).
- **ffmpeg:** no system ffmpeg — use the **bundled binary** via
  `imageio_ffmpeg.get_ffmpeg_exe()` (ffmpeg 7.1; has `gdigrab`, `xfade`, `zoompan`,
  `sidechaincompress`, `qtrle`, etc.). It supports `gdigrab` screen capture.
- The user is a WoWS Community Contributor; replays live at
  `C:\Program Files (x86)\Steam\steamapps\common\World of Warships\replays\`.
- You can drive/inspect the editor UI with the **Claude_Preview** MCP (a
  `.claude/launch.json` config named `editor` is set up). You can `gdigrab`-screenshot
  the desktop to see the game while driving capture.

## Structure

```
shorts project/
  CLAUDE.md            this file
  README.md            human overview
  requirements.txt     consolidated deps (pip install -r)
  .claude/launch.json  Claude_Preview config ("editor" server on :8765)
  toolkit/             reusable WoWS library (was "discovery/")
    parse_header.py    zero-dep .wowsreplay metadata + version gate (works on ANY version)
    decode_replay.py   decode timed frags/damage/achievements/positions (replay_unpack, ≤15.4)
    events.py          normalized event schema (the decoder<->scorer contract)
    scorer.py          highlight scoring (multikill/Kraken/carry/detonation + narrative)
    card.py            hook/title/caption copy (template + optional Claude)
    render_clip.py     PIL->ffmpeg raw-pixel pipe; tactical render + animated counters
    build_short.py     ffmpeg crop 16:9->9:16 + PIL cards + concat (export seed)
    batch_scan.py      rank a folder of replays by best highlight
    run_discovery.py   CLI: replay -> ranked highlight cards
  editor/              the app
    backend/app.py     FastAPI: /api/sources(+proxy), /api/project, /api/export, media
    backend/models.py  pydantic Project schema (sources, segments, reframe, audio, cards)
    backend/exporter.py STAGED ffmpeg export (trim->reframe->audio->cards->mux)
    frontend/          vanilla index.html + app.js + style.css (no build step)
    sources/  projects/  output/  work/   (runtime data; gitignored)
  samples/             example outputs (wisconsin_short.mp4)
```

## Running things

```bash
# Editor (the app)
python -m uvicorn editor.backend.app:app --port 8765   # -> http://127.0.0.1:8765/

# Toolkit CLIs (script's own dir goes on sys.path, so bare imports work)
python toolkit/parse_header.py  "<replay.wowsreplay>"
python toolkit/decode_replay.py "<replay.wowsreplay>"          # ≤15.4 only
python toolkit/batch_scan.py    "<replays folder>"
python toolkit/build_short.py                                  # data-render demo
```

## Status & roadmap

- **DONE — Phase A (editor MVP):** pick source → mark segments → 9:16 crop box →
  balance game/mic stems + music bed → intro/outro → export a 1080×1920 MP4.
- **DONE — Phase B (animated counters), verified end-to-end:** attach a replay to a
  source, decode it (`replay_data.py`), toggle frag/damage counters, sync with a
  battle-offset, and the export composites animated FRAGS/DAMAGE pills + kill callouts
  (`counters.py` → RGBA qtrle overlay → ffmpeg `overlay`). Driven by current-version
  decode, so it works on live-patch replays.
- **DONE — toolkit:** current-version decode, scoring, copy, data-render, proven
  real-footage capture recipe (OBS region capture; see plan + memory).
- **DONE — editor "magic" polish:** **auto-suggest segments** (one click runs the
  toolkit scorer → proposes highlight keep-segments mapped to source time, `/api/suggest`)
  and **kill chips** (click to seek the preview to each kill). Verified in the UI.
- **DONE — OBS capture (offline-verified):** `backend/obs_capture.py` (`CaptureManager`)
  drives OBS over its WebSocket (`obsws-python`): connect → Record → **⚓ Battle start**
  (sets `battle_offset = -(battle_start − record_start)`) → ★ Mark → Stop, which registers
  the recording as an editor source with the offset pre-set. Best-effort F9/F8 global
  hotkeys (`keyboard`). Graceful when OBS is off. Endpoints `/api/obs/*`; Capture panel in
  the UI. **Live record flow needs OBS open with WebSocket enabled** (see editor/README) —
  the user launches the replay; offset math + offline paths verified, live capture is the
  user's to confirm.
- **DONE — Phase C (subtitles), verified:** add timed caption cues in the UI; the
  exporter burns them via a generated ASS file (`_write_ass`/`_burn_subs`, run with
  cwd=work so the filter path is relative — dodges Windows path escaping). proj-time,
  over the assembled gameplay body.
- **DONE — Phase D (transitions), verified:** crossfade/dissolve/wipe/slide between
  segments via xfade (video) + acrossfade (audio) in `_concat_with_transitions`
  (falls back to hard-cut concat when type=none). UI Transitions panel.
- **DONE — Phase E (effects on kills), verified:** flash (time-gated `drawbox`) and
  zoom-punch (`zoompan`) at each decoded kill time, applied per-segment before the
  counter composite (`_apply_effects`, kills mapped to segment-local time). UI Effects panel.
- **DONE — Phase F (audio ducking), verified:** music auto-ducks under game/voice via
  `sidechaincompress` (body audio is the sidechain key) in `_add_music` when `music.duck`;
  UI "Duck under gameplay" checkbox.
- **DONE — GameParams names:** `names.py` resolves ship + achievement names from the
  bundled BY_ID table; counters show the victim's SHIP in kill callouts ("» AP citadel ·
  Zao"), and the attach info shows the focus ship ("POV bloodminister · Taihang · 5 frags").
- **DONE — source scrubber:** timeline bar under the preview (`#scrubber`) with gold
  kill-marker ticks (hover = "Zao · ap_shell @ 5s"; click to seek), translucent segment
  bands, pending IN/OUT band, and a live playhead. Click to seek; rebuilt on
  select/attach/offset/segment changes (frontend-only, in `app.js`).
- **DONE — per-segment speed:** each timeline clip has a speed selector (0.25×–2×);
  exporter applies `setpts` (video) + a chained `atempo` (audio, decomposed into 0.5–2.0
  factors). NOTE: `-ss/-t` must be INPUT options (before `-i`) or the output `-t` truncates
  the stretched clip — this was silently breaking all non-1× speeds until fixed.
- **DONE — keyframed reframe (pan):** select a segment, position the crop box at the
  playhead, "◇ Reframe key" captures a keyframe; 2+ keyframes pan the 9:16 window across
  the clip via `crop` x/y time-exprs (`_kf_expr` piecewise-linear) → scale. ffmpeg `crop`
  has NO `eval` option, so w/h can't vary per-frame → pan only, constant window size
  (smooth zoom-over-time not supported this way; use per-segment zoom or zoom-punch).
- **ALL PLANNED PHASES (A–F) + capture + names + scrubber + speed + keyframed pan COMPLETE.**
  `frontend/index.html` loads `app.js?v=2` (bump the query to bust browser JS cache after
  edits). Remaining nice-to-haves: achievement names in auto-suggest narratives, smooth
  keyframed zoom (needs a zoompan-based path). Live OBS record flow is the user's to confirm.

## Conventions & gotchas

- **Browser can't preview `.mkv`/high-profile h264** in `<video>`. The backend
  auto-generates a **720p mp4 proxy** per source for preview; **export always uses
  the full-res original**. (`editor/backend/app.py:_make_proxy`)
- **The exporter is intentionally STAGED** (intermediate files in `editor/work/`)
  so Phases B–F slot between stages without a rewrite. Don't collapse it into one
  mega-filtergraph.
- **Three clocks** in the editor model: `src_t` (into a recording), `proj_t` (final
  timeline), `battle_t` (decoder events). `battle_t = src_t + battle_offset_s`.
- **Editor → toolkit imports (Phase B):** the editor will add the `toolkit/` dir to
  `sys.path` and import `decode_replay`/`events`/`scorer` (they use bare imports).
- **Always use the bundled ffmpeg** (`imageio_ffmpeg.get_ffmpeg_exe()`), never a
  bare `ffmpeg` (none on PATH).
- **Don't commit large media** (recordings, exports, replays) — see `.gitignore`.
  Raw captures are large (~minutes of 1440p); delete `editor/work/` and old
  recordings when done.
- **No git repo yet** — don't run git operations unless the user asks.

## Memory

Durable project facts (decisions, the journey, what's proven) are in the auto-memory
at `…\projects\C--Users-kuba--shorts-project\memory\`. Update it when status changes.
