---
name: PAC — Proactive Accountability Challenge
version: 2.2.0
category: research
description: A mirror for self-limiting patterns in PaR practice — dual voice (socratic + peer-learning), detects repetition, claim-action conflict, capability mismatch, sunk cost, and decision nodes across sessions
tags: [pac, accountability, self-reflection, practice-as-research, socratic, peer-learning]
---

# PAC — Proactive Accountability Challenge

> *"PAC is not a judge. It's a mirror."*

A port of Enso OS's PAC mechanism, adapted for Hermes and Chris's PaR practice at UAS. Detects 5 patterns of self-limiting behaviour across sessions and delivers challenges in two voices — either proactively in-chat or via weekly cron.

## Two Voices

| Voice | Frame | Register | When |
|---|---|---|---|
| **Socratic** (default) | Mirror — reflects what it sees | Interrogative, 5 quality rules | When you want the sharp edge |
| **Peer** | Sit beside — collaborative noticing | Rehearsal-room, design-thinking, offers over probes | When you want a colleague in the room with you |

Switch with `/pac voice socratic` or `/pac voice peer`. Preference persists across sessions.

## When to Activate

Activate this skill when:
- The user runs `/pac check` or `/pac status`
- The agent detects a pattern during conversation (repetition, claim-action conflict, etc.) and calls `pac_analyze`
- The weekly PAC cron job fires

## Architecture

```
~/.hermes/pac/
├── traces.jsonl             # Event log (agent records significant events)
├── challenges/
│   ├── pending/             # Unanswered challenges (JSON files)
│   └── history/             # Answered/dismissed challenges
└── rate-state.json          # Rate limiting state

Plugin: ~/.hermes/plugins/pac/__init__.py  (v1.1.0)
  - /pac check     — analyze current conversation context
  - /pac status    — show pending + recent challenges
  - /pac voice     — show or set voice preference
  - /pac learn     — add keywords to config at runtime
  - pac_analyze()  — tool for agent to call proactively
  - pac_log_event() — tool to record significant events

Scripts: ~/.hermes/skills/research/pac/scripts/
  - pac-analyzer.py              — 5-pattern detection engine
  - pac-semantic.py              — Local LLM semantic classification (ollama, graceful fallback)
  - pac-question-generator.py    — Dual-voice challenge generator
  - pac-config.json              — PaR domain configuration
  - pac-prefs.json               — Voice preference (persistent)
```

## The Five Patterns

| # | Pattern | What it detects |
|---|---------|-----------------|
| 1 | **Repetition** | Starting new projects/ideas while old ones are incomplete |
| 2 | **Claim-Action Conflict** | Declared focus (mapped to a business line) ≠ dominant actual time allocation |
| 3 | **Capability Mismatch** | Strategic work delegated to wrong level |
| 4 | **Sunk Cost** | Long-running zero-growth with tactical churn |
| 5 | **Decision Node** | Irreversible action about to ship |

## Constraint vs Self-Limitation

- 🟢 **Constraint-optimal** (DO NOT challenge) — real-world limits (no runway, family, health, institutional constraints)
- 🔴 **Self-limiting** (MUST challenge) — internal patterns the user can change

When uncertain, PAC biases toward **not** challenging.

## Claim-Action Conflict Semantics (v2.2)

The detector is **semantic, not keyword-based**. It only fires when:

1. A focus claim is **extractable** from memory via `claim_focus_patterns`
   (e.g. "my priority is X", "focusing on X", "what matters is X").
2. The claim **maps to a business line** (Research, Creative Practice, etc.).
3. The declared line is **not** the dominant line in recent traces.
4. The declared line is **not** a survival line (Teaching/Administration).
5. The dominant line absorbs **≥ 35%** of recent effort.

It does **not** fire on the bare word "priority" alone, on unmappable claims
("my priority is getting things done"), or when words and actions align.
The challenge text names the actual claim (e.g. "the sound design for the
new show"), not a placeholder.

### Semantic classification (local LLM)

Both the claim AND the effort traces are classified into business lines by a
**local ollama model** (`qwen3.5:9b-mlx` by default) — not keyword matching.
This handles nuance the keyword matcher gets wrong: "production management
for the festival" → Administration (not Creative Practice), "rehearsal for
the new show" → Creative Practice.

- All texts are batched into **one** model call (not one per trace) for speed.
- **Graceful degradation**: if ollama is unreachable or the call fails, the
  detector falls back to keyword matching — it never crashes, never hangs.
- The model is configured in `scripts/pac-semantic.py` (env overrides:
  `PAC_OLLAMA_URL`, `PAC_LLM_MODEL`, `PAC_LLM_TIMEOUT`).

Tune the semantics in `scripts/pac-config.json`:
- `claim_focus_patterns` — regexes that extract the claim (group 1 = the focus)
- `business_lines` — keyword sets per line (used as the FALLBACK classifier)
- `survival_lines` — lines whose claims are never challenged
- The 35% threshold is hardcoded in `detect_claim_action_conflict`

> **Note on timeouts**: the semantic call takes ~30-40s. The cron script
> (`pac-cron.py`) and plugin (`/pac check`) both call the analyzer with a
> `timeout=120` to accommodate this. If you change the model or add latency,
> keep these timeouts in sync.

## Quality Standards

Socratic voice follows all 5:
1. Based on observation, not generic wisdom
2. Points to structure, not instance
3. Challenges premise, not options
4. Has a time dimension
5. No answer given

Peer voice replaces #5 with an open door — the user is invited, not left alone with a question.

## Anti-Fatigue

- Max 1 challenge per 24 hours
- Max 3 per week
- 7-day silence on same pattern
- 1-month cooldown after 3 consecutive rejections

## Commands

| Command | Description |
|---|---|
| `/pac check` | Analyze current conversation context |
| `/pac check --voice peer` | Analyze with a specific voice (overrides preference) |
| `/pac status` | Show pending + recent challenges |
| `/pac history [N]` | Show last N challenges |
| `/pac resolve <id> answered\|dismissed\|skip [response]` | Respond to a challenge |
| `/pac voice [socratic\|peer]` | Show or set voice preference |
| `/pac learn <keyword>` | Add a keyword to config (start_keywords) |
| `/pac learn <target> <keyword>` | Add keyword to a specific config list |
| `/pac learn list` | Show available config targets |
| `/pac log <type> <task>` | Log an event to the trace file |

## `/pac learn` — Learner-Driven Config

PAC's detection is keyword/regex-based. You can teach it new patterns at runtime:

- `/pac learn "embarking on a fresh investigation"` — adds to `start_keywords`
- `/pac learn constraint "visa processing"` — adds to `constraint_signals`
- `/pac learn strategic "artistic methodology"` — adds to `strategic_keywords`

This makes PAC genuinely *learner-driven*: you define what matters to your practice.

## Workflow

### In-Chat (agent-initiated)

1. Agent detects a pattern during conversation
2. Agent calls `pac_analyze(context)` with the relevant evidence
3. Analyzer classifies as self-limiting or constraint-optimal
4. If self-limiting, question generator produces a challenge in the current voice
5. Challenge is delivered in-chat and saved to pending/

### Cron (weekly deep scan)

1. Cron job runs `pac-analyzer.py` against accumulated traces
2. Top signal is passed to question generator (uses configured voice)
3. Challenge is delivered via Telegram/Discord

## The Peer Voice Register

The peer voice draws on language from:
- **Theatre/dramaturgy:** gesture, scene, threshold, map, frame, blocking run, shape
- **Design/technical:** threshold test, release condition, trace, conditions, structural risk
- **Phenomenological/being-in:** sitting, holding, open threads, landing, stepping in
- **Collaborative/offering:** "want to...?", "I can help...", "let me know when..."

These registers are held in tension — no single one dominates. The voice moves through them depending on what the moment needs.

## Linked Files

- `references/enso-os-provenance.md` — Full provenance, adaptation decisions, and what was left behind from the Enso OS port
- `scripts/pac-analyzer.py` — 5-pattern detection engine (stdlib only)
- `scripts/pac-question-generator.py` — Dual-voice challenge generator (template-based, no LLM)
- `scripts/pac-config.json` — PaR domain configuration
- `../pac-prefs.json` — Voice preference (persistent)

## Integration

This skill works well with:
- `noema` — for persistent memory of challenges and responses
- `obsidian` — for storing challenge history in the vault
- `cronjob` — for weekly deep scans
