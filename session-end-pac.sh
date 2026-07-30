#!/bin/bash
# Session-end hook — triggers PAC analysis
set -euo pipefail
INPUT=$(cat)
LOG_DIR="$HOME/.hermes/logs"
mkdir -p "$LOG_DIR"
SESSION_ID=$(echo "$INPUT" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('session_id','unknown'))" 2>/dev/null || echo "unknown")
COMPLETED=$(echo "$INPUT" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('completed', False))" 2>/dev/null || echo "false")
PAC_RESULT=""
if command -v pac_analyze &>/dev/null; then
  PAC_RESULT=$(pac_analyze 2>/dev/null | head -5 || echo "PAC analysis failed")
fi
python3 -c "
import json, os
from datetime import datetime, timezone
record = {
    'ts': datetime.now(timezone.utc).isoformat(),
    'session_id': '$SESSION_ID',
    'completed': $COMPLETED,
    'pac_result': '''$PAC_RESULT''',
}
log_dir = os.path.expanduser('$LOG_DIR')
with open(os.path.join(log_dir, 'pac-session.log'), 'a') as f:
    f.write(json.dumps(record) + '\n')
" 2>/dev/null || true
exit 0
