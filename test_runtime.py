from contextlib import ExitStack, redirect_stderr, redirect_stdout
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from integrations.todoist import runtime, paths
from integrations.todoist.brief import render_brief
from integrations.todoist.completion_events import append_events, TaskCompletionEvent
from integrations.todoist.sync import sync
from test_task_selection import PROVENANCE, task
from test_brief import HEADER


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / 'state'
        self.snapshot = self.root / 'external/todoist'
        self.context = self.root / 'strategic-context.json'
        self.history = self.root / 'todoist-history.sqlite3'
        self.resources = dict(projects=[dict(id='p', name='Project', child_order=1)], sections=[],
                              tasks=[self.row('parent', 'Fix truck', child_order=1),
                                     self.row('leaf', 'Check codes', parent_id='parent'),
                                     self.row('next', 'Other work', child_order=2)])
        self.publish()
        self.context.write_text(json.dumps({'why':'Make time for meaningful work.',
                                           'direction':'Build a useful project.'}))
        append_events(self.history, [])
        self.kwargs = dict(snapshot_path=self.snapshot, context_path=self.context)

    def row(self, ident, title, **changes):
        return dict(dict(id=ident, content=title, project_id='p', section_id=None, parent_id=None,
                         checked=False, labels=[], due=None, deadline=None, duration=None), **changes)

    def publish(self):
        with patch('integrations.todoist.sync.fetch_all',
                   side_effect=lambda resource, *args: (self.resources[resource], 1)):
            sync('fixture-token', self.snapshot)

    def cli(self, argv=None):
        out, err = io.StringIO(), io.StringIO()
        with patch.object(paths, 'TODOIST_EXTERNAL_STATE_ROOT', self.snapshot), \
             patch.object(paths, 'STRATEGIC_CONTEXT_PATH', self.context), \
             redirect_stdout(out), redirect_stderr(err):
            code = runtime.main(['brief'] if argv is None else argv)
        return code, out.getvalue(), err.getvalue()

    def contents(self):
        return {str(p.relative_to(self.root)): ('link', str(p.readlink())) if p.is_symlink()
                else ('file', p.read_bytes(), p.stat().st_mtime_ns) if p.is_file()
                else ('directory',) for p in self.root.rglob('*')}

    def assert_failure(self, message):
        code, out, err = self.cli()
        self.assertEqual((code, out), (1, ''))
        self.assertEqual(err, 'GHOST ERROR\n' + message + '\n')

    def test_full_pipeline_exact_cli(self):
        expected = HEADER + 'Check codes\n\nUNDER\nFix truck\n\nNEXT\n- Other work\n'
        self.assertEqual(self.cli(), (0, expected, ''))
        self.assertEqual(render_brief(runtime.run_brief(**self.kwargs).brief), expected)

    def test_no_registry_or_links_required_and_new_sync_is_sufficient(self):
        self.assertFalse((self.root / 'goals.json').exists())
        self.assertEqual(runtime.run_brief(**self.kwargs).task_selection.primary.source_id, 'leaf')
        context_before = self.context.read_bytes()
        self.resources['tasks'].append(self.row('new', 'New unlinked task', child_order=0))
        self.publish()
        self.assertEqual(runtime.run_brief(**self.kwargs).task_selection.primary.source_id, 'new')
        self.resources['tasks'].append(self.row('new-child', 'New unlinked subtask', parent_id='new'))
        self.publish()
        self.assertEqual(runtime.run_brief(**self.kwargs).task_selection.primary.source_id, 'new-child')
        self.assertEqual(self.context.read_bytes(), context_before)
        self.assertFalse((self.root / 'goals.json').exists())

    def test_legacy_registry_not_read(self):
        (self.root / 'goals.json').write_text('malformed unused legacy data')
        self.assertEqual(self.cli()[0], 0)

    def test_history_does_not_affect_actionability(self):
        before = self.cli()
        append_events(self.history, [TaskCompletionEvent(task('leaf').id, PROVENANCE.synced_at,
                      'Historical completion', None, None, None, 'todoist', {})])
        self.assertEqual(self.cli(), before)
        self.history.write_bytes(b'malformed history is not operational state')
        self.assertEqual(self.cli(), before)
        self.history.unlink()
        self.assertEqual(self.cli(), before)
        self.assertFalse(self.history.exists())

    def test_missing_snapshot(self):
        self.snapshot.unlink()
        self.assert_failure('Todoist snapshot unavailable or invalid')

    def test_malformed_snapshot(self):
        (self.snapshot / 'tasks.json').write_text('{')
        self.assert_failure('Todoist snapshot unavailable or invalid')

    def test_missing_and_malformed_context(self):
        self.context.unlink()
        self.assert_failure('Strategic context unavailable or invalid')
        self.context.write_text('{}')
        self.assert_failure('Strategic context unavailable or invalid')

    def test_normalization_failure(self):
        self.resources['tasks'][0]['priority'] = 99
        self.publish()
        self.assert_failure('Normalization failed')

    def test_malformed_hierarchy_error_boundary(self):
        for parent in ('missing', 'leaf'):
            self.resources['tasks'][0]['parent_id'] = parent
            self.publish()
            self.assert_failure('Todoist snapshot unavailable or invalid')

    def test_malformed_order_error_boundary(self):
        self.resources['tasks'][0]['child_order'] = 'bad'
        self.publish()
        self.assert_failure('Invalid provider ordering integer')

    def test_no_network_or_ingestion(self):
        with ExitStack() as stack:
            for name in ['socket.socket.connect', 'socket.create_connection',
                         'urllib.request.urlopen', 'urllib.request.OpenerDirector.open',
                         'integrations.todoist.sync.sync', 'integrations.todoist.history.ingest_history',
                         'integrations.todoist.completion_events.load_events']:
                stack.enter_context(patch(name, side_effect=AssertionError('Forbidden network/history')))
            self.assertEqual(self.cli()[0], 0)

    def test_no_writes_and_repeatability(self):
        before = self.contents()
        self.assertEqual(self.cli(), self.cli())
        self.assertEqual(self.contents(), before)

    def test_normalized_content_is_used(self):
        normalize = runtime.normalize_todoist_snapshot
        def changed(snapshot):
            current = normalize(snapshot)
            return replace(current, tasks=tuple(replace(t, title='Normalized '+t.title) for t in current.tasks))
        with patch.object(runtime, 'normalize_todoist_snapshot', side_effect=changed):
            self.assertIn('PRIMARY ORDER\nNormalized Check codes\n', self.cli()[1])

    def test_old_snapshot_accepted(self):
        path = self.snapshot / 'sync_metadata.json'
        data = json.loads(path.read_text())
        data['synced_at'] = '2000-01-01T00:00:00Z'
        path.write_text(json.dumps(data))
        self.assertEqual(self.cli()[0], 0)

    def test_completed_child_changes_primary_to_parent_after_sync(self):
        self.resources['tasks'][1]['checked'] = True
        self.publish()
        self.assertEqual(runtime.run_brief(**self.kwargs).task_selection.primary.source_id, 'parent')

    def test_completion_affirmatives_and_local_state_unchanged(self):
        primary = runtime.run_brief(**self.kwargs).task_selection.primary
        before = self.contents()
        for answer in ('y', 'yes', 'Y', 'YeS'):
            with self.subTest(answer=answer), patch('builtins.input', return_value=answer) as prompt, \
                 patch.dict('os.environ', {'TODOIST_API_TOKEN':'fixture-token'}), \
                 patch.object(runtime, 'close_task') as close, \
                 patch('integrations.todoist.sync.sync', side_effect=AssertionError('No sync')), \
                 patch('integrations.todoist.history.ingest_history', side_effect=AssertionError('No history')):
                code, out, err = self.cli(['complete'])
                self.assertEqual((code, err), (0, ''))
                self.assertIn('PRIMARY ORDER\n'+primary.title+'\n', out)
                prompt.assert_called_once_with('Mark this task complete in Todoist? [y/N] ')
                close.assert_called_once_with('fixture-token', primary.source_id)
                self.assertEqual(self.contents(), before)

    def test_completion_cancellation_has_no_write(self):
        before = self.contents()
        for answer in ('', 'n', 'no', 'anything', 'true'):
            with self.subTest(answer=answer), patch('builtins.input', return_value=answer), \
                 patch.object(runtime, 'close_task') as close, \
                 patch('integrations.todoist.todoist_write.build_opener') as network:
                self.assertEqual(self.cli(['complete']),
                                 (0, 'PRIMARY ORDER\nCheck codes\n\nCompletion cancelled.\n', ''))
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
        self.resources['tasks'] = []
        self.publish()
        with patch.object(runtime, 'close_task') as close, patch('builtins.input') as prompt:
            self.assertEqual(self.cli(['complete']), (1, '', 'GHOST ERROR\nNo PRIMARY ORDER to complete\n'))
            close.assert_not_called()
            prompt.assert_not_called()

    def test_completion_error_boundary(self):
        with patch('builtins.input', return_value='y'), \
             patch.object(runtime, 'close_task', side_effect=runtime.TodoistWriteError('HTTP failure')):
            code, out, err = self.cli(['complete'])
            self.assertEqual((code, err), (1, 'GHOST ERROR\nHTTP failure\n'))
            self.assertNotIn('Completed in Todoist.', out)

    def test_completion_missing_token_before_network(self):
        with patch.dict('os.environ', {}, clear=True), patch('builtins.input', return_value='y'), \
             patch('integrations.todoist.todoist_write.build_opener') as network:
            self.assertIn('GHOST ERROR\nTODOIST_API_TOKEN', self.cli(['complete'])[2])
            network.assert_not_called()

    def test_bad_state_prevents_confirmation_and_write(self):
        self.context.unlink()
        with patch.object(runtime, 'close_task') as close, patch('builtins.input') as prompt:
            self.assertEqual(self.cli(['complete'])[0], 1)
            close.assert_not_called()
            prompt.assert_not_called()

    def test_complete_dispatch_and_argument_rejection(self):
        with patch.object(runtime, 'run_complete') as complete:
            self.assertEqual(self.cli(['complete']), (0, '', ''))
            complete.assert_called_once_with()
        with patch.object(runtime, 'run_complete') as complete:
            with self.assertRaises(SystemExit):
                self.cli(['complete', 'foo'])
            complete.assert_not_called()

    def test_executable_and_module_entrypoints(self):
        home = self.root.parent / 'home'
        (home / 'ghost/integrations').mkdir(parents=True)
        (home / 'ghost/integrations/state').symlink_to(self.root, target_is_directory=True)
        env = dict(os.environ, HOME=str(home), PYTHONDONTWRITEBYTECODE='1')
        for command in ([str(Path(__file__).with_name('ghost'))],
                        [sys.executable, '-m', 'integrations.todoist.runtime']):
            for args in (['brief'], ['complete'], ['--help']):
                with self.subTest(command=command, args=args):
                    result = subprocess.run(command + args, cwd=Path(__file__).resolve().parents[2],
                                            env=env, input='n\n', capture_output=True, text=True)
                    self.assertEqual((result.returncode, result.stderr), (0, ''))
                    if args == ['brief']:
                        self.assertEqual(result.stdout, self.cli()[1])
                    elif args == ['complete']:
                        self.assertIn('Completion cancelled.', result.stdout)


class CreateCommandTests(unittest.TestCase):
    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = runtime.main(['create', *args])
        return code, out.getvalue(), err.getvalue()

    def test_explicit_yes_json_and_no_snapshot_or_sync(self):
        from integrations.todoist.todoist_write import CreatedTask
        with patch.object(runtime, 'create_task', return_value=CreatedTask('actual', 'Parent', None)) as create, \
             patch.dict(os.environ, {'TODOIST_API_TOKEN':'fixture-token'}), \
             patch.object(runtime, 'run_brief', side_effect=AssertionError('No snapshot access')), \
             patch('integrations.todoist.sync.sync', side_effect=AssertionError('No sync')), \
             patch('integrations.todoist.completion_events.append_events', side_effect=AssertionError('No history')), \
             patch('builtins.input', side_effect=AssertionError('Already authorized')):
            code, out, err = self.cli('Parent', '--yes')
            self.assertEqual((code, err), (0, ''))
            self.assertEqual(json.loads(out), {'id':'actual', 'content':'Parent', 'parent_id':None})
            create.assert_called_once_with('fixture-token', 'Parent', parent_id=None, description=None,
                project_id=None, section_id=None, due_string=None, due_date=None, priority=None)

    def test_optional_arguments(self):
        from integrations.todoist.todoist_write import CreatedTask
        with patch.object(runtime, 'create_task', return_value=CreatedTask('child', 'Child', '123')) as create:
            self.assertEqual(self.cli('Child', '--yes', '--parent', '123', '--description', 'Details',
                '--project', 'p', '--section', 's', '--due-date', '2026-09-30', '--priority', '4')[0], 0)
            self.assertEqual(create.call_args.kwargs, dict(parent_id='123', description='Details',
                project_id='p', section_id='s', due_string=None, due_date='2026-09-30', priority=4))

    def test_confirmation_required_and_json_stdout_only(self):
        from integrations.todoist.todoist_write import CreatedTask
        with patch('builtins.input', return_value='yes') as prompt, \
             patch.object(runtime, 'create_task', return_value=CreatedTask('new', 'Child', '123')) as create:
            code, out, err = self.cli('Child', '--parent', '123')
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out)['id'], 'new')
            self.assertIn('PARENT\n123', err)
            self.assertIn('Create this task in Todoist? [y/N]', err)
            prompt.assert_called_once()
            create.assert_called_once()

    def test_cancellation_no_write(self):
        for answer in ('', 'n', 'no', 'true'):
            with patch('builtins.input', return_value=answer), patch.object(runtime, 'create_task') as create:
                code, out, err = self.cli('Parent')
                self.assertEqual((code, out), (0, ''))
                self.assertIn('Creation cancelled.', err)
                create.assert_not_called()

    def test_eof_interrupt_no_write(self):
        for error in (EOFError, KeyboardInterrupt):
            with patch('builtins.input', side_effect=error), patch.object(runtime, 'create_task') as create:
                self.assertEqual(self.cli('Parent')[1], '')
                create.assert_not_called()

    def test_failure_has_no_success_json(self):
        with patch.object(runtime, 'create_task', side_effect=runtime.TodoistWriteError('HTTP 400')):
            self.assertEqual(self.cli('Parent', '--yes'), (1, '', 'GHOST ERROR\nHTTP 400\n'))

    def test_invalid_cli_arguments(self):
        for args in ([], ['Task','--priority','5'], ['Task','--due-date','2026-09-30','--due-string','tomorrow']):
            with patch.object(runtime, 'create_task') as create, self.assertRaises(SystemExit):
                self.cli(*args)
            create.assert_not_called()

    def test_missing_token_never_sends_request(self):
        with patch.dict(os.environ, {}, clear=True), patch('integrations.todoist.todoist_write.build_opener') as network:
            code, out, err = self.cli('Parent', '--yes')
            self.assertEqual((code, out), (1, ''))
            self.assertIn('TODOIST_API_TOKEN', err)
            network.assert_not_called()

    def test_executable_cancellation_without_local_state(self):
        with tempfile.TemporaryDirectory() as home:
            result = subprocess.run([str(Path(__file__).with_name('ghost')), 'create', 'Parent'],
                input='n\n', capture_output=True, text=True, env=dict(os.environ, HOME=home))
            self.assertEqual((result.returncode, result.stdout), (0, ''))
            self.assertIn('Creation cancelled.', result.stderr)
            self.assertEqual(list(Path(home).iterdir()), [])
