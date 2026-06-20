"""
Assemble a vertical short from captured gameplay segments.

Stages (all via the bundled ffmpeg):
  1. PIL renders intro card, outro card, and a transparent persistent overlay
     (top label + bottom branding bar) at 1080x1920.
  2. Each "beat" (a time window from a recorded segment) is cropped 16:9 -> 9:16,
     scaled to 1080x1920, and the overlay is composited on top.
  3. Intro/outro PNGs become short clips.
  4. Everything is concatenated into the final vertical MP4.

This is the same overlay/encode stage the data-render used; here the source is
REAL captured gameplay footage instead of a tactical animation.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFont

FF = imageio_ffmpeg.get_ffmpeg_exe()
W, H = 1080, 1920
CAP = Path("cap")
OUT = Path("wisconsin_short.mp4")
TMP = Path("cap/_build")
TMP.mkdir(exist_ok=True)

# source crop: 2560x1440 -> centered 9:16 slice (810x1440) -> 1080x1920
SRC_W, SRC_H = 2560, 1440
CROP_W = int(SRC_H * 9 / 16)          # 810
CROP_X = (SRC_W - CROP_W) // 2        # centered

# the two combat beats: (segment_index, start_in_segment_s, duration_s)
# windows chosen from a brightness scan to avoid the death/camera fade at ~492-494s
BEATS = [
    (18, 9, 8),     # ~369-377s: sunset salvo (clean)
    (24, 1, 9),     # ~481-490s: golden-hour engagement (clean, before the fade)
]

GOLD = (255, 205, 70)
WHITE = (242, 246, 252)
DIM = (170, 180, 195)


def font(opts, size):
    for p in opts:
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default()


F_HUGE = font([r"C:\Windows\Fonts\ariblk.ttf", r"C:\Windows\Fonts\impact.ttf"], 130)
F_BIG = font([r"C:\Windows\Fonts\ariblk.ttf"], 72)
F_MED = font([r"C:\Windows\Fonts\arialbd.ttf"], 44)
F_SMALL = font([r"C:\Windows\Fonts\arialbd.ttf"], 34)


def centered(d, cx, y, text, fnt, fill):
    w = d.textlength(text, font=fnt)
    d.text((cx - w / 2, y), text, font=fnt, fill=fill)


def make_cards():
    # intro hook card
    intro = Image.new("RGB", (W, H), (8, 11, 16))
    d = ImageDraw.Draw(intro)
    centered(d, W / 2, 700, "I DID", F_BIG, WHITE)
    centered(d, W / 2, 790, "135K", F_HUGE, GOLD)
    centered(d, W / 2, 940, "DAMAGE", F_BIG, WHITE)
    centered(d, W / 2, 1080, "...and still lost.", F_MED, DIM)
    intro.save(TMP / "intro.png")

    # outro card
    outro = Image.new("RGB", (W, H), (8, 11, 16))
    d = ImageDraw.Draw(outro)
    centered(d, W / 2, 760, "DEFEAT", F_HUGE, (255, 90, 90))
    centered(d, W / 2, 940, "134,968 damage. Wisconsin.", F_MED, WHITE)
    centered(d, W / 2, 1010, "Some games you just can't carry.", F_SMALL, DIM)
    centered(d, W / 2, 1700, "WORLD OF WARSHIPS", F_SMALL, DIM)
    outro.save(TMP / "outro.png")

    # persistent transparent overlay (top label + bottom branding)
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    d.rectangle([0, 0, W, 120], fill=(0, 0, 0, 140))
    centered(d, W / 2, 38, "WISCONSIN  •  ARMS RACE", F_SMALL, WHITE)
    d.rectangle([0, H - 90, W, H], fill=(0, 0, 0, 140))
    centered(d, W / 2, H - 64, "WORLD OF WARSHIPS", F_SMALL, GOLD)
    ov.save(TMP / "overlay.png")


def run(args):
    subprocess.run([FF, "-y", *args], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)


def build_beat(i, seg, ss, dur):
    src = CAP / f"seg_{seg:03d}.mp4"
    out = TMP / f"beat_{i}.mp4"
    vf = (f"[0:v]crop={CROP_W}:{SRC_H}:{CROP_X}:0,scale={W}:{H},fps=30,setsar=1[v];"
          f"[v][1:v]overlay=0:0,format=yuv420p[o]")
    run(["-ss", str(ss), "-i", str(src), "-i", str(TMP / "overlay.png"), "-t", str(dur),
         "-filter_complex", vf, "-map", "[o]", "-c:v", "libx264", "-crf", "20",
         "-preset", "medium", "-r", "30", str(out)])
    return out


def card_clip(name, dur):
    out = TMP / f"{name}.mp4"
    run(["-loop", "1", "-i", str(TMP / f"{name}.png"), "-t", str(dur),
         "-vf", f"scale={W}:{H},format=yuv420p,fps=30", "-c:v", "libx264", "-crf", "20",
         "-preset", "medium", "-r", "30", str(out)])
    return out


def main():
    make_cards()
    clips = [card_clip("intro", 1.6)]
    for i, (seg, ss, dur) in enumerate(BEATS):
        clips.append(build_beat(i, seg, ss, dur))
    clips.append(card_clip("outro", 2.2))

    # concat (all share 1080x1920/30fps/yuv420p) via concat demuxer
    listfile = TMP / "list.txt"
    listfile.write_text("".join(f"file '{c.resolve().as_posix()}'\n" for c in clips), encoding="utf-8")
    run(["-f", "concat", "-safe", "0", "-i", str(listfile), "-c:v", "libx264",
         "-crf", "20", "-preset", "medium", "-pix_fmt", "yuv420p", str(OUT)])
    print(f"built {OUT}  ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
