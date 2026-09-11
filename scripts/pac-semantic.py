#!/usr/bin/env python3
"""
pac-semantic.py — Local LLM semantic classification for PAC.

Classifies short work descriptions into PAC business lines using a local
ollama model. Batches all texts into a single call for speed (one call,
not one per trace). Falls back to keyword matching when ollama is
unreachable or the call fails — graceful degradation, never a crash.

Stdlib only (urllib). No external deps.

Env overrides:
    PAC_OLLAMA_URL   — ollama endpoint (default http://localhost:11434/api/generate)
    PAC_LLM_MODEL    — model name (default qwen3.5:9b-mlx)
    PAC_LLM_TIMEOUT  — per-call timeout in seconds (default 90)
"""
from __future__ import annotations

import json
import os
import re
import urllib.request

OLLAMA_URL = os.environ.get("PAC_OLLAMA_URL", "http://localhost:11434/api/generate")
TAGS_URL = os.environ.get("PAC_OLLAMA_TAGS_URL", "http://localhost:11434/api/tags")
DEFAULT_MODEL = os.environ.get("PAC_LLM_MODEL", "qwen3.5:9b-mlx")
TIMEOUT = float(os.environ.get("PAC_LLM_TIMEOUT", "90"))

SYSTEM_PROMPT = (
    "You classify short work descriptions into exactly one of these lines: "
    "{lines}. "
    "Nuance matters: 'production' in a creative context is Creative Practice, "
    "'production management' or 'production admin' is Administration. "
    "Return ONLY a JSON object mapping each id to its line, "
    "e.g. {{\"1\": \"Research\"}}. No prose, no markdown fences."
)


def ollama_available() -> bool:
    """Cheap liveness probe — returns True if ollama is serving."""
    try:
        with urllib.request.urlopen(TAGS_URL, timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def _extract_json(text: str) -> dict | None:
    """Robustly pull a JSON object out of a model response."""
    text = text.strip()
    # Strip markdown fences if present
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.M)
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    # Fallback: find the first {...} block
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            obj = json.loads(m.group(0))
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
    return None


def classify_batch(
    texts: dict[str, str],
    lines: list[str],
    model: str | None = None,
) -> dict[str, str] | None:
    """Classify {id: text} into business lines.

    Returns {id: line} for the texts the model classified, or None on
    failure (caller falls back to keyword matching). Only lines present
    in `lines` are accepted — anything else is dropped.
    """
    if not texts:
        return {}
    model = model or DEFAULT_MODEL
    line_set = set(lines)
    system = SYSTEM_PROMPT.format(lines=", ".join(lines))
    prompt = "Classify each id:\n" + "\n".join(
        f'{i}: "{t}"' for i, t in texts.items()
    )
    payload = {
        "model": model,
        "prompt": prompt,
        "system": system,
        "stream": False,
        "options": {"temperature": 0.0},
    }
    try:
        req = urllib.request.Request(
            OLLAMA_URL,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            resp = json.loads(r.read()).get("response", "")
        obj = _extract_json(resp)
        if obj is None:
            return None
        return {
            str(k): str(v)
            for k, v in obj.items()
            if str(v) in line_set
        }
    except Exception:
        return None
