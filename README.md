# ccd-session-sync

> **This fork adds `ccd-org-sync`** for one account with two orgs (e.g. Personal/Max + a Team org) switched with Claude Desktop's **native** org switcher. The upstream tools below assume two *different accounts* and don't handle this case. See [the section right below](#same-account-two-orgs-maxteam--ccd-org-sync).

## Same account, two orgs (Max/Team) — `ccd-org-sync`

### 1. What it is
Claude Desktop keeps one Code sidebar index **per org**. When you use the native switcher to go from Personal/Max to Team, you get a different list of folders and sessions, although the transcripts in `~/.claude/projects` are shared. That hurts when your Max quota runs out mid-session: you switch to Team and the session is gone from the sidebar. `ccd-org-sync` keeps both orgs' indexes merged. The same folders and sessions appear on both sides, and a session continued under Team shows its continuation back under Max. It never touches transcripts, credentials, the org switch itself, classic chats or Cowork.

### 2. How it works
```
  ~/.claude/projects/*/<cli>.jsonl   (shared transcripts — never written)
                 ▲ cliSessionId
  claude-code-sessions/<acct>/<MAX org>/local_*.json   ◄──┐
  claude-code-sessions/<acct>/<TEAM org>/local_*.json  ◄──┤ union merge, per entry:
                                                           │ newer (lastActivityAt, lastFocusedAt) wins,
  launchd WatchPaths on both dirs ──► ccd-org-sync sync ──┘ equal+different → ABORT
```
Desktop only writes the active org's index and re-reads the index on every switch. Whatever you do under one org is therefore merged into the other org's index within seconds, and it's there the next time you switch. You don't need a restart or a manual command. Every writing run takes a SHA-256 manifest snapshot, so it can be rolled back. Full rules, observed facts and safety mechanics are in [docs/max-team-org-sync.md](docs/max-team-org-sync.md).

### 3. How to use it
Prereqs: macOS, `python3`, Claude Desktop logged in, and the two org ids from `ls ~/Library/Application\ Support/Claude/claude-code-sessions/<account>/`.
```bash
PAIR="MAX=<account>/<max-org>,TEAM=<account>/<team-org>"
CCD_PAIR="$PAIR" bin/ccd-org-sync sync --dry-run     # preview, writes nothing
# first bulk merge with Desktop fully quit (Cmd+Q), then relaunch:
CCD_PAIR="$PAIR" bin/ccd-org-sync sync
./install-org-sync.sh "$PAIR"                        # ~/bin/ccd-org-sync + one launchd agent
```
From then on you just switch orgs natively. The only manual steps left:
- On a **"ccd-org-sync: BLOCKED"** notification, follow the runbook, then run `ccd-org-sync unblock`.
- To undo, run `ccd-org-sync rollback <ts>`. To remove, run `./install-org-sync.sh --uninstall`.

Tests: `python3 -m unittest tests/test_org_sync.py`. `install-org-sync.sh` installs **only** this tool. It does not install `claude-archive-sync` or `claude-second`.

---

## Upstream: switching between two different accounts

Keep your **Claude Desktop** sidebar session list intact when you switch between Claude accounts on macOS — plus a transcript backup that survives the 30‑day cleanup, all git‑versioned and reversible.

> Solves the problem in [anthropics/claude-code#48511](https://github.com/anthropics/claude-code/issues/48511) ("session history lost when switching accounts", closed *not planned*) and [#74662](https://github.com/anthropics/claude-code/issues/74662) (request for a local‑sessions view). If Anthropic ships a native fix, you can uninstall this and lose nothing.

## The problem

Claude Desktop stores the sidebar session list **per logged‑in account**:

```
~/Library/Application Support/Claude/claude-code-sessions/<accountUUID>/<orgUUID>/local_*.json
```

Each `local_*.json` is a tiny index entry that points (via `cliSessionId`) at the real transcript. When you log into a **different** account, the app reads that account's (empty) directory and your sidebar looks wiped — even though:

- your **conversations are safe** — transcripts live account‑agnostically in `~/.claude/projects/**/*.jsonl`, and
- `claude --resume` (press **Ctrl+A** to show all) can reopen any local session under **any** login.

Only the Desktop **sidebar index** is account‑scoped. This tool re‑points that index so both accounts show the same list.

## What it does

| Component | Job |
|---|---|
| `ccd-migrate` | Git‑versioned, tombstone‑aware, **newer‑wins** merge of the other account's index entries into the current account's dir. Subcommands: `--dry-run`, `status`, `log`, `undo`, `snapshot`. |
| `ccd-migrate-auto` + launchd | Watches `cowork-enabled-cli-ops.json`; when the logged‑in account changes it runs `ccd-migrate` automatically and posts a macOS notification. |
| `claude-archive-sync` + launchd | Daily `rsync` of `~/.claude/projects` transcripts to `~/ClaudeArchive` (**no `--delete`**, so anything the app later prunes stays archived). The real long‑term data‑loss risk is the `cleanupPeriodDays` retention pruning, not account switching. |
| `claude-second` | Optional: launches a **second isolated Claude Desktop instance** (`open -n --user-data-dir=…`) so you can run one account per instance and stop switching entirely. |

## Safety by design

- **Never touches your conversations or credentials.** It only copies the small index JSONs and (optionally) backs up transcripts read‑only. It never reads or writes the Keychain, OAuth tokens, or `oauthAccount`.
- **The session index is git‑versioned.** Every change to the small `local_*.json` index files is a commit in `~/.ccd-sessions-git`; `ccd-migrate log` shows history and `ccd-migrate undo` reverts the last migration. (Large local‑agent‑mode *payload* directories are copied but intentionally **not** tracked in git — so `undo` restores the index, not those directories. They're additive/copy‑once and never deleted.)
- **Newer‑wins, never destructive.** Merges index entries by modification time with atomic temp‑then‑rename writes (Claude never reads a half‑written file); `rsync` runs **without `--delete`**; `--dry-run` previews everything and writes nothing.
- **Reversible install.** `uninstall.sh` removes the agents/scripts and keeps your backups by default.

## Install

```bash
git clone https://github.com/jeremy9682/ccd-session-sync
cd ccd-session-sync
./install.sh              # scripts -> ~/bin, bootstrap git repo, load launchd agents
# or: ./install.sh --no-agents   (manual `ccd-migrate` only, no background automation)
# or: ./install.sh --dry-run     (print every step, change nothing)
```

Requirements: macOS, `git`, `python3`, `rsync`, and Claude Desktop launched at least once. Put `~/bin` on your `PATH`.

## Use

```bash
ccd-migrate status      # accounts, per-dir entry counts, git state
ccd-migrate --dry-run   # preview what would be merged
ccd-migrate             # merge now  (the auto agent also does this on account switch)
ccd-migrate log         # migration history
ccd-migrate undo        # revert the last migration
```

After any migration, **fully quit and relaunch Claude Desktop** — it reads the session index only at startup.

Finding an old session across accounts without migrating? `claude --resume` in the project directory, then **Ctrl+A**.

### More than two accounts

Auto‑detection picks the current account as destination and the most‑recently‑active other account as source. With 3+ accounts, force direction:

```bash
CCD_FROM=<uuid-prefix> CCD_TO=<uuid-prefix> ccd-migrate
```

### The zero‑switching alternative

If you juggle accounts constantly, run two Desktop instances instead of switching:

```bash
claude-second           # opens a 2nd instance with an isolated data dir
```

Log each instance into its own account **with the other instance fully quit** (the `claude://` login deep‑link goes to an arbitrary running instance — the #1 pitfall). Then both stay logged in, each with its own sidebar, and account switching disappears. Don't run Cowork/local‑agent mode in the second instance unless you have several GB of free disk (it pulls its own VM bundles).

## Uninstall

```bash
./uninstall.sh          # remove agents + scripts, KEEP git history & archive
./uninstall.sh --purge  # also delete ~/.ccd-sessions-git and ~/ClaudeArchive (irreversible)
```

## Privacy

Your **local** state is private data — do not paste it into bug reports or commit it anywhere:

- `~/.ccd-sessions-git` (the git history) tracks your real session index entries, including account/org UUIDs and session **titles**.
- `~/ClaudeArchive` holds your actual transcripts; the logs and `last-owner` file contain account UUIDs.
- `ccd-migrate status` / `log` and commit messages print account/org identifiers.

None of that is in *this* repo, and `.gitignore` keeps it out of any clone. Only the tooling is published.

## Known limitations (it's experimental)

- **Unofficial / version‑sensitive.** Operates on Claude Desktop's *internal, undocumented* file layout. A future app update could change it and break this tool — worst case it errors out; your data is untouched and git‑backed. No schema/version detection. Not affiliated with or endorsed by Anthropic; no warranty; you are responsible for your own backups and for compliance with Claude's terms.
- **Heuristic direction.** Destination = current account; source = most‑recently‑active other account. With 3+ accounts, or an account that has more than one org directory, it warns and picks the newest — **always `--dry-run` first**, and use `CCD_FROM=/CCD_TO=` to force direction.
- **`undo`** reverts the most recent `migrate:` commit (not necessarily the very last operation) and can conflict if you edited things after; for surgical rollback use `ccd-migrate log` + `git … revert <hash>`.
- **Tombstones** (`.tombstones`, to keep intentionally‑deleted sessions from reappearing) are hand‑maintained/advanced — the installer seeds an empty one; ignore it unless you need it.
- **Writes are not transactional** — they land in Claude's live data dir and are then committed to git. Keep the app quit during a manual migration if you want a perfectly clean diff.
- The sidebar's project grouping is derived from repo/PR metadata; this tool does **not** rewrite `originCwd` (load‑bearing for worktree/PR features). Sessions group under whatever project directory they actually ran in.
- macOS only (uses `launchd` + `osascript`).

## License

MIT — see [LICENSE](LICENSE).
