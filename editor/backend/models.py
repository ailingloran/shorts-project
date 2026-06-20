"""
Project data model for the WoWS shorts editor.

One Project (serialized as project.json) is the single source of truth that the
browser UI edits and the exporter consumes. This is the Phase-A subset; later
phases extend it (keyframed reframe, transitions, counters, subtitles, effects)
without breaking the schema.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class AudioStem(BaseModel):
    track: int                      # audio-relative stream index in the source (0:a:<track>)
    role: str = "game"              # "game" | "mic" | "mix"
    label: str = ""
    gain_db: float = 0.0
    mute: bool = False


class Source(BaseModel):
    id: str
    path: str
    width: int = 2560
    height: int = 1440
    fps: float = 30.0
    duration_s: float = 0.0
    audio_stems: list[AudioStem] = Field(default_factory=list)
    replay_path: Optional[str] = None
    focus_player: Optional[str] = None          # POV for counters; None = top fragger
    battle_offset_s: float = 0.0                # battle_t = src_t + battle_offset_s
    events_available: bool = False


class ReframeKey(BaseModel):
    t: float          # segment-local seconds (clip-natural time, before speed)
    x: int
    y: int
    w: int
    h: int


class Reframe(BaseModel):
    """9:16 crop box over the 16:9 source. w<=0 means auto-centered full-height 9:16."""
    mode: Literal["static", "keyframed"] = "static"
    x: int = 0
    y: int = 0
    w: int = 0
    h: int = 0
    keyframes: list[ReframeKey] = Field(default_factory=list)


class Segment(BaseModel):
    id: str
    source_id: str
    in_s: float
    out_s: float
    speed: float = 1.0
    reframe: Reframe = Field(default_factory=Reframe)

    @property
    def duration(self) -> float:
        return max(0.0, (self.out_s - self.in_s) / max(self.speed, 0.01))


class MusicBed(BaseModel):
    path: Optional[str] = None
    gain_db: float = -8.0
    in_s: float = 0.0               # offset into the music file
    fade_in_s: float = 0.4
    fade_out_s: float = 0.8
    duck: bool = False              # Phase F


class Audio(BaseModel):
    music: MusicBed = Field(default_factory=MusicBed)


class CardLine(BaseModel):
    text: str
    style: Literal["huge", "huge_gold", "huge_red", "big", "med", "small"] = "big"


class Card(BaseModel):
    enabled: bool = False
    dur_s: float = 1.6
    lines: list[CardLine] = Field(default_factory=list)
    cta: Optional[str] = None
    bg: str = "#080B10"


class Counter(BaseModel):
    kind: Literal["frags", "damage"] = "frags"
    enabled: bool = True
    anchor: str = "bottom_left"                 # bottom_left | bottom_right | bottom_center | top_*
    dx: int = 0
    dy: int = 0


class Overlays(BaseModel):
    counters: list[Counter] = Field(default_factory=list)


class SubCue(BaseModel):
    proj_in_s: float                            # seconds into the gameplay body
    proj_out_s: float
    text: str


class Subtitles(BaseModel):
    cues: list[SubCue] = Field(default_factory=list)
    font: str = "Arial Black"
    size: int = 54
    margin_v: int = 180                         # px above the bottom edge


class Effects(BaseModel):
    flash_on_kills: bool = False
    zoom_on_kills: bool = False
    zoom_amount: float = 1.15       # peak zoom factor at each kill


class Transition(BaseModel):
    # xfade transition name: none | fade | fadeblack | dissolve | wipeleft | slideleft | ...
    type: str = "none"
    duration: float = 0.4


class Output(BaseModel):
    w: int = 1080
    h: int = 1920
    fps: int = 30
    crf: int = 19
    preset: str = "medium"


class Project(BaseModel):
    schema_version: int = 1
    project_id: str
    title: str = "Untitled"
    output: Output = Field(default_factory=Output)
    sources: list[Source] = Field(default_factory=list)
    segments: list[Segment] = Field(default_factory=list)
    audio: Audio = Field(default_factory=Audio)
    overlays: Overlays = Field(default_factory=Overlays)
    effects: Effects = Field(default_factory=Effects)
    transition: Transition = Field(default_factory=Transition)
    subtitles: Subtitles = Field(default_factory=Subtitles)
    intro: Card = Field(default_factory=Card)
    outro: Card = Field(default_factory=Card)

    def source(self, source_id: str) -> Source:
        for s in self.sources:
            if s.id == source_id:
                return s
        raise KeyError(f"source {source_id} not found")
