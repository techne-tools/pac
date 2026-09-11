#!/usr/bin/env python3
"""
pac-analyzer.py — PAC Pattern Detection Engine (Hermes port)

Scans session traces, memory, and conversation context for 5 patterns
of self-limiting behaviour. Outputs signals classified as constraint_optimal
(don't challenge) or self_limiting (must challenge).

Usage:
    cat input.json | python3 pac-analyzer.py

Input JSON:
    {
        "session_text": "...",       # current conversation context
        "traces": [...],             # list of {ts, task, detail, ...}
        "memory": "...",             # MEMORY.md or equivalent
        "biography": "..."          # user profile/biography
    }

Output: JSON array of actionable signals (stdout)
    [{"pattern": "repetition", "confidence": 0.85, ...}]

Stdlib only. No external deps.
"""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any


# ============================================================================
# Configuration
# ============================================================================

# Config path — can be overridden via env
CONFIG_PATH = Path(
    os.environ.get("PAC_CONFIG_PATH",
                   str(Path(__file__).resolve().parent / "pac-config.json"))
)

MIN_CONFIDENCE = float(os.environ.get("PAC_CONFIDENCE_THRESHOLD", "0.70"))
LOOKBACK_DAYS = int(os.environ.get("PAC_LOOKBACK_DAYS", "30"))


class Pattern(str, Enum):
    REPETITION = "repetition"
    CLAIM_ACTION_CONFLICT = "claim_action_conflict"
    CAPABILITY_MISMATCH = "capability_mismatch"
    SUNK_COST = "sunk_cost"
    DECISION_NODE = "decision_node"


class Classification(str, Enum):
    SELF_LIMITING = "self_limiting"
    CONSTRAINT_OPTIMAL = "constraint_optimal"
    UNKNOWN = "unknown"


SELF_LIMITING_PATTERNS = {
    Pattern.REPETITION,
    Pattern.CLAIM_ACTION_CONFLICT,
    Pattern.CAPABILITY_MISMATCH,
    Pattern.SUNK_COST,
    Pattern.DECISION_NODE,
}


# ============================================================================
# Config Model
# ============================================================================

@dataclass(frozen=True)
class PacConfig:
    start_keywords: tuple[str, ...]
    delegate_regexes: tuple[re.Pattern, ...]
    strategic_keywords: tuple[str, ...]
    zero_growth_regexes: tuple[re.Pattern, ...]
    decision_patterns: tuple[tuple[re.Pattern, str], ...]
    business_lines: dict[str, tuple[str, ...]]
    survival_lines: frozenset[str]
    constraint_signals: tuple[str, ...]
    executor_roles: frozenset[str]
    claim_focus_marker: str
    claim_focus_patterns: tuple[re.Pattern, ...]


_DEFAULT_CONFIG = PacConfig(
    start_keywords=(
        "start new", "started new", "starting new", "new project",
        "new production", "new show", "new piece", "new work",
        "new research", "new paper", "new module", "new course",
        "new design", "new concept", "kickoff", "spinning up",
        "new idea", "new collaboration", "new sound design",
        "new installation", "new composition",
    ),
    delegate_regexes=(
        re.compile(r"(?:delegating|handing|giving) (?:to|over) (\w+)", re.I),
        re.compile(r"(\w+) (?:will handle|is handling|takes over)", re.I),
    ),
    strategic_keywords=(
        "strategy", "artistic direction", "concept", "aesthetic",
        "research question", "methodology", "curation", "programme",
        "grant", "proposal", "publication", "conference",
        "exhibition", "premiere", "commission",
    ),
    zero_growth_regexes=(
        re.compile(
            r"([A-Za-z0-9_\-]{1,30})\s*:?\s*(\d+)\s*days?\s*(?:of\s*)?"
            r"(?:zero|no|stalled|stuck)\s*(?:growth|progress|movement|development)",
            re.I,
        ),
    ),
    decision_patterns=(
        (re.compile(r"about to (?:sign|submit|commit|publish|premiere)", re.I), "commitment"),
        (re.compile(r"committing \$?(\d+)[kKmM]?", re.I), "financial_commit"),
        (re.compile(r"(?:shutting down|closing|cancelling) (\S+)", re.I), "closure"),
        (re.compile(r"(?:funding|grant|budget) (?:round|proposal|application)", re.I), "funding_decision"),
        (re.compile(r"about to (?:decide|choose|pick)", re.I), "decision_node"),
    ),
    business_lines={
        "Teaching": ("teaching", "module", "course", "curriculum", "syllabus", "lesson", "student"),
        "Research": ("research", "paper", "publication", "conference", "article", "chapter"),
        "Creative Practice": ("sound design", "production", "show", "performance", "composition", "installation"),
        "Administration": ("admin", "meeting", "committee", "report", "email", "paperwork"),
        "Technical Production": ("qlab", "touchdesigner", "rigging", "lighting", "audio", "tech"),
    },
    survival_lines=frozenset(["Teaching", "Administration"]),
    constraint_signals=(
        "no budget", "limited resources", "institutional constraint",
        "teaching load", "family obligations", "visa restrictions",
        "deadline pressure", "no runway", "compliance", "health",
    ),
    executor_roles=frozenset(["student", "technician", "assistant", "intern"]),
    claim_focus_marker="priority",
    claim_focus_patterns=(
        re.compile(r"my priority is ([^.,;]+)", re.I),
        re.compile(r"my priorities are ([^.,;]+)", re.I),
        re.compile(r"prioritising ([^.,;]+)", re.I),
        re.compile(r"prioritizing ([^.,;]+)", re.I),
        re.compile(r"focus(?:ed|ing)? on ([^.,;]+)", re.I),
        re.compile(r"what matters (?:to me )?is ([^.,;]+)", re.I),
        re.compile(r"the priority is ([^.,;]+)", re.I),
    ),
)


def _compile_regexes(patterns: list, flags: int = 0) -> tuple:
    compiled = []
    for p in patterns:
        try:
            compiled.append(re.compile(p, flags))
        except re.error:
            continue
    return tuple(compiled)


def load_config() -> PacConfig:
    """Load user-specific config; fall back to PaR defaults."""
    if not CONFIG_PATH.exists():
        return _DEFAULT_CONFIG
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _DEFAULT_CONFIG

    flags = re.IGNORECASE if raw.get("case_insensitive", True) else 0

    decision_compiled = []
    for item in raw.get("decision_patterns", []):
        if isinstance(item, dict) and "regex" in item and "type" in item:
            try:
                decision_compiled.append(
                    (re.compile(item["regex"], flags), str(item["type"]))
                )
            except re.error:
                continue

    business_lines_raw = raw.get("business_lines", {})
    business_lines = {
        str(k): tuple(str(x) for x in v)
        for k, v in business_lines_raw.items()
        if isinstance(v, (list, tuple))
    }

    return PacConfig(
        start_keywords=tuple(raw.get("start_keywords", _DEFAULT_CONFIG.start_keywords)),
        delegate_regexes=_compile_regexes(
            raw.get("delegate_regexes", []), flags
        ) or _DEFAULT_CONFIG.delegate_regexes,
        strategic_keywords=tuple(
            raw.get("strategic_keywords", _DEFAULT_CONFIG.strategic_keywords)
        ),
        zero_growth_regexes=_compile_regexes(
            raw.get("zero_growth_regexes", []), flags
        ) or _DEFAULT_CONFIG.zero_growth_regexes,
        decision_patterns=tuple(decision_compiled) or _DEFAULT_CONFIG.decision_patterns,
        business_lines=business_lines or _DEFAULT_CONFIG.business_lines,
        survival_lines=frozenset(raw.get("survival_lines", [])),
        constraint_signals=tuple(
            raw.get("constraint_signals", _DEFAULT_CONFIG.constraint_signals)
        ),
        executor_roles=frozenset(raw.get("executor_roles", [])),
        claim_focus_marker=str(
            raw.get("claim_focus_marker", _DEFAULT_CONFIG.claim_focus_marker)
        ),
        claim_focus_patterns=_compile_regexes(
            raw.get("claim_focus_patterns", []), flags
        ) or _DEFAULT_CONFIG.claim_focus_patterns,
    )


# ============================================================================
# Data Model
# ============================================================================

@dataclass
class Signal:
    pattern: Pattern
    confidence: float
    evidence: dict
    classification: Classification = Classification.UNKNOWN
    summary: str = ""

    def to_json(self) -> dict:
        d = asdict(self)
        d["pattern"] = self.pattern.value
        d["classification"] = self.classification.value
        return d


# ============================================================================
# Utilities
# ============================================================================

def _text_of(rec: dict) -> str:
    return f"{rec.get('task', '')} {rec.get('detail', '')}"


def _load_momentum():
    """Load project_momentum.py by path (hyphen-free module, sibling of this
    file — same pattern as _load_semantic). Returns the module or None so the
    analyzer stays functional if the sibling is absent."""
    try:
        import importlib.util
        path = Path(__file__).resolve().parent / "project_momentum.py"
        spec = importlib.util.spec_from_file_location("project_momentum", path)
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


def _resolve_project_dir(line: str):
    """Resolve a project name to a real path under ~/Development when possible.
    Exact match first (case-sensitive), then a bounded case-insensitive glob.
    Returns a string path or None. Never raises. Ground-truth momentum is only
    attached when the line actually resolves — unknown otherwise (fail-open)."""
    try:
        dev = Path.home() / "Development"
        if not dev.is_dir():
            return None
        exact = dev / line
        if exact.is_dir():
            return str(exact)
        # bounded case-insensitive match (first hit only)
        try:
            for cand in dev.iterdir():
                if cand.is_dir() and cand.name.lower() == line.lower():
                    return str(cand)
        except Exception:
            return None
        return None
    except Exception:
        return None


# ============================================================================
# Semantic Classification (local LLM, graceful fallback)
# ============================================================================

def _load_semantic():
    """Load pac-semantic.py by path (hyphenated filename can't be imported)."""
    try:
        import importlib.util
        path = Path(__file__).resolve().parent / "pac-semantic.py"
        spec = importlib.util.spec_from_file_location("pac_semantic", path)
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


def _classify_texts(texts: dict[str, str], config: PacConfig) -> dict[str, str]:
    """Classify {id: text} into business lines.

    Uses the local LLM when available; falls back to keyword matching.
    Never raises — always returns a mapping (possibly partial).
    """
    lines = list(config.business_lines.keys())
    result: dict[str, str] = {}

    sem = _load_semantic()
    if sem is not None and sem.ollama_available():
        try:
            semantic = sem.classify_batch(texts, lines)
            if semantic is not None:
                result.update(semantic)
        except Exception:
            pass

    # Keyword fallback for anything the model didn't classify
    for i, text in texts.items():
        if i in result:
            continue
        tl = text.lower()
        for line, kws in config.business_lines.items():
            if any(kw.lower() in tl for kw in kws):
                result[i] = line
                break
    return result


# ============================================================================
# Confidence Calculators
# ============================================================================

def _conf_repetition(event_count: int) -> float:
    return min(0.95, 0.50 + 0.08 * event_count)


def _conf_capability_mismatch(count: int) -> float:
    return min(0.90, 0.70 + 0.10 * count)


def _conf_sunk_cost(days: int, action_count: int) -> float:
    return min(0.95, 0.60 + 0.015 * days + 0.05 * action_count)


# ============================================================================
# Pattern Detectors
# ============================================================================

def detect_repetition_pattern(
    traces: list, session_text: str, config: PacConfig
) -> list[Signal]:
    # Primary: keyword-based detection (explicit "new project" etc.)
    new_events = [
        r for r in traces
        if any(kw.lower() in _text_of(r).lower() for kw in config.start_keywords)
    ]

    # Secondary: domain-based detection — many distinct tasks across domains
    # in the lookback period is also a repetition signal
    domain_counts: dict[str, int] = {}
    for r in traces:
        domain = r.get("domain", "unknown")
        domain_counts[domain] = domain_counts.get(domain, 0) + 1

    # If keyword-based found enough, use that
    if len(new_events) >= 5:
        return [Signal(
            pattern=Pattern.REPETITION,
            confidence=_conf_repetition(len(new_events)),
            evidence={
                "new_events_count": len(new_events),
                "lookback_days": LOOKBACK_DAYS,
                "sample_events": [
                    {"ts": e.get("ts"), "task": e.get("task", "")[:80]}
                    for e in new_events[:3]
                ],
            },
            summary=f"Started {len(new_events)} new items in the past {LOOKBACK_DAYS} days",
        )]

    # Fallback: if 10+ traces across 2+ domains, that's also repetition
    active_domains = {d for d, c in domain_counts.items() if c >= 3}
    total_traces = len(traces)
    if total_traces >= 10 and len(active_domains) >= 2:
        return [Signal(
            pattern=Pattern.REPETITION,
            confidence=_conf_repetition(total_traces),
            evidence={
                "new_events_count": total_traces,
                "lookback_days": LOOKBACK_DAYS,
                "domain_breakdown": domain_counts,
                "active_domains": list(active_domains),
                "sample_events": [
                    {"ts": r.get("ts"), "task": r.get("task", "")[:80]}
                    for r in traces[:3]
                ],
            },
            summary=f"{total_traces} activities across {len(active_domains)} domains in the past {LOOKBACK_DAYS} days",
        )]

    return []


def _extract_claim(memory: str, config: PacConfig) -> tuple[str, str] | None:
    """Extract a declared focus from memory.

    Returns (raw_claim, matched_line) when the claim can be mapped to a
    business line, or (raw_claim, "") when a claim exists but is too vague
    to map. Returns None when no focus claim is present at all.

    The claim is mapped to a line semantically (local LLM) when available,
    falling back to keyword matching.
    """
    if not memory:
        return None
    for pat in config.claim_focus_patterns:
        m = pat.search(memory)
        if not m:
            continue
        raw = m.group(1).strip()
        if not raw:
            continue
        # Semantic mapping of the claim to a line
        mapped = _classify_texts({"claim": raw}, config)
        line = mapped.get("claim", "")
        return raw, line
    return None


def detect_claim_action_conflict(
    traces: list, memory: str, config: PacConfig
) -> list[Signal]:
    if not config.business_lines or not config.survival_lines:
        return []
    if config.claim_focus_marker.lower() not in memory.lower():
        return []

    claim = _extract_claim(memory, config)
    if claim is None:
        return []
    raw_claim, declared_line = claim

    # No declared line (claim too vague to map) — no conflict to assert.
    # Bail before classifying traces to avoid a wasted semantic call.
    if not declared_line:
        return []

    # Classify effort traces semantically (local LLM) with keyword fallback.
    trace_texts = {str(i): _text_of(rec) for i, rec in enumerate(traces)}
    classified = _classify_texts(trace_texts, config)

    counts = {}
    for i, rec in enumerate(traces):
        line = classified.get(str(i))
        if line:
            counts[line] = counts.get(line, 0) + 1

    total = sum(counts.values()) or 1
    allocation = {k: v / total for k, v in counts.items()}
    dominant = None
    dominant_pct = 0.0
    if allocation:
        dominant = max(allocation, key=lambda k: allocation[k])
        dominant_pct = allocation[dominant]

    # Declared line IS the dominant line — words and actions align.
    if declared_line == dominant:
        return []

    # Declared line is a survival line — the claim is about keeping the
    # lights on, not about where creative energy should go.
    if declared_line in config.survival_lines:
        return []

    # Genuine divergence: declared a non-survival focus, but effort went
    # elsewhere. Only fire when the dominant line is meaningfully absorbing
    # attention (>= 35%) and is not itself the declared line.
    if dominant and dominant_pct >= 0.35:
        return [Signal(
            pattern=Pattern.CLAIM_ACTION_CONFLICT,
            confidence=0.75,
            evidence={
                "declared_focus": raw_claim,
                "declared_line": declared_line,
                "actual_allocation": {k: round(v, 3) for k, v in allocation.items()},
                "conflicting_lines": [(l, round(p, 3)) for l, p in allocation.items()
                                     if l != declared_line and p >= 0.35],
            },
            summary=(
                f"Declared focus on {declared_line} ({raw_claim!r}), but "
                f"{dominant} absorbs {dominant_pct:.0%}"
            ),
        )]
    return []


def detect_capability_mismatch(
    session_text: str, biography: str, config: PacConfig
) -> list[Signal]:
    if not config.executor_roles:
        return []

    mismatch_count = 0
    evidence_quotes = []

    for pattern in config.delegate_regexes:
        for match in pattern.finditer(session_text):
            if not match.groups():
                continue
            person = match.group(1)
            if person not in config.executor_roles:
                continue
            context = session_text[
                max(0, match.start() - 50) : min(len(session_text), match.end() + 100)
            ]
            if any(kw.lower() in context.lower() for kw in config.strategic_keywords):
                mismatch_count += 1
                evidence_quotes.append({"person": person, "context": context.strip()})

    if mismatch_count < 1:
        return []

    return [Signal(
        pattern=Pattern.CAPABILITY_MISMATCH,
        confidence=_conf_capability_mismatch(mismatch_count),
        evidence={"mismatch_count": mismatch_count, "quotes": evidence_quotes[:3]},
        summary=(
            f"Strategic task delegated to {evidence_quotes[0]['person']} "
            f"(configured as executor-layer, not strategic)"
        ),
    )]


def detect_sunk_cost(
    traces: list, memory: str, config: PacConfig
) -> list[Signal]:
    signals = []
    seen = set()

    for pattern in config.zero_growth_regexes:
        for match in pattern.finditer(memory):
            groups = match.groups()
            if len(groups) < 2:
                continue
            line = str(groups[0]).strip()
            try:
                days = int(groups[1])
            except (TypeError, ValueError):
                continue
            if days < 14 or (line, days) in seen:
                continue
            seen.add((line, days))

            active = [
                r for r in traces
                if line.lower() in _text_of(r).lower()
            ]
            if not active:
                continue

            # Ground-truth momentum (Anansi-style read-time velocity, adapted for
            # PAC): attach days-idle from the repo/git reflog when the project
            # line resolves to a real path. Fail-open — unknown when unresolvable,
            # so the original statement-based days remain the fallback evidence.
            momentum = None
            project_dir = _resolve_project_dir(line)
            if project_dir:
                m = _load_momentum()
                if m is not None:
                    try:
                        momentum = m.project_momentum(project_dir)
                    except Exception:
                        momentum = None

            evidence = {
                "line": line,
                "zero_growth_days": days,
                "recent_action_count": len(active),
                "sample_actions": [a.get("task", "")[:80] for a in active[:3]],
            }
            if momentum is not None:
                evidence["momentum"] = momentum
                if momentum.get("stalled_days") is not None:
                    evidence["ground_truth_stalled_days"] = momentum["stalled_days"]

            signals.append(Signal(
                pattern=Pattern.SUNK_COST,
                confidence=_conf_sunk_cost(days, len(active)),
                evidence=evidence,
                summary=(
                    f"{line} at {days} days zero growth but still receiving "
                    f"{len(active)} recent tactical actions"
                    + (
                        f" (repo ground truth: {momentum['momentum']} "
                        f"{momentum['stalled_days']} days idle)"
                        if momentum and momentum.get("stalled_days") is not None
                        else ""
                    )
                ),
            ))
    return signals


def detect_decision_node(session_text: str, config: PacConfig) -> list[Signal]:
    signals = []
    for pattern, decision_type in config.decision_patterns:
        for match in pattern.finditer(session_text):
            context = session_text[
                max(0, match.start() - 100) : min(len(session_text), match.end() + 150)
            ]
            signals.append(Signal(
                pattern=Pattern.DECISION_NODE,
                confidence=0.80,
                evidence={
                    "decision_type": decision_type,
                    "matched_text": match.group(0),
                    "context": context.strip(),
                },
                summary=f"Major decision node: {match.group(0)}",
            ))
    return signals


DETECTORS: list = [
    lambda traces, session, memory, bio, cfg: detect_repetition_pattern(traces, session, cfg),
    lambda traces, session, memory, bio, cfg: detect_claim_action_conflict(traces, memory, cfg),
    lambda traces, session, memory, bio, cfg: detect_capability_mismatch(session, bio, cfg),
    lambda traces, session, memory, bio, cfg: detect_sunk_cost(traces, memory, cfg),
    lambda traces, session, memory, bio, cfg: detect_decision_node(session, cfg),
]


# ============================================================================
# Classification
# ============================================================================

def classify_signal(signal: Signal, memory: str, config: PacConfig) -> Classification:
    """Classify as self_limiting (challenge) or constraint_optimal (skip).

    Constraint signals must appear WITHIN the current signal's evidence context,
    not anywhere in memory. Secondary proximity check: constraint keyword near
    the signal's subject in memory.
    """
    evidence_str = str(signal.evidence).lower()

    # Primary: constraint keyword is cited IN this signal's evidence
    for kw in config.constraint_signals:
        if kw.lower() in evidence_str:
            return Classification.CONSTRAINT_OPTIMAL

    # Secondary: constraint keyword appears near the signal's subject in memory
    subject = ""
    for field in ("line", "business", "person"):
        v = signal.evidence.get(field) if isinstance(signal.evidence, dict) else None
        if v:
            subject = str(v).lower()
            break

    if subject and memory:
        memory_lower = memory.lower()
        for kw in config.constraint_signals:
            kw_lower = kw.lower()
            start = 0
            while True:
                idx = memory_lower.find(kw_lower, start)
                if idx == -1:
                    break
                window = memory_lower[max(0, idx - 120): idx + 120 + len(kw_lower)]
                if subject in window:
                    return Classification.CONSTRAINT_OPTIMAL
                start = idx + 1

    if signal.pattern in SELF_LIMITING_PATTERNS:
        return Classification.SELF_LIMITING
    return Classification.SELF_LIMITING


# ============================================================================
# Main
# ============================================================================

def analyze(input_data: dict) -> list[dict]:
    """Run PAC analysis on input data. Returns list of actionable signal dicts."""
    config = load_config()
    traces = input_data.get("traces", [])
    memory = input_data.get("memory", "")
    biography = input_data.get("biography", "")
    session_text = input_data.get("session_text", "")

    all_signals = []
    for detector in DETECTORS:
        all_signals.extend(detector(traces, session_text, memory, biography, config))

    for s in all_signals:
        s.classification = classify_signal(s, memory, config)

    actionable = sorted(
        (s for s in all_signals
         if s.classification == Classification.SELF_LIMITING
         and s.confidence >= MIN_CONFIDENCE),
        key=lambda s: s.confidence,
        reverse=True,
    )
    return [s.to_json() for s in actionable]


def main() -> None:
    try:
        raw = sys.stdin.read().strip()
        input_data = json.loads(raw) if raw else {}
    except (json.JSONDecodeError, OSError):
        input_data = {}

    results = analyze(input_data)
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
