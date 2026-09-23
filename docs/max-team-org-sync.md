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
| A rename sets `titleSource=user` and pushes the old title to `previousTitles`, without bumping `lastActivityAt` | live rename test (the rename was first lost, then fixed) |
| `ownerAccountId` / `spaces-present` do **not** change on an org switch | before/after diff → upstream `ccd-migrate-auto` never fires here |

## Merge rules

Per `sessionId` present in either org:

1. tombstone `deleted_<uuid>` in either org → skip (never resurrect; deletions not propagated in v1)
2. only in one org → create in the other, **if** its transcript exists and it is not a `scheduledTaskId` run
3. identical bytes or identical JSON → nothing
4. the **title group** (`title`, `titleSource`, `titleTurn`, `previousTitles`) is merged on its own, because a rename does not bump `lastActivityAt`: the side whose `previousTitles` contains the other side's title wins (if both do — a rename was undone — the history that strictly extends the other wins, else ABORT); else a chosen title (`user`/`tool`) beats an automatic one; two different chosen titles without history → ABORT
5. rest of the entry: version `(lastActivityAt, lastFocusedAt|0)` differs → newer wins; the merged entry is written to every side that differs from it
6. equal version, only `BACKGROUND_FIELDS` (`prs`, `prState`, `prNumber`, `prUrl`, `prRepository`) differ → newer file mtime wins
7. anything else (incl. two different automatic titles at equal version) → **ABORT** the whole run, write `BLOCKED`, one notification

Never touched: `~/.claude/projects`, `scheduled-tasks.json`, `backlog/`, `archived-sessions.idx`,
`local-agent-mode-sessions` (Cowork), `config.json` content (OAuth cache — only its mtime is used),
Keychain, cookies.

## Safety mechanics

- **Pair pinned** in the launchd plist (`CCD_PAIR`); refuses if the logged-in `ownerAccountId` ≠ paired account.
- **Schema guard**: every `local_*.json` must parse, have `sessionId == filename`, int `lastActivityAt`, str `cwd` — else no writes.
- **Quiet gate**: waits until nothing in either org dir nor `config.json` / `mcp-user-tool-toggles.json` / `plan-usage-history.json` changed for 15 s (max 120 s wait, then retries on next trigger).
- **Atomic + compare-and-swap**: hidden temp `.ccdsync-*.tmp` → fsync → sha check → re-stat destination (mtime_ns, size) → `os.replace`.
- **Mass-write guard**: a real run aborts if either org has 0 entries (fill a brand-new org with `--only <sessionId>`), or would create more than 25 entries (`--allow-bulk` overrides this one after reviewing a dry-run).
- **Lock**: sync and rollback share an `flock`; `BLOCKED` is re-checked after the quiet wait, and rollback sets it *before* restoring.
- **Snapshot per writing run**: `~/.claude/ccd-session-sync-backups/<ts>/manifest.json` (timestamp, Desktop version, source/destination space, path, sha256 before/after) + byte copies of overwritten files. Only `done` snapshots and `aborted` ones that wrote nothing are pruned (last 200 kept); `in-progress` (crashed), partially applied, `rolled-back` and `PINNED` ones are kept.
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
- Not yet verified: when a newer entry lands in the org that is **active** at that moment (switch within the quiet window), Desktop may keep its stale in-memory copy and re-write it on the next focus; only metadata could regress, never the transcript.
- `archived-sessions.idx` is per org and not synced; archive state is carried by the entry's `isArchived` flag only.
- Deleting a session in one org is not propagated; the copy in the other org stays (not resurrected where a tombstone exists).
- If activity favours one side and focus the other, the side with the newer `lastActivityAt` wins for every field except the title group (merged separately, see rule 4).
- Verified live: create, continue (both directions), fast switch, rename, archive. Deletion is intentionally not propagated.
- `sync.log` / `launchd.log` are not rotated (a few lines per run).

## Uninstall
```bash
./install-org-sync.sh --uninstall     # removes agent + ~/bin/ccd-org-sync, keeps snapshots
```
Entries already merged stay. `rollback <ts>` is meant for undoing a recent run: it refuses as soon as a synced entry has changed since (every open/focus changes one). The initial bulk migration snapshot is marked `PINNED` (never pruned): its manifest lists every entry created by the migration (`op: NEW`), which is the reference if you ever want to remove those copies by hand.
