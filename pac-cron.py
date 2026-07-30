#!/usr/bin/env python3
"""
pac-cron.py — Cron-mode PAC detector for Hermes

Runs the PAC analysis pipeline against the trace log and memory.
Uses the configured voice preference from pac-prefs.json.
If a self-limiting pattern is detected and a challenge generated,
saves it to the pending/ directory for delivery at next session start.

Silent (no stdout) when nothing to report — watchdog pattern.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────

SCRIPTS_DIR = Path.home() / ".hermes" / "skills" / "research" / "pac" / "scripts"
ANALYZER = SCRIPTS_DIR / "pac-analyzer.py"
GENERATOR = SCRIPTS_DIR / "pac-question-generator.py"
CONFIG = SCRIPTS_DIR / "pac-config.json"
PREFS_FILE = SCRIPTS_DIR / ".." / "pac-prefs.json"

PAC_DIR = Path.home() / ".hermes" / "pac"
TRACES_FILE = PAC_DIR / "traces.jsonl"
PENDING_DIR = PAC_DIR / "challenges" / "pending"
HISTORY_DIR = PAC_DIR / "challenges" / "history"
RATE_STATE = PAC_DIR / "rate-state.json"

VALID_VOICES = ("socratic", "peer")


# ── Helpers ────────────────────────────────────────────────────────────────

def _read_traces(days: int = 30) -> list[dict]:
    """Read recent traces from the trace file."""
    if not TRACES_FILE.exists():
        return []
    from datetime import datetime, timezone
    cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
    traces = []
    with open(TRACES_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = rec.get("ts", 0)
            if isinstance(ts, (int, float)) and ts >= cutoff:
                traces.append(rec)
            elif isinstance(ts, str):
                try:
                    from datetime import datetime
                    t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    if t.timestamp() >= cutoff:
                        traces.append(rec)
                except (ValueError, TypeError):
                    traces.append(rec)
    return traces


def _read_memory() -> str:
    """Read user memory/profile for context."""
    profile_path = os.environ.get("PAC_PROFILE_PATH", "")
    paths = [
        Path(profile_path) if profile_path else Path.home() / "PROFILE.md",
    ]
    for p in paths:
        if p.exists():
            return p.read_text(encoding="utf-8")
    return ""


def _read_biography() -> str:
    """Read user biography."""
    profile_path = os.environ.get("PAC_PROFILE_PATH", "")
    p = Path(profile_path) if profile_path else Path.home() / "PROFILE.md"
    if p.exists():
        return p.read_text(encoding="utf-8")
    return ""


def _load_rate_state() -> dict:
    """Load rate-limiting state."""
    if RATE_STATE.exists():
        try:
            return json.loads(RATE_STATE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"challenge_timestamps": [], "pattern_last_ts": {}, "rejection_count": 0, "cooldown_until": None}


def _save_rate_state(state: dict) -> None:
    """Save rate-limiting state."""
    RATE_STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _normalise_ts(val) -> int:
    """Normalise timestamp to int."""
    if isinstance(val, (int, float)):
        return int(val)
    if isinstance(val, str):
        try:
            from datetime import datetime
            return int(datetime.fromisoformat(val.replace("Z", "+00:00")).timestamp())
        except (ValueError, TypeError):
            return 0
    return 0


def _check_rate_limit(pattern: str) -> tuple[bool, str]:
    """Check if a challenge is allowed. Returns (allowed, reason)."""
    state = _load_rate_state()
    from datetime import datetime, timezone
    now = int(datetime.now(timezone.utc).timestamp())

    cooldown = _normalise_ts(state.get("cooldown_until"))
    if cooldown and now < cooldown:
        return False, f"In cooldown for {(cooldown - now) // 86400} more days"

    timestamps = [_normalise_ts(t) for t in state.get("challenge_timestamps", [])]
    if timestamps:
        last_ts = timestamps[-1]
        if now - last_ts < 86400:
            return False, f"Last challenge was {(86400 - (now - last_ts)) // 60} minutes ago (min 24h)"

    week_ago = now - 7 * 86400
    week_count = sum(1 for t in timestamps if t >= week_ago)
    if week_count >= 3:
        return False, f"Already issued 3 challenges this week"

    pattern_key = f"pattern_{pattern}"
    last_pattern_ts = _normalise_ts(state.get("pattern_last_ts", {}).get(pattern_key))
    if last_pattern_ts and now - last_pattern_ts < 7 * 86400:
        return False, (f"Same pattern challenged "
                       f"{(7 * 86400 - (now - last_pattern_ts)) // 86400} days ago (min 7-day silence)")

    return True, ""


def _record_challenge(pattern: str) -> None:
    """Record a challenge for rate-limiting purposes."""
    state = _load_rate_state()
    from datetime import datetime, timezone
    now = int(datetime.now(timezone.utc).timestamp())
    timestamps = state.get("challenge_timestamps", [])
    timestamps.append(now)
    state["challenge_timestamps"] = timestamps[-14:]
    pattern_key = f"pattern_{pattern}"
    if "pattern_last_ts" not in state:
        state["pattern_last_ts"] = {}
    state["pattern_last_ts"][pattern_key] = now
    _save_rate_state(state)


def _get_voice() -> str:
    """Read voice preference from prefs file or env."""
    env_voice = os.environ.get("PAC_VOICE", "").lower()
    if env_voice in VALID_VOICES:
        return env_voice
    if PREFS_FILE.exists():
        try:
            prefs = json.loads(PREFS_FILE.read_text(encoding="utf-8"))
            return prefs.get("voice", "socratic")
        except (json.JSONDecodeError, OSError):
            pass
    return "socratic"


# ── Main ────────────────────────────────────────────────────────────────────

def main() -> None:
    PENDING_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)

    if not ANALYZER.exists() or not GENERATOR.exists():
        return  # scripts not installed — silent exit

    traces = _read_traces()
    memory = _read_memory()
    biography = _read_biography()

    # Build input for analyzer
    input_data = {
        "session_text": "",
        "traces": traces,
        "memory": memory,
        "biography": biography,
    }

    # Run analyzer
    try:
        result = subprocess.run(
            [sys.executable, str(ANALYZER)],
            input=json.dumps(input_data),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return  # no signals — silent exit
        signals = json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return  # transient failure — silent exit

    if not signals:
        return  # no actionable signals — silent exit

    top_pattern = signals[0]["pattern"]

    # Rate limit check
    allowed, reason = _check_rate_limit(top_pattern)
    if not allowed:
        return  # suppressed — silent exit

    # Determine voice
    voice = _get_voice()

    # Generate challenge with voice
    try:
        result = subprocess.run(
            [sys.executable, str(GENERATOR), "--voice", voice],
            input=json.dumps(signals),
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return  # generation failed — silent exit
        challenge_text = result.stdout.strip()
    except (subprocess.TimeoutExpired, OSError):
        return

    # Save to pending
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    cid = f"pac-{now.strftime('%Y%m%d')}-{now.strftime('%H%M%S')}"
    pending_file = PENDING_DIR / f"{cid}.xml"

    record = {
        "challenge_id": cid,
        "ts": now.isoformat(),
        "pattern": top_pattern,
        "voice": voice,
        "signals": signals,
        "challenge_text": challenge_text,
        "status": "pending",
    }
    pending_file.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    _record_challenge(top_pattern)

    # Output the challenge for delivery
    voice_label = f"  (voice: {voice})" if voice else ""
    print(f"🪞 **PAC Challenge — {top_pattern}**{voice_label}\n\n{challenge_text}\n\nChallenge ID: `{cid}`\nRespond with: `/pac resolve {cid} answered|dismissed|skip [your thoughts]`")


if __name__ == "__main__":
    main()
