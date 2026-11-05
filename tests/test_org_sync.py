"""End-to-end tests for bin/ccd-org-sync against a fake Claude Desktop tree (python3 -m unittest)."""
import glob, hashlib, json, os, subprocess, tempfile, time, unittest

SCRIPT = os.path.join(os.path.dirname(__file__), '..', 'bin', 'ccd-org-sync')
PYTHON = os.environ.get('CCD_TEST_PYTHON', '/usr/bin/python3')  # the interpreter the launchd agent runs
ACCT = '11111111-0000-0000-0000-000000000000'
MAX = f'{ACCT}/aaaaaaaa-0000-0000-0000-000000000000'
TEAM = f'{ACCT}/bbbbbbbb-0000-0000-0000-000000000000'


# the three keys that make a real entry ~540 KB (83 % / 15 % / 2 % of 662 MB on 2026-11-05).
# The sync never reads them and must copy them through untouched; fixtures carry them so that a
# cache feeding a write — which would silently drop them — fails the suite instead of passing it.
HEAVY = {'remoteMcpServersConfig': {'servers': [{'name': f's{i}', 'cmd': 'x' * 40} for i in range(20)]},
         'enabledMcpTools': [f'mcp__srv__tool_{i}' for i in range(50)],
         'promptAppendSnapshot': 'z' * 200}


def entry(n, act=1000, focus=None, **kw):
    e = {'sessionId': f'local_{n}', 'cliSessionId': f'cli-{n}', 'cwd': '/p/alpha',
         'createdAt': 1, 'lastActivityAt': act, 'title': 't', **HEAVY}
    if focus is not None:
        e['lastFocusedAt'] = focus
    e.update(kw)
    return e


def task(i, **kw):
    t = {'id': i, 'cronExpression': '0 9 * * *', 'enabled': True, 'filePath': f'/x/{i}/SKILL.md',
         'createdAt': 1, 'cwd': '/p/alpha'}
    t.update(kw)
    return {k: v for k, v in t.items() if v is not None}


class OrgSyncTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = os.path.join(self.tmp, 'Claude')
        self.proj = os.path.join(self.tmp, 'projects', '-p-alpha')
        self.bk = os.path.join(self.tmp, 'backups')
        self.logs = os.path.join(self.tmp, 'logs')
        os.makedirs(self.logs)
        for o in (MAX, TEAM):
            os.makedirs(self.org(o))
        os.makedirs(self.proj)
        self.write(os.path.join(self.root, 'cowork-enabled-cli-ops.json'), {'ownerAccountId': ACCT})

    def org(self, o):
        return os.path.join(self.root, 'claude-code-sessions', o)

    def write(self, p, obj):
        with open(p, 'w') as f:
            json.dump(obj, f)

    def put(self, o, e, transcript=True):
        self.write(os.path.join(self.org(o), e['sessionId'] + '.json'), e)
        if transcript:
            open(os.path.join(self.proj, e['cliSessionId'] + '.jsonl'), 'w').close()

    def get(self, o, n):
        p = os.path.join(self.org(o), f'local_{n}.json')
        return json.load(open(p)) if os.path.exists(p) else None

    def run_sync(self, *args, **env):
        e = dict(os.environ, CCD_CLAUDE_DIR=self.root, CCD_PROJECTS_DIR=os.path.dirname(self.proj),
                 CCD_BACKUP_DIR=self.bk, CCD_QUIET_SECONDS='0', CCD_NO_NOTIFY='1', CCD_DESKTOP_LOGS=self.logs,
                 CCD_PAIR=f'MAX={MAX},TEAM={TEAM}')
        e.update(env)
        return subprocess.run([PYTHON, SCRIPT, *args], env=e, capture_output=True, text=True)

    def stats(self, r):
        """the sidebar stats block (a run may print a tasks block before it)."""
        dec, i = json.JSONDecoder(), 0
        while (i := r.stdout.find('{', i)) != -1:
            obj, end = dec.raw_decode(r.stdout[i:])
            if 'cache_hit' in obj:
                return obj
            i += end
        raise AssertionError(f'no sidebar stats block in {r.stdout!r}')

    def cache_file(self):
        return os.path.join(self.bk, 'index-cache.json')

    def tree_hash(self):
        h = hashlib.sha256()
        for p in sorted(glob.glob(os.path.join(self.root, '**'), recursive=True)):
            if os.path.isfile(p):
                h.update(p.encode() + open(p, 'rb').read())
        return h.hexdigest()

    def snapshots(self):
        return sorted(d for d in os.listdir(self.bk) if os.path.isdir(os.path.join(self.bk, d)))

    def active(self, o, reset=False):
        """Desktop's log: the CCD scheduler last loaded org o (then started a switch if reset)."""
        with open(os.path.join(self.logs, 'main.log'), 'a') as f:
            f.write("2026-10-06 12:45:19 [info] [CCDScheduledTasks] Reset\n")
            f.write(f"2026-10-06 12:45:24 [info] [CCDScheduledTasks] Initialized {{ accountId: '{ACCT}', "
                    f"orgId: '{o.split('/')[1]}' }}\n")
            if reset:
                f.write("2026-10-06 12:50:00 [info] [CCDScheduledTasks] Reset\n")

    def put_tasks(self, o, tasks, **extra):
        with open(os.path.join(self.org(o), 'scheduled-tasks.json'), 'w') as f:
            f.write(json.dumps({'scheduledTasks': tasks, 'recordedSkips': {}, **extra}, indent=2))

    def tasks(self, o):
        p = os.path.join(self.org(o), 'scheduled-tasks.json')
        return {t['id']: t for t in json.load(open(p))['scheduledTasks']} if os.path.exists(p) else None

    # --- cases -------------------------------------------------------------------------------
    def test_max_only_and_team_only_union(self):
        self.put(MAX, entry('a')); self.put(MAX, entry('b'))
        self.put(TEAM, entry('e')); self.put(TEAM, entry('f'))
        r = self.run_sync('sync'); self.assertEqual(r.returncode, 0, r.stderr)
        for n in 'abef':
            self.assertEqual(self.get(MAX, n), self.get(TEAM, n))

    def test_identical_common_is_noop(self):
        self.put(MAX, entry('c')); self.put(TEAM, entry('c'))
        before = self.tree_hash()
        r = self.run_sync('sync'); self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(before, self.tree_hash())
        self.assertIn('"common_identical": 1', r.stdout)

    def test_max_newer_wins(self):
        self.put(MAX, entry('c', act=2000, title='new')); self.put(TEAM, entry('c', act=1000))
        self.assertEqual(self.run_sync('sync').returncode, 0)
        self.assertEqual(self.get(TEAM, 'c')['title'], 'new')

    def test_team_newer_wins_via_lastFocusedAt(self):
        self.put(MAX, entry('c', act=1000, focus=5)); self.put(TEAM, entry('c', act=1000, focus=9, title='new'))
        self.assertEqual(self.run_sync('sync').returncode, 0)
        self.assertEqual(self.get(MAX, 'c')['title'], 'new')

    def test_ambiguous_conflict_skips_only_that_entry(self):
        # real outage 2026-09-25 -> 10-06: one conflict blocked the whole sync for 11 days
        self.put(MAX, entry('c', title='x')); self.put(TEAM, entry('c', title='y'))
        self.put(MAX, entry('a'))
        r = self.run_sync('sync')
        self.assertEqual(r.returncode, 0, r.stderr); self.assertIn('conflict', r.stderr)
        self.assertEqual(self.get(TEAM, 'a'), self.get(MAX, 'a'))   # the others still sync
        self.assertEqual((self.get(MAX, 'c')['title'], self.get(TEAM, 'c')['title']), ('x', 'y'))  # untouched
        self.assertFalse(os.path.exists(os.path.join(self.bk, 'BLOCKED')))
        self.put(MAX, entry('b'))
        self.assertEqual(self.run_sync('sync').returncode, 0)
        self.assertIsNotNone(self.get(TEAM, 'b'))                  # later runs keep syncing
        log = open(os.path.join(self.bk, 'sync.log')).read()
        self.assertEqual(log.count('conflict(s) skipped'), 1)      # logged once per conflict set
        self.put(TEAM, entry('c', title='x'))                      # resolved
        self.assertEqual(self.run_sync('sync').returncode, 0)
        self.assertFalse(os.path.exists(os.path.join(self.bk, 'CONFLICTS')))

    def test_background_pr_refresh_newer_file_wins(self):
        self.put(MAX, entry('c', prs=[{'state': 'OPEN'}]))
        self.put(TEAM, entry('c', prs=[{'state': 'MERGED'}]))
        t = os.path.getmtime(os.path.join(self.org(MAX), 'local_c.json'))
        os.utime(os.path.join(self.org(MAX), 'local_c.json'), (t - 100, t - 100))
        r = self.run_sync('sync'); self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.get(MAX, 'c')['prs'], [{'state': 'MERGED'}])

    def test_equal_version_any_field_newer_file_wins(self):
        # real case (Desktop 2.19675): un-starred under Max, no version bump; Team still starred
        self.put(TEAM, entry('c', isStarred=True, publishedArtifacts=None))
        self.put(MAX, entry('c', isStarred=False, publishedArtifacts=[{'url': 'u'}]))
        os.utime(os.path.join(self.org(TEAM), 'local_c.json'), (1, 1))
        r = self.run_sync('sync'); self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn('conflict', r.stderr)
        self.assertEqual(self.get(TEAM, 'c'), self.get(MAX, 'c'))
        self.assertFalse(self.get(TEAM, 'c')['isStarred'])

    def test_only_run_leaves_conflict_record_alone(self):
        self.put(MAX, entry('c', title='x')); self.put(TEAM, entry('c', title='y')); self.put(MAX, entry('a'))
        self.run_sync('sync')
        self.assertEqual(self.run_sync('sync', '--only', 'local_a').returncode, 0)
        self.assertTrue(os.path.exists(os.path.join(self.bk, 'CONFLICTS')))
        self.run_sync('sync')
        self.assertEqual(open(os.path.join(self.bk, 'sync.log')).read().count('conflict(s) skipped'), 1)

    def test_equal_version_equal_mtime_is_conflict(self):
        self.put(MAX, entry('c', isStarred=True)); self.put(TEAM, entry('c', isStarred=False))
        for o in (MAX, TEAM):
            os.utime(os.path.join(self.org(o), 'local_c.json'), ns=(10**18, 10**18))
        self.assertIn('conflict', self.run_sync('sync').stderr)
        self.assertNotEqual(self.get(MAX, 'c'), self.get(TEAM, 'c'))

    def test_rename_survives_newer_activity_in_other_org(self):
        # real case: renamed under Max (no activity bump) while Team got a newer message
        self.put(MAX, entry('c', act=1000, focus=60, title='renamed', titleSource='user', previousTitles=['auto t']))
        self.put(TEAM, entry('c', act=2000, focus=50, title='auto t', titleSource='auto', turns=4))
        r = self.run_sync('sync'); self.assertEqual(r.returncode, 0, r.stderr)
        for o in (MAX, TEAM):
            e = self.get(o, 'c')
            self.assertEqual((e['title'], e['titleSource'], e['lastActivityAt'], e['turns']), ('renamed', 'user', 2000, 4))
        n = len(self.snapshots())
        self.assertEqual(self.run_sync('sync').returncode, 0); self.assertEqual(n, len(self.snapshots()))  # stable

    def test_rename_history_decides_between_two_chosen_titles(self):
        self.put(MAX, entry('c', act=2000, title='v1', titleSource='user'))
        self.put(TEAM, entry('c', act=1000, title='v2', titleSource='user', previousTitles=['v1']))
        self.assertEqual(self.run_sync('sync').returncode, 0)
        self.assertEqual(self.get(MAX, 'c')['title'], 'v2'); self.assertEqual(self.get(MAX, 'c')['lastActivityAt'], 2000)

    def test_two_chosen_titles_without_history_abort(self):
        self.put(MAX, entry('c', act=2000, title='x', titleSource='user'))
        self.put(TEAM, entry('c', act=1000, title='y', titleSource='user'))
        self.assertIn('conflict', self.run_sync('sync').stderr)

    def test_equal_version_different_auto_titles_abort(self):
        self.put(MAX, entry('c', title='x', titleSource='auto')); self.put(TEAM, entry('c', title='y', titleSource='auto'))
        self.assertIn('conflict', self.run_sync('sync').stderr)

    def test_newer_auto_retitle_follows_entry(self):
        self.put(MAX, entry('c', act=2000, title='new auto', titleSource='auto'))
        self.put(TEAM, entry('c', act=1000, title='old auto', titleSource='auto'))
        self.assertEqual(self.run_sync('sync').returncode, 0)
        self.assertEqual(self.get(TEAM, 'c')['title'], 'new auto')

    def test_invalid_json_aborts_without_writes(self):
        self.put(MAX, entry('a'))
        with open(os.path.join(self.org(TEAM), 'local_bad.json'), 'w') as f:
            f.write('{nope')
        before = self.tree_hash()
        r = self.run_sync('sync')
        self.assertEqual(r.returncode, 1); self.assertIn('invalid JSON', r.stderr)
        self.assertEqual(before, self.tree_hash())

    def test_unknown_schema_aborts(self):
        self.put(MAX, entry('a'))
        self.write(os.path.join(self.org(TEAM), 'local_x.json'), {'sessionId': 'local_other'})
        self.assertIn('unexpected schema', self.run_sync('sync').stderr)

    def test_dry_run_modifies_nothing(self):
        self.put(MAX, entry('a')); self.put(TEAM, entry('c', act=5)); self.put(MAX, entry('c', act=9))
        before = self.tree_hash()
        r = self.run_sync('sync', '--dry-run')
        self.assertEqual(r.returncode, 0, r.stderr); self.assertIn('NO FILES MODIFIED', r.stdout)
        self.assertEqual(before, self.tree_hash())
        self.assertFalse(os.path.exists(self.bk) and self.snapshots())

    def test_tombstone_never_resurrected(self):
        self.put(MAX, entry('d')); self.put(TEAM, entry('t0'))
        open(os.path.join(self.org(TEAM), 'deleted_d'), 'w').write('123')
        self.assertEqual(self.run_sync('sync').returncode, 0)
        self.assertIsNone(self.get(TEAM, 'd'))

    def test_dead_transcript_skipped(self):
        self.put(MAX, entry('z'), transcript=False); self.put(TEAM, entry('t0'))
        r = self.run_sync('sync'); self.assertIn('"dead_skipped": 1', r.stdout)
        self.assertIsNone(self.get(TEAM, 'z'))

    def test_scheduled_task_runs_not_copied(self):
        self.put(MAX, entry('s', scheduledTaskId='task-1')); self.put(TEAM, entry('t0'))
        r = self.run_sync('sync'); self.assertIn('"scheduled_task_skipped": 1', r.stdout)
        self.assertIsNone(self.get(TEAM, 's'))

    def test_only_one_session(self):
        self.put(MAX, entry('a')); self.put(MAX, entry('b'))
        self.assertEqual(self.run_sync('sync', '--only', 'local_a').returncode, 0)
        self.assertIsNotNone(self.get(TEAM, 'a')); self.assertIsNone(self.get(TEAM, 'b'))

    def test_wrong_account_logged_in_refuses(self):
        self.put(MAX, entry('a'))
        self.write(os.path.join(self.root, 'cowork-enabled-cli-ops.json'), {'ownerAccountId': 'other'})
        r = self.run_sync('sync'); self.assertEqual(r.returncode, 1); self.assertIn('refusing', r.stderr)
        self.assertIsNone(self.get(TEAM, 'a'))

    def test_bad_pair_refuses(self):
        r = self.run_sync('sync', CCD_PAIR=f'MAX={MAX}')
        self.assertIn('exactly 2', r.stderr)
        r = self.run_sync('sync', CCD_PAIR=f'MAX={MAX},TEAM=ffff/{TEAM.split("/")[1]}')
        self.assertIn('same account', r.stderr)

    def test_quiet_gate_waits_then_gives_up(self):
        self.put(MAX, entry('a'))
        r = self.run_sync('sync', CCD_QUIET_SECONDS='30', CCD_QUIET_MAX_WAIT='0')
        self.assertIn('never went quiet', r.stderr); self.assertIsNone(self.get(TEAM, 'a'))

    def test_interrupted_write_leaves_valid_files_and_rolls_back(self):
        self.put(MAX, entry('a')); self.put(MAX, entry('b')); self.put(MAX, entry('c', act=9))
        self.put(TEAM, entry('c', act=1, title='old'))
        before = self.tree_hash()
        r = self.run_sync('sync', CCD_TEST_CRASH_AFTER='2')
        self.assertEqual(r.returncode, 9)
        for o in (MAX, TEAM):                               # every index file still parses
            for p in glob.glob(os.path.join(self.org(o), 'local_*.json')):
                json.load(open(p))
        ts = self.snapshots()[0]
        man = json.load(open(os.path.join(self.bk, ts, 'manifest.json')))
        self.assertEqual(man['status'], 'in-progress')
        self.assertEqual(self.run_sync('rollback', ts).returncode, 0)
        self.assertEqual(before, self.tree_hash())

    def test_rollback_restores_exact_bytes_and_refuses_after_changes(self):
        self.put(MAX, entry('c', act=9, title='new')); self.put(TEAM, entry('c', act=1, title='old'))
        self.put(MAX, entry('a'))
        before = self.tree_hash()
        self.assertEqual(self.run_sync('sync').returncode, 0)
        ts = self.snapshots()[0]
        man = json.load(open(os.path.join(self.bk, ts, 'manifest.json')))
        self.assertEqual(man['status'], 'done'); self.assertEqual(len(man['ops']), 2)
        self.assertTrue(all(len(o['sha256_after']) == 64 for o in man['ops']))
        # Desktop touches a synced file afterwards -> rollback must refuse
        self.put(TEAM, entry('a', act=77))
        r = self.run_sync('rollback', ts); self.assertEqual(r.returncode, 1); self.assertIn('refused', r.stderr)
        self.put(TEAM, json.load(open(os.path.join(self.org(MAX), 'local_a.json'))))
        self.assertEqual(self.run_sync('rollback', ts).returncode, 0)
        self.assertEqual(before, self.tree_hash())
        self.assertTrue(os.path.exists(os.path.join(self.bk, 'BLOCKED')))

    def test_self_triggered_rerun_is_noop(self):
        self.put(MAX, entry('a')); self.put(TEAM, entry('e'))
        self.run_sync('sync')
        n = len(self.snapshots())
        r = self.run_sync('sync'); self.assertEqual(r.returncode, 0)
        self.assertEqual(n, len(self.snapshots()))
        self.assertEqual([], glob.glob(os.path.join(self.root, '**', '.ccdsync-*'), recursive=True))

    # --- review follow-ups ---------------------------------------------------------------------
    def test_empty_side_refuses(self):
        self.put(MAX, entry('a'))
        r = self.run_sync('sync'); self.assertIn('one side is empty', r.stderr)
        self.assertIsNone(self.get(TEAM, 'a'))

    def test_bulk_creation_cap(self):
        self.put(TEAM, entry('t0'))
        for i in range(4):
            self.put(MAX, entry(f'm{i}'))
        r = self.run_sync('sync', CCD_MAX_CREATE='3'); self.assertIn('cap 3', r.stderr)
        self.assertIsNone(self.get(TEAM, 'm0'))
        self.assertEqual(self.run_sync('sync', '--allow-bulk', CCD_MAX_CREATE='3').returncode, 0)
        self.assertIsNotNone(self.get(TEAM, 'm3'))

    def test_rollback_refuses_corrupt_backup_without_writing(self):
        self.put(MAX, entry('c', act=9, title='new')); self.put(TEAM, entry('c', act=1, title='old'))
        self.run_sync('sync')
        ts = self.snapshots()[0]
        b = glob.glob(os.path.join(self.bk, ts, 'files', '*', '*.json'))[0]
        open(b, 'w').write('GARBAGE')
        r = self.run_sync('rollback', ts); self.assertIn('corrupt', r.stderr)
        self.assertEqual(self.get(TEAM, 'c')['title'], 'new')

    def test_block_set_while_sync_waits_prevents_write(self):
        self.put(MAX, entry('a')); self.put(TEAM, entry('t0'))
        e = dict(os.environ, CCD_CLAUDE_DIR=self.root, CCD_PROJECTS_DIR=os.path.dirname(self.proj),
                 CCD_BACKUP_DIR=self.bk, CCD_QUIET_SECONDS='2', CCD_NO_NOTIFY='1',
                 CCD_PAIR=f'MAX={MAX},TEAM={TEAM}')
        p = subprocess.Popen(['python3', SCRIPT, 'sync'], env=e, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        time.sleep(0.8)
        open(os.path.join(self.bk, 'BLOCKED'), 'w').write('rolling back x')
        _, err = p.communicate(timeout=30)
        self.assertEqual(p.returncode, 1); self.assertIn('blocked', err)
        self.assertIsNone(self.get(TEAM, 'a'))

    def test_prune_keeps_in_progress_and_rolled_back(self):
        self.put(TEAM, entry('t0'))
        self.put(MAX, entry('a')); self.put(MAX, entry('b'))
        self.run_sync('sync', CCD_TEST_CRASH_AFTER='1')           # leaves an in-progress snapshot
        crashed = self.snapshots()[0]
        for i in range(3):
            self.put(MAX, entry(f'x{i}'))
            self.run_sync('sync', CCD_KEEP_SNAPSHOTS='1')
        self.assertIn(crashed, self.snapshots())
        self.assertEqual(len(self.snapshots()), 2)                # crashed + newest done
        rb = self.snapshots()[-1]
        self.assertEqual(self.run_sync('rollback', rb).returncode, 0); self.run_sync('unblock')
        for i in range(2):
            self.put(MAX, entry(f'y{i}'))
            self.run_sync('sync', CCD_KEEP_SNAPSHOTS='1')
        self.assertIn(rb, self.snapshots()); self.assertIn(crashed, self.snapshots())

    def test_prune_keeps_pinned(self):
        self.put(TEAM, entry('t0')); self.put(MAX, entry('a'))
        self.run_sync('sync')
        pinned = self.snapshots()[0]
        open(os.path.join(self.bk, pinned, 'PINNED'), 'w').close()
        for i in range(3):
            self.put(MAX, entry(f'x{i}'))
            self.run_sync('sync', CCD_KEEP_SNAPSHOTS='1')
        self.assertIn(pinned, self.snapshots())
        self.assertEqual(len(self.snapshots()), 2)                # pinned + newest done; 2 pruned

    def test_undone_rename_wins_in_either_orientation(self):
        for first, second in ((MAX, TEAM), (TEAM, MAX)):
            self.setUp()
            self.put(first, entry('c', act=5, title='A', titleSource='user', previousTitles=['A', 'B']))
            self.put(second, entry('c', act=5, title='B', titleSource='user', previousTitles=['A']))
            r = self.run_sync('sync'); self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual((self.get(MAX, 'c')['title'], self.get(TEAM, 'c')['title']), ('A', 'A'))

    def test_divergent_rename_histories_abort(self):
        self.put(MAX, entry('c', title='A', titleSource='user', previousTitles=['B', 'X']))
        self.put(TEAM, entry('c', title='B', titleSource='user', previousTitles=['A', 'Y']))
        self.assertIn('conflict', self.run_sync('sync').stderr)

    def test_entry_changed_during_sync_only_defers_that_entry(self):
        """one session Desktop rewrites mid-run must not hold back the others (the whole point:
        with a batch-wide abort, a single busy session starved every pending creation)."""
        self.put(TEAM, entry('t0'))
        for n in ('a', 'b', 'c'):
            self.put(MAX, entry(n))
        r = self.run_sync('sync', CCD_TEST_TOUCH_BEFORE_APPLY='1')
        self.assertEqual(r.returncode, 0, r.stderr)
        landed = {os.path.basename(f)[:-5] for f in glob.glob(os.path.join(self.org(TEAM), 'local_*.json'))}
        self.assertEqual(len(landed - {'local_t0'}), 2)          # 4 ops planned, 1 deferred
        self.run_sync('sync')                                    # quiet run: it converges
        self.assertIsNotNone(self.get(MAX, 't0'))                # the TEAM-only entry went the other way
        landed = {os.path.basename(f)[:-5] for f in glob.glob(os.path.join(self.org(TEAM), 'local_*.json'))}
        self.assertEqual(landed, {'local_t0', 'local_a', 'local_b', 'local_c'})

    def test_entry_deleted_during_sync_is_deferred_not_fatal(self):
        """Desktop deletes an entry mid-run: that one is deferred, the others still land
        (an os.stat on the vanished file would otherwise abort the whole run)."""
        self.put(TEAM, entry('t0'))
        for n in ('a', 'b'):
            self.put(MAX, entry(n))
        r = self.run_sync('sync', CCD_TEST_TOUCH_BEFORE_APPLY='rm')
        self.assertEqual(r.returncode, 0, r.stderr)
        landed = {os.path.basename(f)[:-5] for f in glob.glob(os.path.join(self.org(TEAM), 'local_*.json'))}
        self.assertEqual(len(landed - {'local_t0'}), 1)

    def test_every_entry_changed_writes_nothing_and_leaves_no_snapshot(self):
        self.put(TEAM, entry('t0')); self.put(MAX, entry('a'))
        r = self.run_sync('sync', CCD_TEST_TOUCH_BEFORE_APPLY='all')
        self.assertIn('changed during sync', r.stderr)
        self.assertEqual(glob.glob(os.path.join(self.org(TEAM), 'local_a.json')), [])
        self.assertEqual(self.snapshots(), [])

    def test_skip_is_on_disk_before_the_next_write(self):
        """a crash right after a CAS skip must leave a manifest that still says 'skipped'.
        Held only in memory, it claimed the skipped file had been written and rollback refused."""
        self.put(TEAM, entry('a')); self.put(MAX, entry('a', act=2000, title='newer'))
        self.put(MAX, entry('z'))                 # 'a' (UPD, skipped) is ordered before 'z' (NEW)
        self.run_sync('sync', CCD_TEST_TOUCH_DEST_BEFORE_WRITE='1', CCD_TEST_CRASH_AFTER='1',
                      CCD_KEEP_SNAPSHOTS='1')
        man = json.load(open(os.path.join(self.bk, self.snapshots()[0], 'manifest.json')))
        skipped = [os.path.basename(o['path']) for o in man['ops'] if o.get('skipped')]
        self.assertEqual(skipped, ['local_a.json'], man['ops'])
        r = self.run_sync('rollback', self.snapshots()[0])
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)

    def test_tasks_and_sidebar_both_land_in_one_run(self):
        """cmd_sync calls apply() twice; when both fell in the same second the snapshot dirs
        collided and the FileExistsError killed the sidebar sync for that run."""
        self.active(MAX)
        self.put_tasks(MAX, [{'id': 'T1', 'cronExpression': '0 9 * * *'}])
        self.put_tasks(TEAM, [])
        self.put(TEAM, entry('t0')); self.put(MAX, entry('a'))
        r = self.run_sync('sync')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('T1', self.tasks(TEAM) or {})       # tasks mirrored
        self.assertIsNotNone(self.get(TEAM, 'a'))         # and the sidebar entry still synced

    # --- index cache: it may only rule work out, never feed a write -----------------------

    def test_warm_cache_plans_exactly_what_a_cold_pass_plans(self):
        """same ops, with the second pass answered almost entirely from the cache."""
        self.put(TEAM, entry('t0'))
        for n in ('a', 'b', 'c'):
            self.put(MAX, entry(n))
        self.put(TEAM, entry('c', act=3000, title='newer'))          # an UPD as well as NEWs
        cold = self.run_sync('sync', '--dry-run', '--verbose')
        warm = self.run_sync('sync', '--dry-run', '--verbose')
        self.assertEqual(cold.returncode, 0, cold.stderr)
        ops = lambda r: sorted(l for l in r.stdout.splitlines() if l.startswith('  '))
        self.assertEqual(ops(warm), ops(cold))
        self.assertTrue(ops(cold), cold.stdout)
        self.assertEqual(self.stats(cold)['cache_hit'], 0)           # nothing cached yet
        self.assertEqual(self.stats(warm)['cache_miss'], 0)          # all five answered from cache
        self.assertEqual(self.stats(warm)['cache_hit'], 5)

    def test_merged_write_keeps_the_heavy_keys_with_a_warm_cache(self):
        """the dangerous path, and the reason the cache may never feed a write: a real merge
        serializes the whole object, so BOTH sides must come from the files. Fed from the cache's
        projection it would write an entry stripped of 98 % of its content.
        The divergence is laid down FIRST and a dry run then fills the cache without writing, so
        both sides are cache HITS when the real run reaches merge(). Filling the cache from an
        identical pair instead would leave the rewritten side a cache miss, and the entry would
        reach merge() through parse_entry — the invariant would never be exercised."""
        self.put(TEAM, entry('a', title='mine', titleSource='user'))  # human title, older
        self.put(MAX, entry('a', act=2000, title='auto'))             # newer, automatic title
        d = self.run_sync('sync', '--dry-run')
        self.assertEqual(self.stats(d)['cache_miss'], 2)              # cold: both sides read
        r = self.run_sync('sync', '--verbose')
        self.assertEqual(self.stats(r)['cache_hit'], 2, self.stats(r))   # warm: both from cache
        self.assertEqual(r.returncode, 0, r.stderr)
        # version winner (MAX, newer) and title winner (TEAM, human) differ, so the merged object
        # equals neither side: the only path that writes json.dumps(merged).
        self.assertIn('MERGE', r.stdout)
        for o in (MAX, TEAM):
            got = self.get(o, 'a')
            self.assertEqual(got['remoteMcpServersConfig'], HEAVY['remoteMcpServersConfig'], o)
            self.assertEqual(got['enabledMcpTools'], HEAVY['enabledMcpTools'], o)
            self.assertEqual(got['promptAppendSnapshot'], HEAVY['promptAppendSnapshot'], o)

    def test_new_entry_is_copied_byte_for_byte(self):
        self.put(TEAM, entry('t0')); self.put(MAX, entry('a'))
        self.run_sync('sync')
        src = open(os.path.join(self.org(MAX), 'local_a.json'), 'rb').read()
        self.assertEqual(open(os.path.join(self.org(TEAM), 'local_a.json'), 'rb').read(), src)
        self.assertIn(b'remoteMcpServersConfig', src)

    def test_a_corrupt_cache_costs_speed_not_correctness(self):
        self.put(TEAM, entry('t0')); self.put(MAX, entry('a'))
        self.run_sync('sync')                                         # writes a cache
        with open(self.cache_file(), 'w') as f:
            f.write('{not json at all')
        self.put(MAX, entry('b'))
        r = self.run_sync('sync')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIsNotNone(self.get(TEAM, 'b'))
        self.assertEqual(self.stats(r)['cache_hit'], 0)               # fell back to a cold pass
        self.assertEqual(json.load(open(self.cache_file()))['format'], 1)   # and rebuilt it

    def test_a_cache_of_another_format_is_ignored(self):
        self.put(TEAM, entry('t0')); self.put(MAX, entry('a'))
        self.run_sync('sync')
        c = json.load(open(self.cache_file())); c['format'] = 99
        self.write(self.cache_file(), c)
        r = self.run_sync('sync', '--dry-run')
        self.assertEqual(self.stats(r)['cache_hit'], 0)

    def test_a_rewritten_entry_misses_the_cache(self):
        self.put(TEAM, entry('t0')); self.put(MAX, entry('a'))
        self.run_sync('sync')
        # the cache is saved at the end of plan(), before apply() writes: the files a pass writes
        # carry a new stat and are re-read once on the next pass. One more pass reaches steady state.
        self.run_sync('sync', '--dry-run')
        r = self.run_sync('sync', '--dry-run')
        self.assertEqual(self.stats(r)['cache_miss'], 0, self.stats(r))   # steady state: all hits
        self.put(MAX, entry('a', act=5000))                               # Desktop rewrites one entry
        r = self.run_sync('sync', '--dry-run')
        self.assertEqual(self.stats(r)['cache_miss'], 1, self.stats(r))

    def test_read_paired_never_claims_stale_bytes_are_current(self):
        """Tested in-process: no subprocess run can force Desktop to write between our stat and our
        read. If read_paired returned the stat taken AFTER the read, that stat would match the file
        as it now stands while the bytes are older — moved_since would find nothing to defer, and
        the cache would store a fresh stat against stale bytes, so a real divergence between the
        two orgs could stay invisible until Desktop next rewrote the file."""
        import importlib.util, importlib.machinery, builtins, io as _io
        ldr = importlib.machinery.SourceFileLoader('ccd_org_sync', os.path.abspath(SCRIPT))
        spec = importlib.util.spec_from_loader('ccd_org_sync', ldr)  # the script has no .py suffix
        mod = importlib.util.module_from_spec(spec)
        ldr.exec_module(mod)

        f = os.path.join(self.org(MAX), 'local_a.json')
        self.write(f, entry('a'))
        first = os.stat(f)
        real_open, n = builtins.open, [0]

        def rewrite_after_every_read(path, *a, **k):          # Desktop, writing without pause
            fh = real_open(path, *a, **k)
            mode = a[0] if a else k.get('mode', 'r')
            if path == f and 'b' in mode and 'r' in mode:
                data = fh.read(); fh.close()
                n[0] += 1
                real_open(f, 'wb').write(json.dumps(entry('a', act=1000 + n[0])).encode())
                os.utime(f, ns=(first.st_atime_ns, first.st_mtime_ns + n[0] * 10 ** 9))
                return _io.BytesIO(data)
            return fh

        builtins.open = rewrite_after_every_read
        try:
            raw, st = mod.read_paired(f)
        finally:
            builtins.open = real_open

        now = os.stat(f)
        self.assertTrue(raw)
        self.assertNotEqual((st.st_mtime_ns, st.st_size), (now.st_mtime_ns, now.st_size),
                            'the returned stat matches the current file although the bytes are '
                            'stale: moved_since would defer nothing and the cache would lie')

    def test_noop_aborted_snapshot_is_pruned(self):
        self.put(TEAM, entry('t0')); self.put(MAX, entry('a'))
        self.run_sync('sync', CCD_KEEP_SNAPSHOTS='1')
        self.put(MAX, entry('b')); self.run_sync('sync', CCD_KEEP_SNAPSHOTS='1')
        self.put(MAX, entry('d')); self.run_sync('sync', CCD_KEEP_SNAPSHOTS='1')
        self.assertEqual(len(self.snapshots()), 1)

    def test_rollback_rejects_path_traversal(self):
        self.assertIn('invalid snapshot', self.run_sync('rollback', '../x').stderr)

    # --- scheduled tasks ---------------------------------------------------------------------
    def test_tasks_active_org_mirrored_into_inactive_byte_for_byte(self):
        self.put(MAX, entry('a')); self.put(TEAM, entry('a'))
        self.put_tasks(MAX, [task('t1'), task('t2')], sundayAliasBoundaryStamped=True)
        self.active(MAX)
        self.assertEqual(self.run_sync('sync').returncode, 0)
        self.assertEqual(set(self.tasks(TEAM)), {'t1', 't2'})
        raw = open(os.path.join(self.org(TEAM), 'scheduled-tasks.json'), 'rb').read()
        self.assertEqual(raw, json.dumps(json.loads(raw), indent=2, ensure_ascii=False).encode())
        self.assertTrue(json.loads(raw)['sundayAliasBoundaryStamped'])
        before = self.tree_hash()
        self.run_sync('sync')                       # second run: nothing left to do
        self.assertEqual(before, self.tree_hash())

    def test_tasks_empty_active_org_never_wipes_the_other(self):
        """the state found on 2026-10-06: Team active with 0 tasks, Max with all of them."""
        self.put(MAX, entry('a')); self.put(TEAM, entry('b'))
        self.put_tasks(MAX, [task('t1'), task('t2'), task('t3')]); self.put_tasks(TEAM, [])
        self.active(TEAM)
        before = open(os.path.join(self.org(MAX), 'scheduled-tasks.json'), 'rb').read()
        r = self.run_sync('sync')
        self.assertEqual(r.returncode, 0)
        self.assertEqual(before, open(os.path.join(self.org(MAX), 'scheduled-tasks.json'), 'rb').read())
        self.assertIn('0 scheduled tasks', open(os.path.join(self.bk, 'TASKS_HELD')).read())
        self.assertIsNotNone(self.get(TEAM, 'a'))   # the sidebar still synced

    def test_tasks_never_written_while_switching_or_without_log(self):
        self.put(MAX, entry('a')); self.put(TEAM, entry('b')); self.put_tasks(MAX, [task('t1')])
        self.run_sync('sync')                       # no Desktop log at all
        self.assertIsNone(self.tasks(TEAM))
        self.active(MAX, reset=True)                # a switch has started, not finished
        self.run_sync('sync')
        self.assertIsNone(self.tasks(TEAM))
        self.assertIsNotNone(self.get(TEAM, 'a'))

    def test_tasks_run_state_never_moves_backwards(self):
        self.put(MAX, entry('a')); self.put(TEAM, entry('a'))
        self.put_tasks(TEAM, [task('t1', lastScheduledFor='2026-10-06T08:00:00.000Z')])
        self.put_tasks(MAX, [task('t1', lastScheduledFor='2026-10-05T08:00:00.000Z', displayName='renamed')])
        self.active(MAX)                            # Max just loaded a stale copy
        self.run_sync('sync')
        t = self.tasks(TEAM)['t1']
        self.assertEqual((t['lastScheduledFor'], t['displayName']), ('2026-10-06T08:00:00.000Z', 'renamed'))
        # a new schedule resets the run state: the active org's values are taken as they are
        self.put_tasks(MAX, [task('t1', cronExpression='0 11 * * *', lastScheduledFor='2026-10-01T08:00:00.000Z')])
        self.run_sync('sync')
        self.assertEqual(self.tasks(TEAM)['t1']['lastScheduledFor'], '2026-10-01T08:00:00.000Z')

    def test_tasks_retries_of_the_inactive_org_are_dropped(self):
        self.put(MAX, entry('a')); self.put(TEAM, entry('a'))
        self.put_tasks(TEAM, [task('t1')], runRetries={'t1': {'slot': '2026-10-06T08:00:00.000Z', 'attempts': 1,
                                                                'notBefore': 1}})
        self.put_tasks(MAX, [task('t1', displayName='x')]); self.active(MAX)
        self.run_sync('sync')
        self.assertNotIn('runRetries', json.load(open(os.path.join(self.org(TEAM), 'scheduled-tasks.json'))))

    def test_tasks_deletions_capped(self):
        self.put(MAX, entry('a')); self.put(TEAM, entry('a'))
        self.put_tasks(TEAM, [task(f't{i}') for i in range(6)])
        self.put_tasks(MAX, [task(f't{i}') for i in range(5)]); self.active(MAX)
        self.run_sync('sync')                       # one deletion: propagated
        self.assertEqual(len(self.tasks(TEAM)), 5)
        self.put_tasks(MAX, [task('t0')])
        self.run_sync('sync')                       # four at once: held
        self.assertEqual(len(self.tasks(TEAM)), 5)
        self.assertIn('would remove 4', open(os.path.join(self.bk, 'TASKS_HELD')).read())
        self.run_sync('sync', '--allow-bulk')
        self.assertEqual(set(self.tasks(TEAM)), {'t0'})
        self.assertFalse(os.path.exists(os.path.join(self.bk, 'TASKS_HELD')))

    def test_tasks_rollback_restores_bytes_only_into_the_inactive_org(self):
        self.put(MAX, entry('a')); self.put(TEAM, entry('a'))
        self.put_tasks(TEAM, [task('old')]); self.put_tasks(MAX, [task('old'), task('new')])
        self.active(MAX)
        before = open(os.path.join(self.org(TEAM), 'scheduled-tasks.json'), 'rb').read()
        self.run_sync('sync')
        ts = self.snapshots()[-1]
        self.active(TEAM)                           # Team is now the active org: its file is Desktop's
        self.assertIn('active org', self.run_sync('rollback', ts).stderr)
        self.active(MAX)
        self.assertEqual(self.run_sync('rollback', ts).returncode, 0)
        self.assertEqual(before, open(os.path.join(self.org(TEAM), 'scheduled-tasks.json'), 'rb').read())

    def test_tasks_dry_run_lists_catch_ups_and_writes_nothing(self):
        self.put(MAX, entry('a')); self.put(TEAM, entry('a'))
        now = time.time()
        iso = lambda t: time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime(t))
        self.put_tasks(MAX, [task('missed', cronExpression='* * * * *', lastScheduledFor=iso(now - 600)),
                             task('fresh', cronExpression='0 0 1 1 *', lastScheduledFor=iso(now - 600)),
                             task('off', enabled=False, cronExpression='* * * * *'),
                             task('once', cronExpression=None, fireAt=int((now - 60) * 1000))])
        self.active(MAX)
        before = self.tree_hash()
        r = self.run_sync('sync', '--dry-run')
        self.assertEqual(before, self.tree_hash())
        out = json.loads(r.stdout[:r.stdout.index('}\n') + 1])
        self.assertEqual(sorted(out['tasks_run_at_once_on_switch_to_TEAM']), ['missed', 'once'])


if __name__ == '__main__':
    unittest.main()
