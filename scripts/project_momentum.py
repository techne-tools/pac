#!/usr/bin/env python3
"""project_momentum.py — read-time project velocity from ground truth.

Determines whether a project/repo is stalled, moving, or unknown USING ONLY
stdlib file reads — no subprocess, no git library (no-shell-out rule).

Technique adapted from the hermes-anansi-plugin (bionicbutterfly13, no
licence, 2026): appraisal-read-time momentum derived from the git reflog
tail + os.stat().st_mtime. Reimplemented fresh here, path-oriented for
PAC's use ("is this project/claim/goal actually moving?"), with two
deliberate differences from the source:
  - a resolvable FILE mtime outranks the repo reflog (the file is the
    most specific signal for that path);
  - a DIRECTORY's st_mtime is treated as weaker than the repo reflog,
    because on macOS directory mtimes do not change when file CONTENTS
    are edited, only on add/remove/rename.

Ground-truth priority:
  1. existing FILE at path          -> its st_mtime
  2. path inside a git repo         -> last git activity epoch
                                     (last line of .git/logs/HEAD)
  3. existing DIRECTORY not in repo -> its st_mtime
  4. updated_at / created_at        -> ISO timestamp fallback
Any failure -> benign "unknown". NEVER raises.

Usage:
    python3 project_momentum.py --check /path/to/project
    python3 project_momentum.py --check /path/to/project --json
    python3 project_momentum.py --check /path --stalled-threshold 5
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# Idle >= this many days reads "stalled" (mirrors anansi's threshold; a
# project touched within two days is "moving").
DEFAULT_STALLED_THRESHOLD_DAYS = 2.0

# Neutral salience: stalled is LOUDER than moving than unknown — ordering
# only, never imperative language.
MOMENTUM_SALIENCE = {
    "stalled": 1.0,
    "moving": 0.3,
    "unknown": 0.0,
}

_UNKNOWN: dict[str, Any] = {
    "momentum": "unknown",
    "stalled_days": None,
    "salience": 0.0,
    "source": "none",
}


# ---------------------------------------------------------------------------
# Repo discovery (walk up, stdlib only)
# ---------------------------------------------------------------------------


def repo_root(start: Optional[str] = None) -> Optional[Path]:
    """Walk up from ``start`` (default: cwd) to the nearest directory
    containing a ``.git`` directory. Returns the repo root Path or None.
    Never raises."""
    try:
        current = Path(start) if start else Path(os.getcwd())
        current = current.resolve()
        for candidate in (current, *current.parents):
            if (candidate / ".git").is_dir():
                return candidate
        return None
    except Exception:
        return None


def _parse_reflog_epoch(repo_root: Path) -> Optional[float]:
    """Read the last git activity epoch from `.git/logs/HEAD` using ONLY
    read-mode open(). The last non-empty line is the most recent reflog
    entry (commit, merge, checkout, amend...); its committer epoch is the
    integer field immediately before the tz offset (the token before
    whitespace preceding the tab-separated tail). None on any problem."""
    try:
        reflog = repo_root / ".git" / "logs" / "HEAD"
        if not reflog.is_file():
            return None
        with open(reflog, "r", encoding="utf-8") as fh:
            lines = [ln for ln in fh.read().splitlines() if ln.strip()]
        if not lines:
            return None
        head_part = lines[-1].split("\t", 1)[0]
        fields = head_part.split()
        if len(fields) < 2:
            return None
        # fields[-1] is the tz offset (e.g. +0400); fields[-2] is the epoch.
        return float(int(fields[-2]))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Days-idle math
# ---------------------------------------------------------------------------


def _days_idle_from_epoch(epoch: float, now: datetime) -> Optional[float]:
    try:
        ts = datetime.fromtimestamp(float(epoch), tz=timezone.utc)
        return max(0.0, (now - ts).total_seconds() / 86400.0)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _days_idle_from_iso(ts_text: Any, now: datetime) -> Optional[float]:
    try:
        ts = datetime.fromisoformat(str(ts_text))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return max(0.0, (now - ts).total_seconds() / 86400.0)
    except (TypeError, ValueError):
        return None


def _classify(days_idle: float, threshold: float) -> dict[str, Any]:
    if days_idle >= threshold:
        return {
            "momentum": "stalled",
            "stalled_days": int(days_idle),
            "salience": MOMENTUM_SALIENCE["stalled"],
        }
    return {
        "momentum": "moving",
        "stalled_days": int(days_idle),
        "salience": MOMENTUM_SALIENCE["moving"],
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def project_momentum(
    path: str,
    updated_at: Optional[str] = None,
    now: Optional[datetime] = None,
    stalled_threshold_days: float = DEFAULT_STALLED_THRESHOLD_DAYS,
) -> dict[str, Any]:
    """Read-time momentum for a project path. Never raises.

    Returns {"momentum": "stalled"|"moving"|"unknown",
             "stalled_days": int|None, "salience": float,
             "source": "file_mtime"|"git_reflog"|"path_mtime"
                       |"updated_at"|"none"}.
    """
    try:
        if now is None:
            now = datetime.now(timezone.utc)
        target = Path(path).expanduser()
        # 1. Existing FILE -> its own mtime (most specific).
        if target.is_file():
            days = _days_idle_from_epoch(os.stat(target).st_mtime, now)
            if days is not None:
                result = _classify(days, stalled_threshold_days)
                result["source"] = "file_mtime"
                return result
        # 2. Inside a git repo -> last git activity epoch.
        root = repo_root(str(target.resolve()) if target.exists() else str(target))
        if root is not None:
            epoch = _parse_reflog_epoch(root)
            days = _days_idle_from_epoch(epoch, now) if epoch else None
            if days is not None:
                result = _classify(days, stalled_threshold_days)
                result["source"] = "git_reflog"
                return result
        # 3. Existing DIRECTORY (not in a repo) -> its st_mtime.
        if target.is_dir():
            days = _days_idle_from_epoch(os.stat(target).st_mtime, now)
            if days is not None:
                result = _classify(days, stalled_threshold_days)
                result["source"] = "path_mtime"
                return result
        # 4. ISO timestamp fallback.
        if updated_at:
            days = _days_idle_from_iso(updated_at, now)
            if days is not None:
                result = _classify(days, stalled_threshold_days)
                result["source"] = "updated_at"
                return result
        return dict(_UNKNOWN)
    except Exception:
        return dict(_UNKNOWN)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-time project momentum from ground truth (stdlib only)."
    )
    parser.add_argument("--check", required=True, help="path to the project/repo")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument(
        "--stalled-threshold",
        type=float,
        default=DEFAULT_STALLED_THRESHOLD_DAYS,
        help="days idle for 'stalled' (default 2.0)",
    )
    args = parser.parse_args(argv)

    result = project_momentum(
        args.check,
        stalled_threshold_days=args.stalled_threshold,
    )
    if args.json:
        print(json.dumps({"path": args.check, **result}))
    else:
        src = result["source"]
        if result["momentum"] == "unknown":
            print(f"{args.check}: unknown (no ground truth; source={src})")
        else:
            print(
                f"{args.check}: {result['momentum']} "
                f"({result['stalled_days']} days idle, source={src})"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
