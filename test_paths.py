"""Check import-time defaults in fresh processes with an isolated HOME."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class PathTests(unittest.TestCase):
    def run_isolated(self, script):
        with tempfile.TemporaryDirectory() as home:
            result = subprocess.run(
                [sys.executable, '-c', script], cwd=Path(__file__).parent,
                env=dict(os.environ, HOME=home), capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(list(Path(home).iterdir()), [])

    def test_path_contract_and_pure_import(self):
        self.run_isolated('''
import os
from pathlib import Path
from paths import (INTEGRATIONS_ROOT, STATE_ROOT, TODOIST_EXTERNAL_STATE_ROOT,
                   GOAL_REGISTRY_PATH, TODOIST_HISTORY_DB_PATH)
root = Path(os.environ['HOME']) / 'ghost/integrations'
assert INTEGRATIONS_ROOT == root
assert STATE_ROOT == root / 'state'
assert TODOIST_EXTERNAL_STATE_ROOT == root / 'state/external/todoist'
assert GOAL_REGISTRY_PATH == root / 'state/goals.json'
assert TODOIST_HISTORY_DB_PATH == root / 'state/todoist-history.sqlite3'
''')

    def test_consumers_use_central_defaults(self):
        self.run_isolated('''
import inspect
from pathlib import Path
import sys
import paths
import goal_store
import reader
import sync
sys.path.insert(0, str(Path.cwd().parents[1]))
from integrations.todoist import history, paths as package_paths
from integrations.todoist import goal_store as package_store
from integrations.todoist import reader as package_reader, sync as package_sync
for module in (goal_store, package_store):
    assert module.DEFAULT_PATH == paths.GOAL_REGISTRY_PATH
    for function in (module.load_goal_registry, module.save_goal_registry):
        assert inspect.signature(function).parameters['path'].default == paths.GOAL_REGISTRY_PATH
for module in (sync, package_sync):
    assert module.DEFAULT_OUTPUT == paths.TODOIST_EXTERNAL_STATE_ROOT
    assert inspect.signature(module.sync).parameters['output'].default == paths.TODOIST_EXTERNAL_STATE_ROOT
for module in (reader, package_reader):
    assert inspect.signature(module.load_snapshot).parameters['current'].default == paths.TODOIST_EXTERNAL_STATE_ROOT
assert history.DEFAULT_HISTORY == paths.TODOIST_HISTORY_DB_PATH
assert inspect.signature(history.ingest_history).parameters['path'].default == paths.TODOIST_HISTORY_DB_PATH
assert package_paths.STATE_ROOT == paths.STATE_ROOT
''')

    def test_cli_does_not_fall_back_to_old_registry(self):
        with tempfile.TemporaryDirectory() as home:
            old = Path(home) / 'ghost/state/goals.json'
            old.parent.mkdir(parents=True)
            contents = '{"goals": [], "milestones": [], "task_links": []}'
            old.write_text(contents)
            result = subprocess.run(
                [sys.executable, '-m', 'goal_store'], cwd=Path(__file__).parent,
                env=dict(os.environ, HOME=home), capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stderr, 'Could not load goal registry\n')
            self.assertEqual(old.read_text(), contents)
            self.assertFalse((Path(home) / 'ghost/integrations').exists())


if __name__ == '__main__':
    unittest.main()
