"""
Staged ffmpeg exporter (Phase A: trim -> 9:16 reframe -> audio stems + music -> cards -> mux).

Each segment is rendered independently (video + audio), muxed, concatenated, the
music bed is mixed in, intro/outro cards are added, and the result is a 1080x1920
MP4. Stages write intermediate files under work/<project_id>/ so they are easy to
inspect and (later) cache. Later phases insert counter overlays, subtitles,
transitions and effects between these stages.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFont

from .models import Project, Segment, Card

FF = imageio_ffmpeg.get_ffmpeg_exe()

GOLD = (255, 205, 70)
RED = (255, 90, 90)
WHITE = (242, 246, 252)
DIM = (170, 180, 195)


def _font(opts, size):
    for p in opts:
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default()


_FONTS = {
    "huge": (r"C:\Windows\Fonts\ariblk.ttf", 130),
    "big": (r"C:\Windows\Fonts\ariblk.ttf", 72),
    "med": (r"C:\Windows\Fonts\arialbd.ttf", 44),
    "small": (r"C:\Windows\Fonts\arialbd.ttf", 34),
}


def _style(style: str):
    base = {"huge_gold": "huge", "huge_red": "huge"}.get(style, style)
    name, size = _FONTS.get(base, _FONTS["big"])
    color = {"huge_gold": GOLD, "huge_red": RED}.get(style, WHITE)
    return _font([name, r"C:\Windows\Fonts\arialbd.ttf"], size), color


def _run(args: list[str], cwd: str | None = None):
    proc = subprocess.run([FF, "-y", "-hide_banner", *args], cwd=cwd,
                          stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed:\n{' '.join(str(a) for a in args)}\n{proc.stderr[-1500:]}")


def _ass_time(t: float) -> str:
    h = int(t // 3600); m = int((t % 3600) // 60); s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _write_ass(project: Project, work: Path) -> Path:
    o, sub = project.output, project.subtitles
    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {o.w}", f"PlayResY: {o.h}", "",
        "[V4+ Styles]",
        ("Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, "
         "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, "
         "Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding"),
        # white text, black outline, bottom-center, outlined
        f"Style: Default,{sub.font},{sub.size},&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,"
        f"100,100,0,0,1,4,1,2,60,60,{sub.margin_v},1", "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for c in sub.cues:
        txt = c.text.replace("\n", "\\N")
        lines.append(f"Dialogue: 0,{_ass_time(c.proj_in_s)},{_ass_time(c.proj_out_s)},"
                     f"Default,,0,0,0,,{txt}")
    p = work / "subs.ass"
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def _burn_subs(body: Path, project: Project, work: Path) -> Path:
    """Burn subtitles via an ASS file (run from work dir so the filter path is relative)."""
    _write_ass(project, work)
    o = project.output
    out = work / "body_sub.mp4"
    _run(["-i", str(body), "-vf", "subtitles=subs.ass", "-r", str(o.fps),
          "-c:v", "libx264", "-crf", str(o.crf), "-preset", o.preset, "-c:a", "copy", str(out)],
         cwd=str(work))
    return out


def probe(path: str) -> dict:
    """Best-effort probe via ffmpeg stderr: duration, fps, audio stream count."""
    out = subprocess.run([FF, "-hide_banner", "-i", path], capture_output=True, text=True).stderr
    dur = 0.0
    m = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", out)
    if m:
        h, mi, s = m.groups()
        dur = int(h) * 3600 + int(mi) * 60 + float(s)
    fps = 30.0
    mf = re.search(r"(\d+(?:\.\d+)?) fps", out)
    if mf:
        fps = float(mf.group(1))
    audio_tracks = len(re.findall(r"Stream #\d+:\d+.*: Audio", out))
    w = h = 0
    mr = re.search(r"Stream #\d+:\d+.*: Video.* (\d{2,5})x(\d{2,5})", out)
    if mr:
        w, h = int(mr.group(1)), int(mr.group(2))
    return {"duration_s": round(dur, 2), "fps": fps, "audio_tracks": audio_tracks,
            "width": w, "height": h}


def _crop_box(project: Project, seg: Segment) -> tuple[int, int, int, int]:
    """Return (cw, ch, cx, cy) for the 9:16 crop. Auto-centers when reframe.w<=0."""
    src = project.source(seg.source_id)
    ow, oh = project.output.w, project.output.h
    rf = seg.reframe
    if rf.w and rf.h:
        return rf.w, rf.h, rf.x, rf.y
    cw = round(src.height * ow / oh)
    cw -= cw % 2
    cx = max(0, (src.width - cw) // 2)
    return cw, src.height, cx, 0


def _kf_expr(kfs: list, key: str) -> str:
    """Piecewise-linear ffmpeg expr (in `t`) over keyframes, clamped at the ends.
    Wrapped in single quotes by the caller, so commas need no escaping."""
    pts = [(float(getattr(k, "t")), float(getattr(k, key))) for k in kfs]
    e = f"{pts[-1][1]}"                                   # after last kf: hold
    for i in range(len(pts) - 1, 0, -1):
        t0, v0 = pts[i - 1]
        t1, v1 = pts[i]
        dt = (t1 - t0) or 1.0
        seg = f"({v0}+({v1}-{v0})*(t-{t0})/{dt})"
        e = f"if(lt(t,{t1}),{seg},{e})"
    return f"if(lt(t,{pts[0][0]}),{pts[0][1]},{e})"        # before first kf: hold


def _reframe_vf(project: Project, seg: Segment) -> str:
    """Static crop, or a keyframed PAN. ffmpeg crop re-evaluates x/y per frame but
    NOT w/h (no eval option), so the 9:16 window pans at a constant size; the window
    size is taken from the first keyframe. (Smooth zoom-over-time isn't supported this
    way — use per-segment zoom or the zoom-punch effect for that.)"""
    o = project.output
    rf = seg.reframe
    if rf.mode == "keyframed" and len(rf.keyframes) >= 2:
        kfs = sorted(rf.keyframes, key=lambda k: k.t)
        w, h = int(kfs[0].w), int(kfs[0].h)
        return (f"crop=w={w}:h={h}:x='{_kf_expr(kfs,'x')}':y='{_kf_expr(kfs,'y')}',"
                f"scale={o.w}:{o.h},setsar=1")
    cw, ch, cx, cy = _crop_box(project, seg)
    return f"crop={cw}:{ch}:{cx}:{cy},scale={o.w}:{o.h},setsar=1"


def _seg_video(project: Project, seg: Segment, work: Path, i: int) -> Path:
    src = project.source(seg.source_id)
    o = project.output
    speed = max(seg.speed, 0.01)
    vf = _reframe_vf(project, seg)
    if speed != 1.0:
        vf += f",setpts=PTS/{speed}"
    vf += f",fps={o.fps},format=yuv420p"
    out = work / f"segv_{i}.mp4"
    # -ss/-t are INPUT options (before -i): they bound the SOURCE read, so setpts
    # speed changes stretch into the output instead of being truncated.
    _run(["-ss", str(seg.in_s), "-t", str(seg.out_s - seg.in_s), "-i", src.path,
          "-an", "-vf", vf, "-r", str(o.fps), "-c:v", "libx264", "-crf", str(o.crf),
          "-preset", o.preset, str(out)])
    return out


def _atempo_chain(speed: float) -> str:
    """atempo only accepts 0.5–2.0; decompose any speed into a chain of valid factors."""
    if speed == 1.0:
        return ""
    factors, s = [], speed
    while s < 0.5:
        factors.append(0.5); s /= 0.5
    while s > 2.0:
        factors.append(2.0); s /= 2.0
    factors.append(s)
    return ",".join(f"atempo={f:.4f}" for f in factors) + ","


def _seg_audio(project: Project, seg: Segment, work: Path, i: int) -> Path:
    src = project.source(seg.source_id)
    out = work / f"sega_{i}.m4a"
    stems = [s for s in src.audio_stems if not s.mute]
    dur = seg.out_s - seg.in_s
    speed = max(seg.speed, 0.01)
    if not stems:
        _run(["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
              "-t", str(seg.duration), "-c:a", "aac", str(out)])
        return out
    parts, labels = [], []
    for j, st in enumerate(stems):
        gain = f"volume={st.gain_db}dB," if st.gain_db else ""
        atempo = _atempo_chain(speed)
        parts.append(f"[0:a:{st.track}]{gain}{atempo}aresample=48000[a{j}]")
        labels.append(f"[a{j}]")
    if len(labels) == 1:
        fc = parts[0].replace("[a0]", "[aout]")
    else:
        fc = ";".join(parts) + ";" + "".join(labels) + f"amix=inputs={len(labels)}:normalize=0[aout]"
    _run(["-ss", str(seg.in_s), "-t", str(dur), "-i", src.path,
          "-filter_complex", fc, "-map", "[aout]", "-ac", "2", "-ar", "48000",
          "-c:a", "aac", str(out)])
    return out


def _composite(base: Path, overlay: Path, out: Path, project: Project):
    """Composite a transparent overlay (.mov) onto a segment video."""
    o = project.output
    _run(["-i", str(base), "-i", str(overlay),
          "-filter_complex", "[0:v][1:v]overlay=0:0,format=yuv420p[v]", "-map", "[v]",
          "-r", str(o.fps), "-c:v", "libx264", "-crf", str(o.crf), "-preset", o.preset, str(out)])


def _get_timeline(src, timelines: dict) -> dict:
    """Decode (cached) the source's replay timeline for counters/effects."""
    tl = timelines.get(src.id)
    if tl is None:
        from .replay_data import decode_for_editor
        tl = decode_for_editor(src.replay_path, src.focus_player)
        timelines[src.id] = tl
    return tl


def _segment_kill_times(seg: Segment, src, tl: dict) -> list[float]:
    """Kill times in segment-local export seconds (battle_t → src_t → clip time)."""
    off = src.battle_offset_s or 0.0
    speed = max(seg.speed, 0.01)
    out = []
    for k in tl["kills"]:
        local = (k["t"] - off - seg.in_s) / speed
        if 0 <= local <= seg.duration:
            out.append(round(local, 2))
    return out


def _apply_effects(project: Project, video: Path, kills_local: list[float],
                   work: Path, i: int) -> Path:
    """Flash + zoom-punch on kills (per-segment), keyed to decoded kill times."""
    fx = project.effects
    if not kills_local or not (fx.flash_on_kills or fx.zoom_on_kills):
        return video
    o = project.output
    filters = []
    if fx.zoom_on_kills:
        half = max(1, round(0.33 * o.fps))     # half-window in frames
        amt = fx.zoom_amount - 1.0
        terms = "+".join(f"{amt}*max(0\\,1-abs((on-{round(tk * o.fps)})/{half}))"
                         for tk in kills_local)
        z = f"1+{terms}" if terms else "1"
        filters.append(f"zoompan=z='{z}':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                       f"fps={o.fps}:s={o.w}x{o.h}")
    if fx.flash_on_kills:
        # time-gated translucent white box = a localized flash (chained fades would
        # white-out the whole clip, since fade=in holds the colour before its start).
        for tk in kills_local:
            filters.append(f"drawbox=x=0:y=0:w=iw:h=ih:color=white@0.7:t=fill:"
                           f"enable='between(t,{tk},{round(tk + 0.10, 2)})'")
    out = work / f"segfx_{i}.mp4"
    _run(["-i", str(video), "-vf", ",".join(filters), "-r", str(o.fps),
          "-c:v", "libx264", "-crf", str(o.crf), "-preset", o.preset, str(out)])
    return out


def _apply_counters(project: Project, seg: Segment, src, video: Path, work: Path,
                    i: int, tl: dict) -> Path:
    """Render + composite animated frag/damage counters for one segment."""
    from . import counters as C

    cfg = project.overlays.counters
    frags = next((c for c in cfg if c.kind == "frags" and c.enabled), None)
    dmg = next((c for c in cfg if c.kind == "damage" and c.enabled), None)
    if not frags and not dmg:
        return video

    o = project.output
    ov = work / f"ovl_{i}.mov"
    C.render_counter_overlay(
        ov, o.w, o.h, o.fps, seg.duration, seg.in_s, seg.speed, src.battle_offset_s or 0.0,
        tl["kills"], tl["damage_curve"], tl["frags_total"],
        show_frags=bool(frags), show_damage=bool(dmg),
        frags_anchor=frags.anchor if frags else "bottom_left",
        damage_anchor=dmg.anchor if dmg else "bottom_right")
    out = work / f"segc_{i}.mp4"
    _composite(video, ov, out, project)
    return out


def _mux(video: Path, audio: Path, out: Path):
    _run(["-i", str(video), "-i", str(audio), "-map", "0:v", "-map", "1:a",
          "-c:v", "copy", "-c:a", "copy", "-shortest", str(out)])


def _concat(parts: list[Path], out: Path, project: Project, reencode=True):
    lst = out.parent / f"{out.stem}_list.txt"
    lst.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in parts), encoding="utf-8")
    o = project.output
    if reencode:
        _run(["-f", "concat", "-safe", "0", "-i", str(lst), "-r", str(o.fps),
              "-c:v", "libx264", "-crf", str(o.crf), "-preset", o.preset, "-pix_fmt", "yuv420p",
              "-c:a", "aac", "-ar", "48000", "-ac", "2", str(out)])
    else:
        _run(["-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(out)])


def _concat_with_transitions(clips: list[Path], durs: list[float], out: Path,
                             project: Project, ttype: str, d: float):
    """Assemble segments with crossfade transitions (xfade video + acrossfade audio)."""
    o = project.output
    d = max(0.1, min(d, min(durs) / 2 - 0.05))      # keep < each clip so xfade offsets stay valid
    inputs = []
    for c in clips:
        inputs += ["-i", str(c)]
    fc = []
    prev, running = "0:v", durs[0]
    for i in range(1, len(clips)):
        off = round(running - d, 3)
        fc.append(f"[{prev}][{i}:v]xfade=transition={ttype}:duration={d}:offset={off}[v{i}]")
        prev = f"v{i}"
        running += durs[i] - d
    vout = prev
    aprev = "0:a"
    for i in range(1, len(clips)):
        fc.append(f"[{aprev}][{i}:a]acrossfade=d={d}[a{i}]")
        aprev = f"a{i}"
    _run([*inputs, "-filter_complex", ";".join(fc), "-map", f"[{vout}]", "-map", f"[{aprev}]",
          "-r", str(o.fps), "-c:v", "libx264", "-crf", str(o.crf), "-preset", o.preset,
          "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000", "-ac", "2", str(out)])


def _add_music(body: Path, project: Project, body_dur: float, out: Path):
    m = project.audio.music
    if not m.path:
        return body
    fout_start = max(0.0, body_dur - m.fade_out_s)
    music = (f"[1:a]volume={m.gain_db}dB,aresample=48000,"
             f"afade=t=in:st=0:d={m.fade_in_s},"
             f"afade=t=out:st={fout_start:.2f}:d={m.fade_out_s}")
    if m.duck:
        # duck the music whenever the gameplay/voice (body) is loud
        music_fc = (f"[0:a]aresample=48000,asplit=2[bmix][bkey];"
                    f"{music}[mus];"
                    f"[mus][bkey]sidechaincompress=threshold=0.04:ratio=8:"
                    f"attack=20:release=300[mduck];"
                    f"[bmix][mduck]amix=inputs=2:normalize=0:duration=first[a]")
    else:
        music_fc = f"{music}[m];[0:a][m]amix=inputs=2:normalize=0:duration=first[a]"
    _run(["-i", str(body), "-ss", str(m.in_s), "-i", m.path,
          "-filter_complex", music_fc, "-map", "0:v", "-map", "[a]",
          "-c:v", "copy", "-c:a", "aac", "-ar", "48000", str(out)])
    return out


def _render_card(card: Card, project: Project, path: Path):
    o = project.output
    img = Image.new("RGB", (o.w, o.h), tuple(int(card.bg[k:k + 2], 16) for k in (1, 3, 5)))
    d = ImageDraw.Draw(img)
    fonts = [_style(ln.style) for ln in card.lines]
    heights = [f.getbbox(ln.text)[3] + 18 for ln, (f, _) in zip(card.lines, fonts)]
    total = sum(heights)
    y = (o.h - total) / 2
    for ln, (f, col) in zip(card.lines, fonts):
        w = d.textlength(ln.text, font=f)
        d.text(((o.w - w) / 2, y), ln.text, font=f, fill=col)
        y += f.getbbox(ln.text)[3] + 18
    if card.cta:
        f, _ = _style("small")
        w = d.textlength(card.cta, font=f)
        d.text(((o.w - w) / 2, o.h - 140), card.cta, font=f, fill=DIM)
    img.save(path)


def _card_clip(card: Card, project: Project, work: Path, name: str) -> Path:
    png = work / f"{name}.png"
    _render_card(card, project, png)
    o = project.output
    out = work / f"{name}.mp4"
    _run(["-loop", "1", "-i", str(png),
          "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
          "-t", str(card.dur_s), "-vf", f"scale={o.w}:{o.h},setsar=1,fps={o.fps},format=yuv420p",
          "-r", str(o.fps), "-c:v", "libx264", "-crf", str(o.crf), "-preset", o.preset,
          "-c:a", "aac", "-ar", "48000", "-ac", "2", "-shortest", str(out)])
    return out


def export(project: Project, work_dir: str | Path, out_path: str | Path) -> Path:
    work = Path(work_dir).resolve()        # absolute: _burn_subs runs ffmpeg with cwd=work
    work.mkdir(parents=True, exist_ok=True)
    out_path = Path(out_path)
    if not project.segments:
        raise ValueError("project has no segments")

    # Stage 1-4: per-segment video, (counter overlays), audio, then mux
    seg_clips: list[Path] = []
    body_dur = 0.0
    timelines: dict = {}
    fx = project.effects
    for i, seg in enumerate(project.segments):
        v = _seg_video(project, seg, work, i)
        src = project.source(seg.source_id)
        wants_data = src.replay_path and (project.overlays.counters
                                          or fx.flash_on_kills or fx.zoom_on_kills)
        tl = _get_timeline(src, timelines) if wants_data else None
        if tl and (fx.flash_on_kills or fx.zoom_on_kills):
            v = _apply_effects(project, v, _segment_kill_times(seg, src, tl), work, i)
        if tl and project.overlays.counters:
            v = _apply_counters(project, seg, src, v, work, i, tl)
        a = _seg_audio(project, seg, work, i)
        m = work / f"seg_{i}.mp4"
        _mux(v, a, m)
        seg_clips.append(m)
        body_dur += seg.duration

    # Stage 5: concat segments (hard cuts) or crossfade transitions
    body = work / "body.mp4"
    tr = project.transition
    if tr.type != "none" and len(seg_clips) > 1:
        _concat_with_transitions(seg_clips, [s.duration for s in project.segments],
                                 body, project, tr.type, tr.duration)
    else:
        _concat(seg_clips, body, project)

    # Stage 6: burn subtitles (proj-time, over the assembled gameplay body)
    if project.subtitles.cues:
        body = _burn_subs(body, project, work)

    # Stage 7: music bed
    if project.audio.music.path:
        body_m = work / "body_music.mp4"
        body = _add_music(body, project, body_dur, body_m)

    # Stage 8: intro/outro + final concat
    parts: list[Path] = []
    if project.intro.enabled and project.intro.lines:
        parts.append(_card_clip(project.intro, project, work, "intro"))
    parts.append(body)
    if project.outro.enabled and project.outro.lines:
        parts.append(_card_clip(project.outro, project, work, "outro"))

    if len(parts) == 1:
        _concat(parts, out_path, project)
    else:
        _concat(parts, out_path, project)
    return out_path


if __name__ == "__main__":
    import json
    import sys

    proj = Project.model_validate_json(Path(sys.argv[1]).read_text(encoding="utf-8"))
    work = Path(sys.argv[1]).parent / f"work_{proj.project_id}"
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(f"{proj.project_id}.mp4")
    result = export(proj, work, out)
    print(f"exported {result}  ({result.stat().st_size // 1024} KB)")
