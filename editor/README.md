# WoWS Shorts Editor

A local web app to turn captured WoWS gameplay into vertical (1080×1920) shorts by
clicking — trim/select segments, reframe to 9:16, balance audio stems + music, add
intro/outro, and export an MP4. Built on the `discovery/` toolkit.

## Status

- **Phase A (MVP) — DONE.** Pick a source → mark keep-segments → drag the 9:16 crop box
  → balance game/mic audio stems + a music bed → intro/outro cards → **Export** a
  1080×1920 MP4. The whole loop runs in the browser.
- **Phase B (counters) — DONE.** Attach a replay to a source, toggle **Frags**/**Damage**
  counters, set a **battle offset** to sync, and the export composites animated counter
  pills + kill callouts from the decoded replay (`backend/replay_data.py` + `counters.py`).
- **Editor polish — DONE.** **✨ Auto-suggest segments** (one click → the scorer proposes
  highlight keep-segments) and **kill chips** (click to jump the preview to each kill).
- **OBS-driven capture — DONE (offline-verified).** Connect OBS → **● Record** → **⚓ Battle
  start** (auto-sets the counter sync offset) → **★ Mark** → **■ Stop & load**, which drops the
  recording into the editor as a source with the offset pre-set. See *OBS setup* below.
- **Phase C (subtitles) — DONE.** Add timed caption cues (text + in/out seconds into the
  gameplay); the exporter burns them in (white, outlined, bottom-center) via ASS.
- **Phase D (transitions) — DONE.** Pick a transition (crossfade / fade-through-black /
  dissolve / wipe / slide) + duration; segments crossfade into each other on export.
- **Phase E (effects) — DONE.** Toggle **flash on kills** and **zoom punch on kills** (uses
  the attached replay's kill times) — a white flash + zoom hit land on each frag.
- **Phase F (audio ducking) — DONE.** Tick "Duck under gameplay" on the music bed and it
  auto-lowers under game/voice (sidechain compression).
- **GameParams names — DONE.** Real ship names everywhere: kill callouts show the victim's
  ship ("» AP citadel · Zao"), and the attach info shows the POV ship ("· Taihang").
- **Source scrubber — DONE.** A timeline under the preview shows gold **kill-marker ticks**
  (hover for ship/weapon, click to jump), your marked **segment bands**, and a live playhead;
  click anywhere to seek.
- **Per-segment speed — DONE.** Each timeline clip has a speed dropdown (0.25× slo-mo → 2×
  fast); applied to both video and audio on export.
- **Keyframed reframe (pan) — DONE.** Select a segment, drag the 9:16 box, and hit **◇ Reframe
  key** at different playhead points; the window pans across the clip between keyframes.
- **All planned phases (A–F) + capture + names + scrubber + speed + keyframed pan complete.**
  Nice-to-haves left: achievement names in narratives, smooth keyframed zoom.

## OBS setup (one-time, for capture)

1. In OBS: **Tools → WebSocket Server Settings** → enable, port **4455**, copy the password.
2. Make a scene that captures the game (Game/Display Capture) + desktop audio (and mic on a
   separate track if you want stems — Settings → Output → Recording → Audio Tracks).
3. In the editor's **Capture (OBS)** panel: paste the password → **Connect OBS**.
4. Launch the replay in WoWS yourself, hit **● Record**, press **⚓ Battle start** when the
   battle begins, **★ Mark** cool moments, then **■ Stop & load** to edit it.

## Run

```bash
# deps (one-time): fastapi uvicorn pydantic  (+ already-present imageio-ffmpeg, Pillow)
python -m uvicorn editor.backend.app:app --port 8765
# then open http://127.0.0.1:8765/  in a desktop browser
```

Drop gameplay recordings (`.mkv` / `.mp4` from OBS) into `editor/sources/`. The app
auto-makes a 720p preview proxy for each; export always uses the full-res original.

## Layout

```
editor/
  backend/
    app.py        FastAPI: /api/sources, /api/project, /api/export, media mounts
    models.py     pydantic project schema (sources, segments, reframe, audio, cards)
    exporter.py   staged ffmpeg export (reuses build_short.py patterns)
  frontend/       index.html + app.js + style.css  (vanilla, no build step)
  sources/        drop recordings here  (proxy/ holds preview proxies)
  projects/       saved project.json files
  output/         exported MP4s
  work/           per-export ffmpeg intermediates
```

## Workflow in the UI

1. Click a source (left) → it loads in the preview with a gold **9:16 crop box**.
2. Scrub, **Set IN** / **Set OUT**, **+ Add segment** (repeat for each keep-clip).
3. Drag the crop box to pan the 9:16 frame; click a timeline segment to edit its frame.
4. Balance **Audio** stems (game/mic) and optionally add a **Music bed** + gain.
5. Toggle **Intro/Outro** and type their lines.
6. **Export** → progress in the header → result opens with a download link.

## Notes / constraints (from the build)

- Browsers can't play `.mkv`/high-profile h264 in `<video>`, so the app previews a
  generated mp4 **proxy**; the exporter uses the original source.
- Audio "stems" = game-audio (music+SFX) vs mic. Music and SFX can't be split from the
  live WoWS client.
- The export pipeline is **staged** (intermediates under `work/`) so later phases insert
  counters/subtitles/transitions/effects between stages without a rewrite.
