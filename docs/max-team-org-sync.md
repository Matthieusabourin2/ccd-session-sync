# ccd-org-sync — runbook (Personal/Max ↔ Team, same account)

## Observed facts it relies on (Claude Desktop 2.7032, Claude Code 2.1.278, macOS 26.7)

| Fact | How it was established |
|---|---|
| Sidebar index is per org: `claude-code-sessions/<account>/<org>/local_<uuid>.json` | directory audit; `list_sessions` = exactly the active org's entries |
| Transcripts are org-agnostic: `~/.claude/projects/<cwd>/<cliSessionId>.jsonl` | every live entry resolves there; same file continued under both orgs |
| Sidebar folders have no separate index (derived from entry `cwd`) | no project path in Local Storage / IndexedDB / Session Storage; no custom groups |
| The index is re-read on every native switch — no restart needed | user-confirmed, 4 switches |
| Desktop only writes the **active** org's dir | read-only watcher: 0 writes to the inactive org for minutes after each switch |
| Activation rewrites ~11 entries with identical content (mtime only) | per-field diff watcher (`fields=[]`) |
| Opening a session regenerates org-bound fields (`remoteMcpServersConfig`…) | per-field diff watcher |
| `prs` is refreshed in the background without bumping timestamps | first real conflict, field-level diff |
| `ownerAccountId` / `spaces-present` do **not** change on an org switch | before/after diff → upstream `ccd-migrate-auto` never fires here |

## Merge rules

Per `sessionId` present in either org:

1. tombstone `deleted_<uuid>` in either org → skip (never resurrect; deletions not propagated in v1)
2. only in one org → create in the other, **if** its transcript exists and it is not a `scheduledTaskId` run
3. identical bytes or identical JSON → nothing
4. version `(lastActivityAt, lastFocusedAt|0)` differs → newer overwrites older
5. equal version, only `BACKGROUND_FIELDS` (`prs`) differ → newer file mtime wins
6. anything else → **ABORT** the whole run, write `BLOCKED`, one notification

Never touched: `~/.claude/projects`, `scheduled-tasks.json`, `backlog/`, `archived-sessions.idx`,
`local-agent-mode-sessions` (Cowork), `config.json` content (OAuth cache — only its mtime is used),
Keychain, cookies.

## Safety mechanics

- **Pair pinned** in the launchd plist (`CCD_PAIR`); refuses if the logged-in `ownerAccountId` ≠ paired account.
- **Schema guard**: every `local_*.json` must parse, have `sessionId == filename`, int `lastActivityAt`, str `cwd` — else no writes.
- **Quiet gate**: waits until nothing in either org dir nor `config.json` / `mcp-user-tool-toggles.json` / `plan-usage-history.json` changed for 15 s (max 120 s wait, then retries on next trigger).
- **Atomic + compare-and-swap**: hidden temp `.ccdsync-*.tmp` → fsync → sha check → re-stat destination (mtime_ns, size) → `os.replace`.
- **Snapshot per writing run**: `~/.claude/ccd-session-sync-backups/<ts>/manifest.json` (timestamp, Desktop version, source/destination space, path, sha256 before/after) + byte copies of overwritten files. Last 50 kept.
- **Logs** (`sync.log`): counts, snapshot id, result. No titles, no content.

## Commands

```bash
ccd-org-sync status                       # counts per org, blocked?, last log lines
ccd-org-sync sync --dry-run --verbose     # what would change; NO FILES MODIFIED
ccd-org-sync rollback <ts>                # restore a snapshot (verifies SHA-256), then blocks the agent
ccd-org-sync unblock                      # resume after a conflict or rollback
```
(`CCD_PAIR` must be set for `status`/`sync`; the agent has it in its plist.)

### When you get a "ccd-org-sync: BLOCKED" notification
1. `ccd-org-sync sync --dry-run --verbose` → note the conflicting session id.
2. Compare the two `local_<id>.json` field **names** that differ (never paste content anywhere).
3. If it is a new background-refresh field, add it to `BACKGROUND_FIELDS` with a test; otherwise decide manually.
4. `ccd-org-sync unblock`.

## Known limits
- If you switch org less than ~15 s after the last activity, the newest metadata (title, turn count, a brand-new session) lands after the switch and shows on the next switch. Transcript content is unaffected: it is read directly from the shared `.jsonl`.
- Internal, undocumented format: a Desktop update that changes the schema makes the tool refuse to write (fail closed) — check `sync.log`.
- The initial bulk migration was run with Desktop fully quit; steady-state runs write while Desktop runs, mostly into the inactive org.

## Uninstall
```bash
./install-org-sync.sh --uninstall     # removes agent + ~/bin/ccd-org-sync, keeps snapshots
```
Entries already merged stay; roll them back with `ccd-org-sync rollback <ts>` (bulk migration snapshot) if wanted.
