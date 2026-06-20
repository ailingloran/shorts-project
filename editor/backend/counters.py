"""
Animated frag/damage counter overlay renderer.

Produces a transparent RGBA video (qtrle .mov) the exporter composites over a
segment. Counter values are driven by the decoded replay timeline and synced to
the footage via battle_t = (seg_in + export_t * speed) + battle_offset. Reuses
the render_clip.py approach (PIL frames streamed to ffmpeg) but transparent and
positioned, instead of a full-frame bar.
"""
from __future__ import annotations

import bisect
import subprocess
from pathlib import Path

import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFont

FF = imageio_ffmpeg.get_ffmpeg_exe()

GOLD = (255, 205, 70, 255)
WHITE = (242, 246, 252, 255)
DIM = (180, 190, 205, 255)
POP = (255, 235, 120, 255)
PILL = (10, 13, 20, 170)

WEAPON_VERB = {
    "AP_SHELL": "AP citadel", "CS_SHELL": "AP citadel", "HE_SHELL": "HE",
    "TORPEDO": "torpedo", "FLOOD": "flooding", "BURNING": "fire",
    "ADBOMB": "depth charge", "BOMB": "bomb", "DETONATE": "DETONATION",
}


def _font(opts, size):
    for p in opts:
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default()


F_NUM = _font([r"C:\Windows\Fonts\ariblk.ttf", r"C:\Windows\Fonts\arialbd.ttf"], 76)
F_LBL = _font([r"C:\Windows\Fonts\arialbd.ttf"], 28)
F_CALL = _font([r"C:\Windows\Fonts\arialbd.ttf"], 34)


MARGIN = 40


def _pill(d: ImageDraw.ImageDraw, w: int, h: int, anchor: str, dx: int, dy: int,
          label: str, value: str, val_font, val_color, scale: float = 1.0):
    lw = d.textlength(label, font=F_LBL)
    vw = d.textlength(value, font=val_font)
    pw = int(max(lw, vw) + 56)
    ph = 132
    # place the whole pill inside the frame per anchor (edges, not center)
    if "right" in anchor:
        x0 = w - MARGIN - pw
    elif "left" in anchor:
        x0 = MARGIN
    else:
        x0 = (w - pw) // 2
    y0 = (h - MARGIN - ph) if "bottom" in anchor else (MARGIN if "top" in anchor else (h - ph) // 2)
    x0 += dx
    y0 += dy
    cx = x0 + pw / 2
    d.rounded_rectangle([x0, y0, x0 + pw, y0 + ph], radius=18, fill=PILL)
    d.text((cx - lw / 2, y0 + 16), label, font=F_LBL, fill=DIM)
    f = val_font
    if scale != 1.0:                      # kill "pop"
        f = ImageFont.truetype(val_font.path, max(8, int(val_font.size * scale)))
        vw = d.textlength(value, font=f)
    d.text((cx - vw / 2, y0 + 52), value, font=f, fill=val_color)


def render_counter_overlay(out_path, w, h, fps, duration_s, seg_in_s, seg_speed,
                           battle_offset, kills, damage_curve, frags_total,
                           show_frags=True, show_damage=True,
                           frags_anchor="bottom_left", damage_anchor="bottom_right"):
    """Render a transparent counter overlay .mov for one segment."""
    kt = sorted(k["t"] for k in kills)
    kills_sorted = sorted(kills, key=lambda k: k["t"])
    dmg_t = [d["t"] for d in damage_curve]
    dmg_v = [d["cum"] for d in damage_curve]

    proc = subprocess.Popen(
        [FF, "-y", "-hide_banner", "-f", "rawvideo", "-pixel_format", "rgba",
         "-video_size", f"{w}x{h}", "-framerate", str(fps), "-i", "-",
         "-c:v", "qtrle", str(out_path)],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    n = max(1, int(duration_s * fps))
    for f in range(n):
        export_t = f / fps
        battle_t = seg_in_s + export_t * seg_speed + battle_offset
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)

        frags_now = bisect.bisect_right(kt, battle_t)
        # pop the frags number briefly after each kill
        scale = 1.0
        last_kill_dt = min((battle_t - t for t in kt if 0 <= battle_t - t <= 1.3), default=None)
        if last_kill_dt is not None:
            scale = 1.0 + 0.35 * (1 - last_kill_dt / 1.3)

        if show_frags:
            _pill(d, w, h, frags_anchor, 0, 0, "FRAGS", f"{frags_now} / {frags_total}", F_NUM, GOLD, scale)
        if show_damage:
            di = bisect.bisect_right(dmg_t, battle_t) - 1
            dmg_now = dmg_v[di] if di >= 0 else 0
            _pill(d, w, h, damage_anchor, 0, 0, "DAMAGE", f"{dmg_now:,}", F_NUM, WHITE)

        # kill callout, centered, ~3s after a kill
        recent = [k for k in kills_sorted if 0 <= battle_t - k["t"] < 3.0]
        if recent:
            k = recent[-1]
            verb = WEAPON_VERB.get(k["weapon"], "sunk")
            target = k.get("victim_ship") or k.get("victim")
            txt = f"» {verb}  {target}"
            tw = d.textlength(txt, font=F_CALL)
            d.text((w / 2 - tw / 2, h - 330), txt, font=F_CALL, fill=POP)

        proc.stdin.write(img.tobytes())

    proc.stdin.close()
    proc.wait()
    return Path(out_path)


if __name__ == "__main__":
    # standalone smoke test against real Taihang data (offset 0), window 358-400s
    import sys
    from replay_data import decode_for_editor
    data = decode_for_editor(sys.argv[1])
    render_counter_overlay("counter_test.mov", 1080, 1920, 30, 42, 358, 1.0, 0.0,
                           data["kills"], data["damage_curve"], data["frags_total"])
    print("wrote counter_test.mov ; kills:", len(data["kills"]), "total frags:", data["frags_total"])
