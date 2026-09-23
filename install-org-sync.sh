#!/usr/bin/env bash
# install-org-sync.sh — install ccd-org-sync + its single launchd agent (Max <-> Team org pair).
# Installs NOTHING else from this repo (no claude-archive-sync, no claude-second).
#   ./install-org-sync.sh "MAX=<acct>/<org>,TEAM=<acct>/<org>"   install / refresh
#   ./install-org-sync.sh --uninstall                             remove agent + script (keeps backups)
set -euo pipefail
REPO_SRC="$(cd "$(dirname "$0")" && pwd)"
BIN="${CCD_BIN:-$HOME/bin}"
LA="$HOME/Library/LaunchAgents"
LABEL=com.ccd-session-sync.org-sync
BACKUPS="$HOME/.claude/ccd-session-sync-backups"
CCS="$HOME/Library/Application Support/Claude/claude-code-sessions"

if [ "${1:-}" = "--uninstall" ]; then
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  rm -f "$LA/$LABEL.plist" "$BIN/ccd-org-sync"
  echo "removed agent + script; kept $BACKUPS (snapshots for rollback)"; exit 0
fi
PAIR="${1:?usage: $0 \"MAX=<acct>/<org>,TEAM=<acct>/<org>\"}"
A="${PAIR#*=}"; A="${A%%,*}"; B="${PAIR##*=}"
for d in "$A" "$B"; do [ -d "$CCS/$d" ] || { echo "ERROR: $CCS/$d not found" >&2; exit 1; }; done

mkdir -p "$BIN" "$LA" "$BACKUPS"
cp "$REPO_SRC/bin/ccd-org-sync" "$BIN/ccd-org-sync"; chmod +x "$BIN/ccd-org-sync"
CCD_PAIR="$PAIR" /usr/bin/python3 "$BIN/ccd-org-sync" sync --dry-run >/dev/null   # fail closed before loading the agent
sed -e "s#__BIN__#$BIN#g" -e "s#__PAIR__#$PAIR#g" -e "s#__ORG_A__#$CCS/$A#g" \
    -e "s#__ORG_B__#$CCS/$B#g" -e "s#__BACKUPS__#$BACKUPS#g" \
    "$REPO_SRC/launchagents/$LABEL.plist.template" > "$LA/$LABEL.plist"
plutil -lint "$LA/$LABEL.plist" >/dev/null
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null && sleep 2 || true
launchctl bootstrap "gui/$(id -u)" "$LA/$LABEL.plist"
echo "installed: $BIN/ccd-org-sync + $LABEL (log: $BACKUPS/sync.log)"
