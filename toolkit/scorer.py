"""
Highlight scorer — the WoWS-specific core IP.

Turns a normalized Battle (events.py) into a ranked list of HighlightMoment
candidates, each a clip-ready time window with a score and a narrative tag.

Design principles:
- Deterministic + cheap. No game client, no video, no ML required to *find*
  candidates. (An LLM later writes the human-facing card; it does not need to
  find the moment.)
- Score = rarity x magnitude x narrative arc. Rare + big + a story beats a
  single big-number hit with no context.
- Everything is tunable from SCORING so the Phase-3 feedback loop (what actually
  goes viral) can adjust weights without touching detection logic.
"""
from __future__ import annotations

from dataclasses import dataclass

from events import Battle, Event, EventType

# ---- tunable knobs (later: learned from published-clip performance) ----------
SCORING = {
    "multikill_window_s": 12.0,   # kills this close together form a cluster
    "clip_pad_before_s": 6.0,     # how much lead-in a clip needs
    "clip_pad_after_s": 4.0,
    "weights": {                  # base points per signal
        "kill": 10.0,
        "citadel": 6.0,
        "detonation": 14.0,
        "torpedo_hit": 3.0,
        "big_salvo_per_1k_dmg": 0.4,
    },
    "achievement_bonus": {        # rare achievements = instant strong candidates
        "Kraken": 60.0,           # 5 kills in one battle
        "Devastating Strike": 30.0,
        "Dreadnought": 18.0,
        "Confederate": 16.0,
        "High Caliber": 16.0,
        "First Blood": 8.0,
    },
    "narrative": {
        "comeback_low_hp_frac": 0.15,  # surviving below this HP = clutch arc
        "comeback_bonus": 25.0,
        "closing_seconds_s": 60.0,     # action in the last minute = tension
        "closing_bonus": 12.0,
    },
}


@dataclass
class HighlightMoment:
    start_s: float
    end_s: float
    score: float
    kind: str                 # primary tag: "kraken" | "multikill" | "detonation" | ...
    narrative: str            # short machine description, fed to the card writer
    stats: dict               # raw counts that justify the score (for the card + audit)

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s


def _window(events: list[Event], center: float, before: float, after: float) -> tuple[float, float]:
    start = max(0.0, center - before)
    return start, center + after


def score_battle(battle: Battle) -> list[HighlightMoment]:
    """Return HighlightMoments ranked best-first."""
    w = SCORING["weights"]
    moments: list[HighlightMoment] = []

    kills = [e for e in battle.events if e.type == EventType.KILL]
    achievements = [e for e in battle.events if e.type == EventType.ACHIEVEMENT]
    dets = [e for e in battle.events if e.type == EventType.DETONATION]

    # --- 1. Multi-kill clusters (the bread and butter of WoWS clips) ----------
    used: set[int] = set()
    for i, k in enumerate(kills):
        if i in used:
            continue
        cluster = [k]
        used.add(i)
        for j in range(i + 1, len(kills)):
            if kills[j].t - cluster[-1].t <= SCORING["multikill_window_s"]:
                cluster.append(kills[j])
                used.add(j)
            else:
                break
        n = len(cluster)
        if n < 2:
            continue  # single kills handled only if attached to an achievement

        center_lo, center_hi = cluster[0].t, cluster[-1].t
        start = max(0.0, center_lo - SCORING["clip_pad_before_s"])
        end = center_hi + SCORING["clip_pad_after_s"]

        # supporting events inside the window enrich the score
        cit = _count_between(battle, EventType.CITADEL, start, end)
        dmg = _sum_between(battle, EventType.DAMAGE, start, end)
        score = n * w["kill"] + cit * w["citadel"] + (dmg / 1000.0) * w["big_salvo_per_1k_dmg"]

        kind = {2: "double", 3: "triple", 4: "quad"}.get(n, f"{n}x") + "-kill"
        score += _narrative_bonus(battle, start, end, moments_stats := {})
        moments.append(HighlightMoment(
            start, end, round(score, 1), kind,
            narrative=f"{n} kills in {round(center_hi - center_lo, 1)}s"
                      + (f", {cit} citadels" if cit else "")
                      + (f", {int(dmg):,} dmg" if dmg else "")
                      + moments_stats.get("note", ""),
            stats={"kills": n, "citadels": cit, "damage": int(dmg), **moments_stats},
        ))

    # --- 1b. Whole-match carry (frags spread out, not clustered) --------------
    # Real Krakens are often spread across minutes, so the cluster detector above
    # misses them. Reward total frag count directly and label 5+ as a Kraken.
    if len(kills) >= 4:
        first, last = kills[0].t, kills[-1].t
        # clip the densest kill burst rather than the whole game
        burst_start, burst_end = _densest_kill_burst(kills, span=20.0)
        n = len(kills)
        is_kraken = n >= 5
        score = n * w["kill"] * 1.4  # carry premium
        extra = {}
        score += _narrative_bonus(battle, burst_start, burst_end, extra)
        moments.append(HighlightMoment(
            burst_start, burst_end, round(score, 1),
            kind="kraken" if is_kraken else "carry",
            narrative=(f"{n}-kill carry" if not is_kraken else "KRAKEN: 5+ kills")
                      + f" across the match" + extra.get("note", ""),
            stats={"total_frags": n, **extra},
        ))

    # --- 2. Achievements as anchors (Kraken etc.) -----------------------------
    for a in achievements:
        bonus = SCORING["achievement_bonus"].get(a.label, 10.0)
        start, end = _window(battle.events, a.t, SCORING["clip_pad_before_s"] + 4, SCORING["clip_pad_after_s"])
        extra = {}
        score = bonus + _narrative_bonus(battle, start, end, extra)
        moments.append(HighlightMoment(
            start, end, round(score, 1),
            kind=(a.label or "achievement").lower().replace(" ", "-"),
            narrative=f"{a.label}" + extra.get("note", ""),
            stats={"achievement": a.label, **extra},
        ))

    # --- 3. Detonations (rare, spectacular, instant) --------------------------
    for d in dets:
        start, end = _window(battle.events, d.t, SCORING["clip_pad_before_s"], SCORING["clip_pad_after_s"])
        extra = {}
        score = w["detonation"] + _narrative_bonus(battle, start, end, extra)
        moments.append(HighlightMoment(
            start, end, round(score, 1), "detonation",
            narrative=f"detonated {d.target or 'an enemy'}" + extra.get("note", ""),
            stats={"target": d.target, **extra},
        ))

    moments = _dedupe_overlaps(moments)
    moments.sort(key=lambda m: m.score, reverse=True)
    return moments


def _densest_kill_burst(kills: list[Event], span: float) -> tuple[float, float]:
    """Find the `span`-second window containing the most kills; center a clip on it."""
    best_count, best_center = 0, kills[len(kills) // 2].t
    for k in kills:
        count = sum(1 for o in kills if k.t <= o.t <= k.t + span)
        if count > best_count:
            best_count, best_center = count, k.t + span / 2
    return max(0.0, best_center - SCORING["clip_pad_before_s"]), best_center + SCORING["clip_pad_after_s"]


def _narrative_bonus(battle: Battle, start: float, end: float, out: dict) -> float:
    """Add story value: low-HP clutch survival and closing-seconds tension."""
    bonus = 0.0
    n = SCORING["narrative"]

    # comeback / clutch: was the focus player critically low on HP in this window?
    hp_samples = [e for e in battle.events if e.type == EventType.HP and start <= e.t <= end]
    if hp_samples and battle.meta.full_hp > 0:
        low = min(e.value for e in hp_samples) / battle.meta.full_hp
        if low <= n["comeback_low_hp_frac"]:
            bonus += n["comeback_bonus"]
            out["low_hp_frac"] = round(low, 3)
            out["note"] = out.get("note", "") + f", clutch at {int(low * 100)}% HP"

    # closing-seconds tension
    if end >= battle.meta.duration_s - n["closing_seconds_s"]:
        bonus += n["closing_bonus"]
        out["note"] = out.get("note", "") + ", in the final minute"

    return bonus


def _count_between(battle: Battle, t: EventType, start: float, end: float) -> int:
    return sum(1 for e in battle.events if e.type == t and start <= e.t <= end)


def _sum_between(battle: Battle, t: EventType, start: float, end: float) -> float:
    return sum(e.value for e in battle.events if e.type == t and start <= e.t <= end)


def _dedupe_overlaps(moments: list[HighlightMoment]) -> list[HighlightMoment]:
    """If two candidates overlap heavily, keep the higher-scoring one."""
    moments = sorted(moments, key=lambda m: m.score, reverse=True)
    kept: list[HighlightMoment] = []
    for m in moments:
        if any(_overlap(m, k) > 0.5 for k in kept):
            continue
        kept.append(m)
    return kept


def _overlap(a: HighlightMoment, b: HighlightMoment) -> float:
    lo, hi = max(a.start_s, b.start_s), min(a.end_s, b.end_s)
    inter = max(0.0, hi - lo)
    union = max(a.end_s, b.end_s) - min(a.start_s, b.start_s)
    return inter / union if union else 0.0
