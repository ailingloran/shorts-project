"""
Produce a real vertical MP4 short + script.json from a scored replay moment.

This is the PRODUCTION half running clientless: it renders a top-down "tactical"
animation of the highlight (ship tracks colored by team, kill flashes, a live
frag + damage counter) into a 1080x1920 MP4 with burned-in hook/title/branding,
and writes the matching script.json.

It is NOT the final in-game-capture look (that needs the live client + a capture
farm). It IS a real, on-brand vertical clip produced end-to-end from data, and it
proves the whole assembly stage — the in-game footage later swaps into the same
overlay/encode step.

    python render_clip.py "<replay.wowsreplay>" [--focus NAME] [--duration 26] [--out clip.mp4]
"""
from __future__ import annotations

import argparse
import bisect
import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFont

from decode_replay import decode, build_vehicle_map, pick_focus, to_timeline, DEATH_DETONATE
from events import Battle, BattleMeta
from scorer import score_battle
from card import generate_card, ship_name, map_name

# ---- frame + layout constants ------------------------------------------------
W, H, FPS = 1080, 1920, 30
BG = (11, 15, 22)
GRID = (28, 38, 52)
GOLD = (255, 205, 70)
CYAN = (95, 200, 255)
RED = (255, 84, 84)
WHITE = (240, 244, 250)
DIM = (120, 130, 145)
MAP_TOP, MAP_SIZE = 470, 1000        # tactical square: y in [470,1470], x centered
MAP_LEFT = (W - MAP_SIZE) // 2

WEAPON_VERB = {
    "AP_SHELL": "AP citadel", "CS_SHELL": "AP citadel", "HE_SHELL": "HE", "TORPEDO": "torpedo",
    "FLOOD": "flooding", "BURNING": "fire", "ADBOMB": "depth charge", "BOMB": "bomb",
}


def font(path_options, size):
    for p in path_options:
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default()


F_HOOK = font([r"C:\Windows\Fonts\ariblk.ttf", r"C:\Windows\Fonts\impact.ttf"], 64)
F_TITLE = font([r"C:\Windows\Fonts\arialbd.ttf"], 40)
F_BIG = font([r"C:\Windows\Fonts\ariblk.ttf", r"C:\Windows\Fonts\arialbd.ttf"], 86)
F_LABEL = font([r"C:\Windows\Fonts\arialbd.ttf"], 30)
F_SMALL = font([r"C:\Windows\Fonts\arial.ttf"], 26)


def build_tracks(positions, eids):
    tracks = {e: ([], [], []) for e in eids}
    for t, eid, x, z in positions:
        if eid in tracks:
            ts, xs, zs = tracks[eid]
            ts.append(t); xs.append(x); zs.append(z)
    return tracks


def pos_at(track, t):
    ts, xs, zs = track
    if not ts or t < ts[0]:
        return None
    i = bisect.bisect_right(ts, t) - 1
    return xs[i], zs[i]


def w2p(x, z, board):
    half = MAP_SIZE / 2
    px = MAP_LEFT + half + (x / board) * half
    py = MAP_TOP + half - (z / board) * half   # invert z so north is up
    return px, py


def centered(draw, cx, y, text, fnt, fill):
    w = draw.textlength(text, font=fnt)
    draw.text((cx - w / 2, y), text, font=fnt, fill=fill)


def wrap(draw, text, fnt, max_w):
    words, lines, cur = text.split(), [], ""
    for wd in words:
        trial = (cur + " " + wd).strip()
        if draw.textlength(trial, font=fnt) <= max_w:
            cur = trial
        else:
            lines.append(cur); cur = wd
    if cur:
        lines.append(cur)
    return lines


def render(replay, focus_name, duration, out_path):
    engine, gi, player = decode(replay, capture_positions=True)
    vmap = build_vehicle_map(gi)
    focus_vid = pick_focus(vmap, focus_name)
    focus = vmap[focus_vid]
    focus_team = focus["team"]

    # scored moment + card (the script)
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
    if not moments:
        print("No highlight to render for this POV.")
        return None
    moment = moments[0]
    card = generate_card(moment, meta)

    # focus kills (timed, with weapon + victim) and cumulative damage
    from importlib import import_module
    DT = {}
    try:
        v = "_".join(engine["clientVersionFromExe"].replace(" ", "").split(",")[:3])
        DT = import_module(f"replay_unpack.clients.wows.versions.{v}.constants").DEATH_TYPES
    except Exception:
        pass
    fkills = []
    for t, killed, fr, td in player.kills:
        if fr == focus_vid:
            fkills.append((t, vmap.get(killed, {}).get("name", str(killed)),
                           DT.get(td, {}).get("name", "")))
    dmg_t, dmg_cum, run = [], [], 0
    for t, att, vic, amt in sorted((d for d in player.damage if d[1] == focus_vid)):
        run += amt; dmg_t.append(t); dmg_cum.append(run)
    total_dmg = run

    # clip span: first kill - lead, last kill + tail (whole carry), time-compressed
    span_start = max(0.0, fkills[0][0] - 12) if fkills else moment.start_s
    span_end = (fkills[-1][0] + 6) if fkills else moment.end_s
    tracks = build_tracks(player.positions, set(vmap))
    board = max(abs(min(p[2] for p in player.positions)),
                abs(max(p[2] for p in player.positions)),
                abs(min(p[3] for p in player.positions)),
                abs(max(p[3] for p in player.positions))) * 1.06

    n_frames = int(duration * FPS)
    ship_display = ship_name(meta.focus_ship) if focus_vid == pick_focus(vmap, None) and not focus_name else focus["name"]
    # for non-recorder POV we can't name the ship (needs GameParams); label by player
    ship_label = ship_name(meta.focus_ship)
    mp = map_name(meta.map_name)

    ff = imageio_ffmpeg.get_ffmpeg_exe()
    proc = subprocess.Popen(
        [ff, "-y", "-f", "rawvideo", "-pixel_format", "rgb24",
         "-video_size", f"{W}x{H}", "-framerate", str(FPS), "-i", "-",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", "-preset", "medium",
         str(out_path)],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

    hook_lines = None
    for f in range(n_frames):
        prog = f / n_frames
        t = span_start + prog * (span_end - span_start)
        img = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(img)

        # --- map grid + border ---
        for g in range(1, 10):
            gx = MAP_LEFT + g * MAP_SIZE / 10
            gy = MAP_TOP + g * MAP_SIZE / 10
            d.line([(gx, MAP_TOP), (gx, MAP_TOP + MAP_SIZE)], fill=GRID)
            d.line([(MAP_LEFT, gy), (MAP_LEFT + MAP_SIZE, gy)], fill=GRID)
        d.rectangle([MAP_LEFT, MAP_TOP, MAP_LEFT + MAP_SIZE, MAP_TOP + MAP_SIZE],
                    outline=(60, 80, 105), width=3)

        dead = {k for (kt, k, fr, td) in player.kills if kt <= t}

        # --- ships (focus drawn last so its marker is always on top) ---
        order = [e for e in vmap if e != focus_vid] + [focus_vid]
        for eid in order:
            info = vmap[eid]
            p = pos_at(tracks[eid], t)
            if p is None:
                continue
            px, py = w2p(p[0], p[1], board)
            is_focus = eid == focus_vid
            if eid in dead:
                col, r = (70, 75, 85), 5
                d.line([(px - r, py - r), (px + r, py + r)], fill=col, width=2)
                d.line([(px - r, py + r), (px + r, py - r)], fill=col, width=2)
                continue
            col = GOLD if is_focus else (CYAN if info["team"] == focus_team else RED)
            # trail
            ts, xs, zs = tracks[eid]
            i = bisect.bisect_right(ts, t) - 1
            j = i
            while j > 0 and t - ts[j] < 25:
                j -= 1
            pts = [w2p(xs[k], zs[k], board) for k in range(j, i + 1)]
            if len(pts) > 1:
                d.line(pts, fill=tuple(int(c * 0.45) for c in col), width=2)
            r = 13 if is_focus else 7
            d.ellipse([px - r, py - r, px + r, py + r], fill=col,
                      outline=WHITE if is_focus else None, width=3 if is_focus else 0)
            if is_focus:
                d.ellipse([px - r - 7, py - r - 7, px + r + 7, py + r + 7], outline=GOLD, width=3)
                lbl = (focus["name"] or "")[:14]
                lw = d.textlength(lbl, font=F_SMALL)
                d.text((px - lw / 2, py + r + 9), lbl, font=F_SMALL, fill=GOLD)

        # --- kill flashes: expanding ring at victim's last-known position ---
        for (kt, killed, fr, td) in player.kills:
            if fr == focus_vid and 0 <= t - kt < 1.3:
                vp = pos_at(tracks.get(killed, ([], [], [])), kt)
                if vp:
                    px, py = w2p(vp[0], vp[1], board)
                    rad = 14 + (t - kt) * 40
                    d.ellipse([px - rad, py - rad, px + rad, py + rad], outline=(255, 235, 120), width=3)

        # --- top: hook + title bar ---
        d.rectangle([0, 0, W, 360], fill=(8, 11, 16))
        if hook_lines is None:
            hook_lines = wrap(d, card.hook.upper(), F_HOOK, W - 80)
        hy = 70
        for ln in hook_lines[:3]:
            centered(d, W / 2, hy, ln, F_HOOK, GOLD); hy += 74
        centered(d, W / 2, 300, f"{ship_label}  •  {mp}", F_LABEL, WHITE)

        # --- bottom: live counters ---
        d.rectangle([0, 1480, W, H], fill=(8, 11, 16))
        frags = sum(1 for (kt, _, _) in fkills if kt <= t)
        dmg_now = 0
        if dmg_t:
            di = bisect.bisect_right(dmg_t, t) - 1
            dmg_now = dmg_cum[di] if di >= 0 else 0
        centered(d, W / 4, 1540, "FRAGS", F_LABEL, DIM)
        centered(d, W / 4, 1576, f"{frags} / {len(fkills)}", F_BIG, GOLD)
        centered(d, 3 * W / 4, 1540, "DAMAGE", F_LABEL, DIM)
        centered(d, 3 * W / 4, 1576, f"{int(dmg_now):,}", F_BIG, WHITE)

        # most recent frag callout
        recent = [(kt, vn, wep) for (kt, vn, wep) in fkills if kt <= t]
        if recent:
            kt, vn, wep = recent[-1]
            if t - kt < 4:
                verb = WEAPON_VERB.get(wep, "sunk")
                centered(d, W / 2, 1700, f"▸ {verb}  {vn}", F_LABEL, (255, 235, 120))

        # progress bar + branding
        d.rectangle([0, 1860, W * prog, 1868], fill=GOLD)
        centered(d, W / 2, 1792, "WORLD OF WARSHIPS", F_SMALL, DIM)

        proc.stdin.write(img.tobytes())

    proc.stdin.close()
    proc.wait()

    script = {
        "video_file": str(out_path),
        "hook": card.hook, "title": card.title, "caption": card.caption,
        "hashtags": card.hashtags,
        "pov_player": focus["name"], "ship": ship_label, "map": mp,
        "mode": meta.game_mode, "version": meta.client_version,
        "frags": len(fkills), "total_damage": int(total_dmg),
        "kills": [{"t": kt, "victim": vn, "weapon": wep} for kt, vn, wep in fkills],
        "moment": asdict(moment),
        "render_note": "tactical/data render — swap in in-game capture for final look",
    }
    return script


def main(argv):
    ap = argparse.ArgumentParser(description="Render a vertical highlight MP4 + script from a replay")
    ap.add_argument("replay")
    ap.add_argument("--focus", help="player POV (default: top fragger)")
    ap.add_argument("--duration", type=float, default=26.0)
    ap.add_argument("--out", default="clip.mp4")
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    out = Path(args.out)
    print(f"Rendering {Path(args.replay).name} -> {out} ({args.duration:.0f}s vertical)...")
    script = render(args.replay, args.focus, args.duration, out)
    if not script:
        return 1
    script_path = out.with_suffix(".script.json")
    Path(script_path).write_text(json.dumps(script, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  video : {out}  ({out.stat().st_size // 1024} KB)")
    print(f"  script: {script_path}")
    print(f"  {script['pov_player']} — {script['frags']} frags, {script['total_damage']:,} dmg on {script['map']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
