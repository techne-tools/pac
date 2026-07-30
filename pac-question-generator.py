#!/usr/bin/env python3
"""
pac-question-generator.py — Challenge Generator (Hermes port)

Generates challenges in two voices:
  - socratic (default): Mirror-based, interrogative, follows 5 quality rules
  - peer: Collaborative, offering-based, rehearsal-room register

The voice can be set via --voice flag or VOICE env var.

Usage:
    cat signals.json | python3 pac-question-generator.py [--voice socratic|peer]
    VOICE=peer cat signals.json | python3 pac-question-generator.py

Stdlib only.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any


# ============================================================================
# Voice: Socratic  (original default)
# ============================================================================

SOCRATIC: dict[str, dict[str, Any]] = {
    "repetition": {
        "observation_fmt": (
            "In the past {lookback_days} days you started {new_events_count} "
            "new items. Samples: {sample_summary}"
        ),
        "human_fmt": "You keep starting new things without finishing the old ones.",
        "body": [
            "Of these {new_events_count} items, which one is 'must do or fail' "
            "and which is 'nice to do'?",
            "If you could only work on 3 things right now, which 3 would they be? "
            "Why not the other {new_events_count_minus_3}?",
            "Is the real motivation opportunity, or using novelty to avoid "
            "the hard thing you already know needs doing?",
        ],
        "closing": "*No answer needed right now.*",
    },
    "claim_action_conflict": {
        "observation_fmt": (
            "You stated focus on {declared_focus}, but actual effort shows: "
            "{conflicting_lines_str}"
        ),
        "human_fmt": "What you say is priority and where your energy goes don't match.",
        "body": [
            "Is the stated priority still true, or has it quietly shifted?",
            "If you had to make your actions match your words today, "
            "which side would you change — the words or the actions?",
            "What's the structural force pulling you away from what you say matters?",
        ],
        "closing": "*No answer needed right now.*",
    },
    "capability_mismatch": {
        "observation_fmt": (
            "You delegated a strategic task to {person}. Based on their role, "
            "they're better suited to execution than strategic judgement."
        ),
        "human_fmt": "Strategic work landed on someone whose strength is execution.",
        "body": [
            "Why did you delegate this rather than own it yourself? "
            "Is it time, or is it discomfort with the decision?",
            "If this goes wrong, what's the cost — to the work, to them, to you?",
            "Are you developing them, or are you avoiding the weight of the decision?",
        ],
        "closing": "*No answer needed right now.*",
    },
    "sunk_cost": {
        "observation_fmt": (
            "{line} has been at zero growth for {zero_growth_days} days, "
            "but you're still making tactical moves ({recent_action_count} recent actions)."
        ),
        "human_fmt": "Something's been stalled for a while but you're still feeding it.",
        "body": [
            "Has the core assumption behind {line} ever been tested, "
            "or are you just iterating on tactics?",
            "What specific signal would make you stop? Name the number, the date, the metric.",
            "Are you still in it because you believe, or because stopping "
            "would mean admitting the initial judgement was wrong?",
        ],
        "closing": "*No answer needed right now.*",
    },
    "decision_node": {
        "observation_fmt": (
            "You're facing a major decision: {matched_text}"
        ),
        "human_fmt": "A significant decision point — worth pausing on.",
        "body": [
            "What's the worst that could happen if this goes wrong? "
            "Can you live with that?",
            "Have you asked yourself *why* you're doing this, "
            "not just *how* to do it?",
            "If you looked back in 3 years, would you be glad you made this decision, "
            "or would you wish you'd paused?",
        ],
        "closing": "*No answer needed right now.*",
    },
}


# ============================================================================
# Voice: Peer  (collaborative, rehearsal-room register)
# ============================================================================

PEER: dict[str, dict[str, Any]] = {
    "repetition": {
        "observation_fmt": (
            "There are a few open threads right now. In the past {lookback_days} days, "
            "{new_events_count} new things have come up — samples: {sample_summary}"
        ),
        "human_fmt": "A few open threads — let's see which ones want to land.",
        "body": [
            "Want to put them in front of us and see which ones have the strongest "
            "conditions to land somewhere? I can help sketch what a real threshold "
            "event would look like for each — the thing that tells you "
            '"this one\'s actually happening".',
            "We can do it quick, like a blocking run — no commitment beyond that.",
        ],
        "closing": "*I'll keep an eye on the shape of it. Let me know when you want to look together.*",
    },
    "claim_action_conflict": {
        "observation_fmt": (
            "Your stated focus is on {declared_focus}, but actual effort shows: "
            "{conflicting_lines_str}"
        ),
        "human_fmt": "The words and the time are in divergence — that happens.",
        "body": [
            "Want to do a quick trace together? We look at where attention "
            "has actually been going and see what forces are shaping that.",
            "Then we decide what adjusts: the words, the actions, or just "
            "the understanding of what's actually going on.",
        ],
        "closing": "*No pressure. I can set up the trace and we walk through it when you're ready.*",
    },
    "capability_mismatch": {
        "observation_fmt": (
            "This strategic gesture landed with {person} — their practice "
            "is more about execution than strategic design."
        ),
        "human_fmt": "A strategic gesture sitting at an execution level — worth checking the frame.",
        "body": [
            "That might be exactly right, or it might be a shape we should "
            "look at together. If ownership needs to come back to you, "
            "I can help make room for that.",
            'If they\'re being developed into a strategic role, we should '
            "design the frame together properly — what's actually going "
            "on with this gesture?",
        ],
        "closing": "*Either way the gesture needs a clear dramaturgy. Let me know which direction.*",
    },
    "sunk_cost": {
        "observation_fmt": (
            "{line} has been sitting at zero growth for {zero_growth_days} days, "
            "but it's still getting attention ({recent_action_count} recent moves)."
        ),
        "human_fmt": "This piece has stalled — but something's holding it open.",
        "body": [
            "Want to work out together what a real release condition would "
            "look like? Not a deadline — a condition. The thing that would "
            "genuinely tell you either 'back in' or 'let it go'.",
            "We can set it up so you don't have to decide now — just know "
            "what the signal would be when it arrives.",
        ],
        "closing": "*I can help design the threshold test and remind you when we set. Let me know.*",
    },
    "decision_node": {
        "observation_fmt": (
            "You're at a threshold: {matched_text}"
        ),
        "human_fmt": "You're at a threshold — worth mapping before you step.",
        "body": [
            "Want to do a quick premortem together? We lay out the branching "
            "paths, where the course-correction points are, and the structural "
            "risk profile.",
            "Not to decide — just so you can see the full shape of the move "
            "before you step into it. I'll hold the map while we talk.",
        ],
        "closing": "*When you want to run it, say the word. I've got the frame ready.*",
    },
}


# ============================================================================
# Template selection
# ============================================================================

VOICES = {
    "socratic": SOCRATIC,
    "peer": PEER,
}


# ============================================================================
# Utilities
# ============================================================================

def _prepare_template_kwargs(evidence: dict[str, Any]) -> dict[str, Any]:
    """Flatten evidence into flat kwargs for template.format()."""
    kwargs = dict(evidence)
    if "sample_events" in kwargs:
        kwargs["sample_summary"] = "; ".join(
            e.get("task", "")[:60] for e in kwargs["sample_events"][:3]
        )
    if "conflicting_lines" in kwargs:
        kwargs["conflicting_lines_str"] = ", ".join(
            f"{line}({pct:.0%})" for line, pct in kwargs["conflicting_lines"]
        )
    if "quotes" in kwargs and kwargs["quotes"]:
        kwargs["person"] = kwargs["quotes"][0].get("person", "someone")
    kwargs.setdefault("person", "someone")

    # Add derived values for templates
    new_count = kwargs.get("new_events_count", 0)
    kwargs.setdefault("new_events_count_minus_3", max(0, new_count - 3))

    return kwargs


def _fallback_challenge(pattern: str, confidence: float, summary: str, voice: str) -> str:
    """Fallback when no template matched."""
    if voice == "peer":
        return (
            f"**I noticed something worth looking at together.**\n\n"
            f"_Pattern: {pattern} (confidence: {confidence:.0%})_\n"
        )

    return (
        f"**Pattern detected:** {pattern} (confidence: {confidence:.0%})\n\n"
        f"{summary}\n\n"
        f"**Questions to sit with:**\n"
        f"1. Has this pattern shown up before?\n"
        f"2. If it continues, what's the most likely outcome?\n\n"
        f"*No answer needed right now.*"
    )


# ============================================================================
# Generation
# ============================================================================

def generate_challenge(signals: list[dict[str, Any]], voice: str = "socratic") -> str:
    """Generate a challenge from the top signal. Returns markdown."""
    if not signals:
        return ""

    voice_templates = VOICES.get(voice, SOCRATIC)

    top = signals[0]
    pattern = top["pattern"]
    confidence = top.get("confidence", 0.70)
    template = voice_templates.get(pattern)

    if not template:
        summary = top.get("summary", "A pattern worth reflecting on.")
        return _fallback_challenge(pattern, confidence, summary, voice)

    try:
        kwargs = _prepare_template_kwargs(top["evidence"])
        observation = template["observation_fmt"].format(**kwargs)
        human = template["human_fmt"].format(**kwargs)
        body_lines = [line.format(**kwargs) for line in template["body"]]
        closing = template["closing"].format(**kwargs)
    except (KeyError, IndexError):
        observation = top.get("summary", "Pattern detected")
        human = "A pattern worth reflecting on."
        body_lines = [
            "Have you noticed this pattern yourself?",
            "If so, what's keeping it in place?",
        ]
        closing = "*No answer needed right now.*"

    # Build the output
    body_text = "\n\n".join(body_lines)

    return (
        f"**{human}**\n\n"
        f"_{observation}_\n\n"
        f"{body_text}\n\n"
        f"{closing}"
    )


# ============================================================================
# Validation
# ============================================================================

REQUIRED_TAGS = ("**",)


def validate_output(text: str) -> bool:
    """Basic validation: must contain bold markers."""
    return all(tag in text for tag in REQUIRED_TAGS)


# ============================================================================
# Main
# ============================================================================

def main() -> None:
    voice = os.environ.get("PAC_VOICE", os.environ.get("VOICE", "socratic"))

    # Check --voice flag (overrides env)
    args = sys.argv[1:]
    for i, arg in enumerate(args):
        if arg == "--voice" and i + 1 < len(args):
            voice = args[i + 1]
            break

    if voice not in VOICES:
        voice = "socratic"

    try:
        raw = sys.stdin.read().strip()
        signals = json.loads(raw) if raw else []
    except (json.JSONDecodeError, OSError):
        signals = []

    if not signals:
        sys.exit(0)

    output = generate_challenge(signals, voice=voice)

    if not validate_output(output):
        sys.exit(1)

    print(output)


if __name__ == "__main__":
    main()
