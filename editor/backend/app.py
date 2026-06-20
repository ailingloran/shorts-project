"""
FastAPI backend for the WoWS shorts editor.

Serves the browser editor and exposes:
  GET  /api/sources              list recordings (+ browser preview proxy + metadata)
  GET  /api/project/{id}         load a project.json
  POST /api/project              save a project.json
  POST /api/export/{id}          start an export job (background thread)
  GET  /api/export/{id}          export job status
Media (range-capable) is served via mounted static dirs: /proxy, /output.

Run:  python -m uvicorn editor.backend.app:app --port 8765
"""
from __future__ import annotations

import subprocess
import threading
import traceback
from pathlib import Path

import imageio_ffmpeg
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .exporter import export, probe
from .models import Project

FF = imageio_ffmpeg.get_ffmpeg_exe()
ROOT = Path(__file__).resolve().parents[1]          # editor/
SOURCES = ROOT / "sources"
PROXY = SOURCES / "proxy"
PROJECTS = ROOT / "projects"
OUTPUT = ROOT / "output"
WORK = ROOT / "work"
FRONTEND = ROOT / "frontend"
for d in (SOURCES, PROXY, PROJECTS, OUTPUT, WORK):
    d.mkdir(parents=True, exist_ok=True)

WOWS_REPLAYS = Path(r"C:\Program Files (x86)\Steam\steamapps\common\World of Warships\replays")

app = FastAPI(title="WoWS Shorts Editor")
_jobs: dict[str, dict] = {}
_decode_cache: dict[tuple, dict] = {}


def _make_proxy(src: Path) -> Path:
    """720p faststart mp4 for in-browser preview (mkv/high-profile won't play in <video>)."""
    out = PROXY / f"{src.stem}.mp4"
    if out.exists() and out.stat().st_mtime >= src.stat().st_mtime:
        return out
    subprocess.run([FF, "-y", "-hide_banner", "-i", str(src),
                    "-vf", "scale=-2:720", "-map", "0:v:0", "-map", "0:a:0?",
                    "-c:v", "libx264", "-crf", "28", "-preset", "veryfast",
                    "-movflags", "+faststart", "-c:a", "aac", str(out)],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return out


def _source_item(path: Path, extra: dict | None = None) -> dict:
    """Probe a recording + ensure a preview proxy, returning a UI source dict."""
    proxy = _make_proxy(path)
    item = {"name": path.name, "path": str(path).replace("\\", "/"),
            "proxy_url": f"/proxy/{proxy.name}", **probe(str(path))}
    if extra:
        item.update(extra)
    return item


@app.get("/api/sources")
def list_sources():
    items = [_source_item(src) for src in sorted(SOURCES.glob("*.*"))
             if src.suffix.lower() in (".mkv", ".mp4", ".mov")]
    return {"sources": items}


@app.post("/api/obs/connect")
def obs_connect(host: str = "localhost", port: int = 4455, password: str = ""):
    from .obs_capture import MANAGER
    return MANAGER.connect(host, port, password)


@app.get("/api/obs/status")
def obs_status():
    from .obs_capture import MANAGER
    return MANAGER.status()


@app.post("/api/obs/start")
def obs_start():
    from .obs_capture import MANAGER
    return MANAGER.start()


@app.post("/api/obs/battlestart")
def obs_battlestart():
    from .obs_capture import MANAGER
    return MANAGER.mark_battle_start()


@app.post("/api/obs/mark")
def obs_mark():
    from .obs_capture import MANAGER
    return MANAGER.mark()


@app.post("/api/obs/stop")
def obs_stop():
    from .obs_capture import MANAGER
    r = MANAGER.stop()
    if r.get("ok") and r.get("path"):
        p = Path(r["path"])
        if p.exists():
            r["source"] = _source_item(p, {"battle_offset_s": r.get("battle_offset", 0.0)})
    return r


@app.get("/api/replays")
def list_replays():
    if not WOWS_REPLAYS.exists():
        return {"replays": [], "dir": str(WOWS_REPLAYS)}
    items = [{"name": f.name, "path": str(f).replace("\\", "/")}
             for f in sorted(WOWS_REPLAYS.glob("*.wowsreplay"),
                             key=lambda p: p.stat().st_mtime, reverse=True)]
    return {"replays": items, "dir": str(WOWS_REPLAYS)}


@app.get("/api/decode")
def decode_replay_summary(replay: str, focus: str | None = None):
    """Decode a replay and return a counter/marker summary for the UI."""
    key = (replay, focus)
    if key not in _decode_cache:
        from .replay_data import decode_for_editor
        _decode_cache[key] = decode_for_editor(replay, focus)
    d = _decode_cache[key]
    kt = [k["t"] for k in d["kills"]]
    return {
        "focus_player": d["focus_player"], "focus_ship": d.get("focus_ship"),
        "frags_total": d["frags_total"],
        "total_damage": d["total_damage"], "duration_s": d["duration_s"],
        "map": d["map"], "mode": d["mode"], "focus_options": d["focus_options"],
        "kill_count": len(kt), "first_kill_t": min(kt) if kt else None,
        "last_kill_t": max(kt) if kt else None,
        "kills": d["kills"],   # for timeline markers
    }


@app.get("/api/suggest")
def suggest_segments(replay: str, focus: str | None = None, offset: float = 0.0,
                     max_src: float | None = None, top: int = 4):
    """Scored highlight moments mapped to source-time keep-segments."""
    key = (replay, focus)
    if key not in _decode_cache:
        from .replay_data import decode_for_editor
        _decode_cache[key] = decode_for_editor(replay, focus)
    segs = []
    for m in _decode_cache[key]["moments"][:top]:
        in_s = round(max(0.0, m["start"] - offset), 2)
        out_s = round(m["end"] - offset, 2)
        if max_src is not None:
            if in_s >= max_src:
                continue
            out_s = min(out_s, max_src)
        if out_s <= in_s:
            continue
        segs.append({"in_s": in_s, "out_s": out_s, "score": m["score"],
                     "kind": m["kind"], "narrative": m["narrative"]})
    return {"segments": segs}


@app.get("/api/project/{pid}")
def load_project(pid: str):
    f = PROJECTS / f"{pid}.json"
    if not f.exists():
        raise HTTPException(404, "project not found")
    return Project.model_validate_json(f.read_text(encoding="utf-8")).model_dump()


@app.post("/api/project")
def save_project(project: Project):
    (PROJECTS / f"{project.project_id}.json").write_text(
        project.model_dump_json(indent=2), encoding="utf-8")
    return {"ok": True, "project_id": project.project_id}


def _run_export(project: Project):
    jid = project.project_id
    _jobs[jid] = {"state": "running", "out": None, "error": None}
    try:
        out = OUTPUT / f"{jid}.mp4"
        export(project, WORK / jid, out)
        _jobs[jid] = {"state": "done", "out": f"/output/{out.name}", "error": None}
    except Exception as e:
        _jobs[jid] = {"state": "error", "out": None, "error": f"{e}\n{traceback.format_exc()[-800:]}"}


@app.post("/api/export/{pid}")
def start_export(pid: str, project: Project):
    save_project(project)
    threading.Thread(target=_run_export, args=(project,), daemon=True).start()
    return {"ok": True, "job": pid}


@app.get("/api/export/{pid}")
def export_status(pid: str):
    return _jobs.get(pid, {"state": "idle"})


# --- media + frontend (mounted last so /api takes precedence) ---
app.mount("/proxy", StaticFiles(directory=str(PROXY)), name="proxy")
app.mount("/output", StaticFiles(directory=str(OUTPUT)), name="output")


@app.get("/")
def index():
    return RedirectResponse("/app/index.html")


app.mount("/app", StaticFiles(directory=str(FRONTEND), html=True), name="app")
