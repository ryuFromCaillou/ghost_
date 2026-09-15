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
                   STRATEGIC_CONTEXT_PATH, TODOIST_HISTORY_DB_PATH)
root = Path(os.environ['HOME']) / 'ghost/integrations'
assert INTEGRATIONS_ROOT == root
assert STATE_ROOT == root / 'state'
assert TODOIST_EXTERNAL_STATE_ROOT == root / 'state/external/todoist'
assert STRATEGIC_CONTEXT_PATH == root / 'state/strategic-context.json'
assert TODOIST_HISTORY_DB_PATH == root / 'state/todoist-history.sqlite3'
''')

    def test_consumers_use_central_defaults(self):
        self.run_isolated('''
import inspect
from pathlib import Path
import sys
import paths
import strategic_context
import reader
import sync
sys.path.insert(0, str(Path.cwd().parents[1]))
from integrations.todoist import history, paths as package_paths
from integrations.todoist import strategic_context as package_store
from integrations.todoist import reader as package_reader, sync as package_sync
for module in (strategic_context, package_store):
    for function in (module.load_strategic_context,):
        assert inspect.signature(function).parameters['path'].default == paths.STRATEGIC_CONTEXT_PATH
for module in (sync, package_sync):
    assert module.DEFAULT_OUTPUT == paths.TODOIST_EXTERNAL_STATE_ROOT
    assert inspect.signature(module.sync).parameters['output'].default == paths.TODOIST_EXTERNAL_STATE_ROOT
for module in (reader, package_reader):
    assert inspect.signature(module.load_snapshot).parameters['current'].default == paths.TODOIST_EXTERNAL_STATE_ROOT
assert history.DEFAULT_HISTORY == paths.TODOIST_HISTORY_DB_PATH
assert inspect.signature(history.ingest_history).parameters['path'].default == paths.TODOIST_HISTORY_DB_PATH
assert package_paths.STATE_ROOT == paths.STATE_ROOT
''')
