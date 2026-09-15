from contextlib import ExitStack, redirect_stderr, redirect_stdout
from dataclasses import FrozenInstanceError, replace
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from integrations.todoist import runtime, paths
from integrations.todoist.brief import BriefError, render_brief
from integrations.todoist.completion_events import append_events
from integrations.todoist.goal_store import save_goal_registry
from integrations.todoist.goals import GoalRegistry, Status
from integrations.todoist.normalize import normalize_todoist_snapshot
from integrations.todoist.objective import ObjectiveSelectionError
from integrations.todoist.progress import ProjectionError
from integrations.todoist.sync import sync
from test_brief import fixture, PRIMARY, BLOCKED, QUEUED
from test_progress import event


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / 'state'
        self.snapshot = self.root / 'external/todoist'
        self.goals = self.root / 'goals.json'
        self.history = self.root / 'todoist-history.sqlite3'
        self.registry, current = fixture()
        resources = dict(projects=[dict(id='p', name='Unrelated provider project')], sections=[],
                         tasks=[dict(id=t.id.source_id, content=t.title, project_id='p',
                                     section_id=None, parent_id=None, checked=False, labels=[],
                                     due=None, deadline=None, duration=None) for t in current.tasks])
        with patch('integrations.todoist.sync.fetch_all',
                   side_effect=lambda resource, *args: (resources[resource], 1)):
            sync('fixture-token', self.snapshot)
        save_goal_registry(self.registry, self.goals)
        append_events(self.history, [event('write_methods')])
        self.kwargs = dict(snapshot_path=self.snapshot, registry_path=self.goals,
                           history_path=self.history)

    def cli(self, argv=None):
        out, err = io.StringIO(), io.StringIO()
        with patch.object(paths, 'TODOIST_EXTERNAL_STATE_ROOT', self.snapshot), \
             patch.object(paths, 'GOAL_REGISTRY_PATH', self.goals), \
             patch.object(paths, 'TODOIST_HISTORY_DB_PATH', self.history), \
             redirect_stdout(out), redirect_stderr(err):
            code = runtime.main(['brief'] if argv is None else argv)
        return code, out.getvalue(), err.getvalue()

    def assert_failure(self, message):
        with self.assertRaises(runtime.BriefRunError) as caught:
            runtime.run_brief(**self.kwargs)
        self.assertIsNotNone(caught.exception.__cause__)
        code, out, err = self.cli()
        self.assertEqual(code, 1)
        self.assertEqual(out, '')
        self.assertTrue(err.startswith('GHOST ERROR\n'))
        self.assertIn(message, err)
        self.assertNotIn('Traceback', err)

    def test_full_pipeline_and_exact_cli(self):
        run = runtime.run_brief(**self.kwargs)
        self.assertEqual(run.registry, self.registry)
        self.assertEqual(len(run.completion_events), 1)
        self.assertEqual(run.projection.goals[0].completion_event_count, 1)
        self.assertEqual(run.selection.objective.milestone_id, 'manuscript')
        self.assertEqual(render_brief(run.brief), PRIMARY + BLOCKED + QUEUED)
        self.assertEqual(self.cli(), (0, PRIMARY + BLOCKED + QUEUED, ''))
        with self.assertRaises(FrozenInstanceError):
            run.brief = None

    def test_missing_snapshot(self):
        self.snapshot.unlink()
        self.assert_failure('Todoist snapshot unavailable')

    def test_malformed_snapshot(self):
        (self.snapshot / 'tasks.json').write_text('{')
        self.assert_failure('Todoist snapshot unavailable or invalid')

    def test_missing_registry(self):
        self.goals.unlink()
        self.assert_failure('Goal registry not found')

    def test_malformed_registry(self):
        self.goals.write_text('{}')
        self.assert_failure('Goal registry unavailable or invalid')

    def test_missing_history(self):
        self.history.unlink()
        self.assert_failure('Completion history unavailable or invalid')
        self.assertFalse(self.history.exists())

    def test_malformed_history(self):
        self.history.write_bytes(b'not sqlite')
        self.assert_failure('Completion history unavailable or invalid')

    def test_invalid_history_schema_and_event(self):
        with sqlite3.connect(self.history) as db:
            db.execute("UPDATE task_completion_events SET completed_at = 'bad'")
        self.assert_failure('Completion history unavailable or invalid')
        with sqlite3.connect(self.history) as db:
            db.execute('DROP TABLE task_completion_events')
        self.assert_failure('Completion history unavailable or invalid')

    def test_normalization_failure(self):
        path = self.snapshot / 'tasks.json'
        rows = json.loads(path.read_text())
        rows[0]['priority'] = 99
        path.write_text(json.dumps(rows))
        self.assert_failure('Normalization failed')

    def test_domain_error_boundaries(self):
        for name, error in [('project_progress', ProjectionError),
                            ('select_objective', ObjectiveSelectionError),
                            ('build_brief', BriefError)]:
            with self.subTest(stage=name), patch.object(runtime, name, side_effect=error('inconsistent')):
                self.assert_failure(f'{error.__name__}: inconsistent')

    def test_no_network_or_ingestion(self):
        with ExitStack() as stack:
            for name in ['socket.socket.connect', 'socket.create_connection',
                         'urllib.request.urlopen', 'urllib.request.OpenerDirector.open',
                         'integrations.todoist.sync.build_opener',
                         'integrations.todoist.history.build_opener',
                         'integrations.todoist.sync.sync',
                         'integrations.todoist.history.ingest_history']:
                stack.enter_context(patch(name, side_effect=AssertionError('Forbidden network/sync')))
            self.assertEqual(self.cli(), (0, PRIMARY + BLOCKED + QUEUED, ''))

    def contents(self):
        return {str(p.relative_to(self.root)): ('link', str(p.readlink())) if p.is_symlink()
                else ('file', p.read_bytes(), p.stat().st_mtime_ns) if p.is_file()
                else ('directory',) for p in self.root.rglob('*')}

    def test_no_writes_and_repeatability(self):
        before = self.contents()
        first = self.cli()
        self.assertEqual(first, self.cli())
        self.assertEqual(first[0], 0)
        self.assertEqual(before, self.contents())

    def test_centralized_defaults(self):
        with patch.object(runtime, 'load_snapshot', wraps=runtime.load_snapshot) as snapshot, \
             patch.object(runtime, 'load_goal_registry', wraps=runtime.load_goal_registry) as registry, \
             patch.object(runtime, 'load_events', wraps=runtime.load_events) as history:
            self.assertEqual(self.cli()[0], 0)
            snapshot.assert_called_once_with(self.snapshot)
            registry.assert_called_once_with(self.goals)
            history.assert_called_once_with(self.history)

    def test_normalized_content_is_used(self):
        def normalize(snapshot):
            state = normalize_todoist_snapshot(snapshot)
            return replace(state, tasks=tuple(replace(t, title='Normalized ' + t.title)
                                              for t in state.tasks))
        with patch.object(runtime, 'normalize_todoist_snapshot', side_effect=normalize):
            self.assertIn('PRIMARY ORDER\nNormalized Write methods\n', self.cli()[1])

    def test_explicit_empty_registry(self):
        save_goal_registry(GoalRegistry(), self.goals)
        self.assertEqual(self.cli(), (0, 'GHOST BRIEF\n\nPRIMARY ORDER\nNone\n\nNo actionable work.\n', ''))

    def test_blocked_without_objective(self):
        registry = replace(self.registry, milestones=tuple(replace(m, status=Status.BLOCKED)
                                                           for m in self.registry.milestones))
        save_goal_registry(registry, self.goals)
        self.assertEqual(self.cli(), (0, 'GHOST BRIEF\n\nPRIMARY ORDER\nNone\n\nBLOCKED\n'
                                     '- Thesis — Experimental validation\n- Thesis — Manuscript\n'
                                     '- Revenue — Portfolio offer\n', ''))

    def test_old_snapshot_is_exposed_and_accepted(self):
        path = self.snapshot / 'sync_metadata.json'
        metadata = json.loads(path.read_text())
        metadata['synced_at'] = '2000-01-01T00:00:00Z'
        path.write_text(json.dumps(metadata))
        run = runtime.run_brief(**self.kwargs)
        self.assertEqual(run.snapshot.metadata['synced_at'], metadata['synced_at'])
        self.assertNotIn('2000', render_brief(run.brief))

    def test_executable_and_module_entrypoints(self):
        # A fresh process uses fixture defaults by changing HOME, never ARK state.
        import os
        home = self.root.parent / 'home'
        (home / 'ghost/integrations').mkdir(parents=True)
        (home / 'ghost/integrations/state').symlink_to(self.root, target_is_directory=True)
        env = dict(os.environ, HOME=str(home), PYTHONDONTWRITEBYTECODE='1')
        for command in [[str(Path(__file__).with_name('ghost'))],
                        [sys.executable, '-m', 'integrations.todoist.runtime']]:
            with self.subTest(command=command):
                result = subprocess.run(command + ['brief'], cwd=Path(__file__).resolve().parents[2],
                                        env=env, capture_output=True, text=True)
                self.assertEqual((result.returncode, result.stdout, result.stderr),
                                 (0, PRIMARY + BLOCKED + QUEUED, ''))
                result = subprocess.run(command + ['complete'], cwd=Path(__file__).resolve().parents[2],
                                        env=env, input='n\n', capture_output=True, text=True)
                self.assertEqual((result.returncode, result.stdout, result.stderr),
                                 (0, 'PRIMARY ORDER\nWrite methods\n\n'
                                  'Mark this task complete in Todoist? [y/N] Completion cancelled.\n', ''))


    def test_malformed_hierarchy_error_boundary(self):
        for collection, field in [('directions', 'why_id'), ('goals', 'direction_id')]:
            save_goal_registry(self.registry, self.goals)
            payload = json.loads(self.goals.read_text())
            payload[collection][0][field] = 'unknown'
            self.goals.write_text(json.dumps(payload))
            before = self.contents()
            self.assert_failure('Goal registry unavailable or invalid')
            self.assertEqual(self.contents(), before)

    def test_extended_pipeline_ancestry(self):
        run = runtime.run_brief(**self.kwargs)
        self.assertEqual(run.projection.goals[0].direction_id, 'd')
        self.assertEqual(run.projection.goals[0].why_id, 'w')
        self.assertEqual(run.registry.why_for_goal(run.selection.objective.goal_id).id, 'w')
        self.assertEqual(run.brief.primary.why_title, 'Keep growing')
        self.assertEqual(run.brief.primary.direction_title, 'Learning')


    def test_completion_affirmatives_and_local_state_unchanged(self):
        primary = runtime.run_brief(**self.kwargs).task_selection.primary
        before = self.contents()
        for answer in ('y', 'yes', 'Y', 'YeS'):
            with self.subTest(answer=answer), \
                 patch('builtins.input', return_value=answer) as prompt, \
                 patch.dict('os.environ', {'TODOIST_API_TOKEN': 'fixture-token'}), \
                 patch.object(runtime, 'close_task') as close, \
                 patch('integrations.todoist.sync.sync', side_effect=AssertionError('No sync')), \
                 patch('integrations.todoist.history.ingest_history', side_effect=AssertionError('No ingestion')):
                code, out, err = self.cli(['complete'])
                self.assertEqual((code, err), (0, ''))
                self.assertEqual(out, f'PRIMARY ORDER\n{primary.title}\n\n'
                                 'Completed in Todoist.\nRun `sync` to refresh GHOST state.\n')
                prompt.assert_called_once_with('Mark this task complete in Todoist? [y/N] ')
                close.assert_called_once_with('fixture-token', primary.id.source_id)
                self.assertEqual(self.contents(), before)

    def test_completion_cancellation_has_no_write(self):
        before = self.contents()
        for answer in ('', 'n', 'no', 'anything', 'true'):
            with self.subTest(answer=answer), patch('builtins.input', return_value=answer), \
                 patch.object(runtime, 'close_task') as close, \
                 patch('integrations.todoist.todoist_write.build_opener') as network:
                self.assertEqual(self.cli(['complete']),
                                 (0, 'PRIMARY ORDER\nWrite methods\n\nCompletion cancelled.\n', ''))
                close.assert_not_called()
                network.assert_not_called()
                self.assertEqual(self.contents(), before)

    def test_completion_eof_and_interrupt_cancel(self):
        for error in (EOFError, KeyboardInterrupt):
            with self.subTest(error=error), patch('builtins.input', side_effect=error), \
                 patch.object(runtime, 'close_task') as close:
                self.assertIn('Completion cancelled.', self.cli(['complete'])[1])
                close.assert_not_called()

    def test_completion_no_primary(self):
        save_goal_registry(GoalRegistry(), self.goals)
        with patch.object(runtime, 'close_task') as close, patch('builtins.input') as prompt:
            self.assertEqual(self.cli(['complete']),
                             (1, '', 'GHOST ERROR\nNo PRIMARY ORDER to complete\n'))
            close.assert_not_called()
            prompt.assert_not_called()

    def test_completion_error_boundary(self):
        with patch('builtins.input', return_value='y'), \
             patch.object(runtime, 'close_task', side_effect=runtime.TodoistWriteError('HTTP failure')):
            code, out, err = self.cli(['complete'])
            self.assertEqual(code, 1)
            self.assertEqual(err, 'GHOST ERROR\nHTTP failure\n')
            self.assertNotIn('Completed in Todoist.', out)
        self.goals.unlink()
        with patch.object(runtime, 'close_task') as close:
            code, out, err = self.cli(['complete'])
            self.assertEqual((code, out), (1, ''))
            self.assertTrue(err.startswith('GHOST ERROR\nGoal registry not found'))
            close.assert_not_called()

    def test_completion_missing_token_before_network(self):
        with patch.dict('os.environ', {}, clear=True), patch('builtins.input', return_value='y'), \
             patch('integrations.todoist.todoist_write.build_opener') as network:
            code, out, err = self.cli(['complete'])
            self.assertEqual(code, 1)
            self.assertIn('GHOST ERROR\nTODOIST_API_TOKEN', err)
            network.assert_not_called()

    def test_complete_dispatch_and_argument_rejection(self):
        with patch.object(runtime, 'run_complete') as complete:
            self.assertEqual(self.cli(['complete']), (0, '', ''))
            complete.assert_called_once_with()
        with patch.object(runtime, 'run_complete') as complete:
            with self.assertRaises(SystemExit) as caught:
                self.cli(['complete', 'foo'])
            self.assertEqual(caught.exception.code, 2)
            complete.assert_not_called()

    def test_completion_executable_help_and_rejection(self):
        executable = str(Path(__file__).with_name('ghost'))
        for args, expected in [(['--help'], 0), (['complete', '--help'], 0),
                               (['complete', 'foo'], 2)]:
            with self.subTest(args=args):
                result = subprocess.run([executable] + args, capture_output=True, text=True)
                self.assertEqual(result.returncode, expected)
                self.assertIn('complete', result.stdout + result.stderr)
