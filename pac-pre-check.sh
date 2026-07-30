#!/bin/bash
# PAC pre-tool-check hook
set -euo pipefail
INPUT=$(cat)
TOOL_NAME=$(echo "$INPUT" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('tool_name',''))" 2>/dev/null || echo "")
NOISY_TOOLS="read_file|list_directory|ls|search_files|browser_snapshot"
if echo "$TOOL_NAME" | grep -qE "^($NOISY_TOOLS)$"; then exit 0; fi
PAC_STATE=$(cat ~/.hermes/pac/rate-state.json 2>/dev/null || echo '{}')
COOLDOWN_UNTIL=$(echo "$PAC_STATE" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('cooldown_until') or '')" 2>/dev/null || echo "")
if [ -n "$COOLDOWN_UNTIL" ]; then
  NOW=$(date +%s)
  if [ "$NOW" -lt "$COOLDOWN_UNTIL" ]; then
    python3 -c "import json; print(json.dumps({'action': 'block', 'message': 'PAC cooldown active. Tool blocked.'}))"
    exit 0
  fi
fi
REJECTIONS=$(echo "$PAC_STATE" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('rejection_count', 0))" 2>/dev/null || echo "0")
if [ "$REJECTIONS" -gt 5 ]; then
  python3 -c "import json; print(json.dumps({'action': 'block', 'message': 'Too many PAC rejections. Session needs reset.'}))"
  exit 0
fi
exit 0
