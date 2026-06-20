"""
Highlight Card generator.

Turns a scored HighlightMoment + battle context into the human-facing "script":
hook (first ~2s line), title, caption, hashtags, and a one-line rationale.

Two modes:
- Deterministic template (default): zero dependencies, always runs, good enough
  to review. This is what powers the pipeline when offline.
- Claude (optional): if ANTHROPIC_API_KEY is set, we ask Claude to write punchier,
  on-brand copy. We build the prompt here; wiring the actual API call is a single
  function you enable once a key + the `anthropic` SDK are available. The template
  output is the guaranteed fallback so the pipeline never blocks on the LLM.
"""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass

from scorer import HighlightMoment
from events import BattleMeta

# Friendly map for ship code -> display name. In production this comes from the
# game's GameParams; a few common ones are inlined so the demo reads naturally.
SHIP_DISPLAY = {
    "PVSD710-La-Pampa": "La Pampa",
    "PBSB510-Thunderer": "Thunderer",
    "PHSB710-Willem-de-Eerste": "Willem de Eerste",
}

MAP_DISPLAY = {
    "18_NE_ice_islands": "Ice Islands",
    "41_Conquest": "Conquest",
}


@dataclass
class HighlightCard:
    hook: str
    title: str
    caption: str
    hashtags: list[str]
    rationale: str
    moment: dict      # the HighlightMoment, for traceability in the review queue
    source: str       # "template" | "claude"


def ship_name(code: str) -> str:
    return SHIP_DISPLAY.get(code, code.split("-", 1)[-1].replace("-", " ") if "-" in code else code)


def map_name(code: str) -> str:
    if code in MAP_DISPLAY:
        return MAP_DISPLAY[code]
    # codes look like "18_NE_ice_islands" / "45_Zigzag": drop a leading number and
    # an optional 2-3 letter region token, then title-case the rest.
    parts = code.split("_")
    if parts and parts[0].isdigit():
        parts = parts[1:]
    if parts and len(parts[0]) <= 3 and parts[0].isupper():
        parts = parts[1:]
    return " ".join(parts).replace("-", " ").title() or code


def _template_card(m: HighlightMoment, meta: BattleMeta) -> HighlightCard:
    ship = ship_name(meta.focus_ship)
    mp = map_name(meta.map_name)
    k = m.stats.get("kills")
    clutch = "low_hp_frac" in m.stats

    if m.kind == "kraken":
        hook = f"5 kills in one game. Watch this {ship}."
        title = f"KRAKEN UNLEASHED — {ship}"
    elif m.kind.endswith("-kill") and k:
        hook = f"{k} ships deleted in {m.stats.get('citadels', 0) and 'a few' or 'seconds'}."
        title = f"{k}-for-1 in the {ship}"
    elif m.kind == "detonation":
        hook = f"One salvo. Instant detonation."
        title = f"DETONATION — {ship} one-shot"
    else:
        hook = f"This {ship} play is unreal."
        title = f"{m.kind.replace('-', ' ').title()} — {ship}"

    if clutch:
        hook = f"Down to {int(m.stats['low_hp_frac'] * 100)}% HP... then this."

    caption = (f"{m.narrative}. {ship} on {mp}. "
               f"Clip your best games and tag us! #WorldOfWarships")
    hashtags = ["#WorldOfWarships", "#WoWs", "#Warships", "#Gaming",
                f"#{ship.replace(' ', '')}", "#Shorts"]
    return HighlightCard(
        hook=hook, title=title, caption=caption, hashtags=hashtags,
        rationale=f"score={m.score} kind={m.kind}: {m.narrative}",
        moment=asdict(m), source="template",
    )


def build_claude_prompt(m: HighlightMoment, meta: BattleMeta) -> str:
    """The prompt we'd send to Claude for punchier, on-brand copy."""
    return (
        "You are a short-form social editor for the official World of Warships "
        "channel (TikTok/YouTube Shorts). Write copy for one vertical clip.\n\n"
        f"Clip facts:\n"
        f"- Ship: {ship_name(meta.focus_ship)}\n"
        f"- Map: {map_name(meta.map_name)} | Mode: {meta.game_mode}\n"
        f"- What happens: {m.narrative}\n"
        f"- Detected type: {m.kind} | internal score: {m.score}\n"
        f"- Raw stats: {m.stats}\n\n"
        "Return JSON with keys: hook (<=8 words, must land in the first 2 seconds), "
        "title (<=40 chars), caption (1 sentence + 1 CTA), hashtags (5-7, include "
        "#WorldOfWarships). Tone: hype but not cringe, esports-adjacent, brand-safe. "
        "No profanity, no trash talk toward real players."
    )


def generate_card(m: HighlightMoment, meta: BattleMeta, use_claude: bool | None = None) -> HighlightCard:
    """Generate a card. Uses Claude if available & requested, else the template."""
    if use_claude is None:
        use_claude = bool(os.environ.get("ANTHROPIC_API_KEY"))
    if use_claude:
        try:
            return _claude_card(m, meta)
        except Exception as e:  # never block the pipeline on the LLM
            card = _template_card(m, meta)
            card.rationale += f"  [claude fallback: {e}]"
            return card
    return _template_card(m, meta)


def _claude_card(m: HighlightMoment, meta: BattleMeta) -> HighlightCard:
    """Live Claude call. Requires `anthropic` SDK + ANTHROPIC_API_KEY."""
    import json

    from anthropic import Anthropic  # imported lazily so the template path has no deps

    client = Anthropic()
    resp = client.messages.create(
        model="claude-opus-4-8",
        max_tokens=400,
        messages=[{"role": "user", "content": build_claude_prompt(m, meta)}],
    )
    text = resp.content[0].text
    data = json.loads(text[text.index("{"):text.rindex("}") + 1])
    return HighlightCard(
        hook=data["hook"], title=data["title"], caption=data["caption"],
        hashtags=data["hashtags"], rationale=f"claude | score={m.score} {m.narrative}",
        moment=asdict(m), source="claude",
    )
