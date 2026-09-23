"""End-to-end tests for bin/ccd-org-sync against a fake Claude Desktop tree (python3 -m unittest)."""
import glob, hashlib, json, os, subprocess, tempfile, time, unittest

SCRIPT = os.path.join(os.path.dirname(__file__), '..', 'bin', 'ccd-org-sync')
ACCT = '11111111-0000-0000-0000-000000000000'
MAX = f'{ACCT}/aaaaaaaa-0000-0000-0000-000000000000'
TEAM = f'{ACCT}/bbbbbbbb-0000-0000-0000-000000000000'


def entry(n, act=1000, focus=None, **kw):
    e = {'sessionId': f'local_{n}', 'cliSessionId': f'cli-{n}', 'cwd': '/p/alpha',
         'createdAt': 1, 'lastActivityAt': act, 'title': 't'}
    if focus is not None:
        e['lastFocusedAt'] = focus
    e.update(kw)
    return e


class OrgSyncTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = os.path.join(self.tmp, 'Claude')
        self.proj = os.path.join(self.tmp, 'projects', '-p-alpha')
        self.bk = os.path.join(self.tmp, 'backups')
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
                 CCD_BACKUP_DIR=self.bk, CCD_QUIET_SECONDS='0', CCD_NO_NOTIFY='1',
                 CCD_PAIR=f'MAX={MAX},TEAM={TEAM}')
        e.update(env)
        return subprocess.run(['python3', SCRIPT, *args], env=e, capture_output=True, text=True)

    def tree_hash(self):
        h = hashlib.sha256()
        for p in sorted(glob.glob(os.path.join(self.root, '**'), recursive=True)):
            if os.path.isfile(p):
                h.update(p.encode() + open(p, 'rb').read())
        return h.hexdigest()

    def snapshots(self):
        return sorted(d for d in os.listdir(self.bk) if os.path.isdir(os.path.join(self.bk, d)))

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

    def test_ambiguous_conflict_aborts_and_blocks(self):
        self.put(MAX, entry('c', title='x')); self.put(TEAM, entry('c', title='y'))
        self.put(MAX, entry('a'))
        before = self.tree_hash()
        r = self.run_sync('sync')
        self.assertEqual(r.returncode, 1); self.assertIn('conflict', r.stderr)
        self.assertEqual(before, self.tree_hash())          # nothing written, not even 'a'
        self.assertTrue(os.path.exists(os.path.join(self.bk, 'BLOCKED')))
        self.assertEqual(self.run_sync('sync').returncode, 1)  # stays blocked
        self.run_sync('unblock')
        self.assertIn('conflict', self.run_sync('sync').stderr)

    def test_background_pr_refresh_newer_file_wins(self):
        self.put(MAX, entry('c', prs=[{'state': 'OPEN'}]))
        self.put(TEAM, entry('c', prs=[{'state': 'MERGED'}]))
        t = os.path.getmtime(os.path.join(self.org(MAX), 'local_c.json'))
        os.utime(os.path.join(self.org(MAX), 'local_c.json'), (t - 100, t - 100))
        r = self.run_sync('sync'); self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.get(MAX, 'c')['prs'], [{'state': 'MERGED'}])

    def test_background_field_plus_other_field_still_aborts(self):
        self.put(MAX, entry('c', prs=[1], title='x'))
        self.put(TEAM, entry('c', prs=[2], title='y'))
        os.utime(os.path.join(self.org(MAX), 'local_c.json'), (1, 1))
        self.assertIn('conflict', self.run_sync('sync').stderr)

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

    def test_prune_keeps_pinned(self):
        self.put(TEAM, entry('t0')); self.put(MAX, entry('a'))
        self.run_sync('sync')
        pinned = self.snapshots()[0]
        open(os.path.join(self.bk, pinned, 'PINNED'), 'w').close()
        for i in range(3):
            self.put(MAX, entry(f'x{i}'))
            self.run_sync('sync', CCD_KEEP_SNAPSHOTS='1')
        self.assertIn(pinned, self.snapshots())

    def test_rollback_rejects_path_traversal(self):
        self.assertIn('invalid snapshot', self.run_sync('rollback', '../x').stderr)


if __name__ == '__main__':
    unittest.main()
