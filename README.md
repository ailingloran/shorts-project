# WoWS Shorts

Tools to turn **World of Warships replays** into **vertical short-form videos**
(TikTok / YouTube Shorts / Reels) for the official WoWS channels.

Two parts:

| Folder | What it is |
|--------|-----------|
| **[`editor/`](editor/README.md)** | The product: a local web-app **editor**. Record a replay (via OBS), then click through a timeline to cut a vertical short — trim/select, 9:16 reframe, audio stems + music, (soon) counters/subtitles/transitions/effects — and export an MP4. |
| **[`toolkit/`](toolkit/README.md)** | The reusable **WoWS library** the editor builds on: decode `.wowsreplay` files into a timed event feed (frags/damage), score highlights, generate copy, and render PIL→ffmpeg overlays. |
| `samples/` | Example outputs (e.g. `wisconsin_short.mp4`). |

## Quick start (editor)

```bash
python -m pip install -r requirements.txt
python -m uvicorn editor.backend.app:app --port 8765
# open http://127.0.0.1:8765/ in a desktop browser
```

Drop OBS recordings into `editor/sources/`, then edit and export.

See **[CLAUDE.md](CLAUDE.md)** for the full picture: the domain constraints that
shape everything, the architecture, current status, and the roadmap.
