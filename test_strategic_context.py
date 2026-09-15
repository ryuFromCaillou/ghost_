from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from strategic_context import StrategicContext, StrategicContextError, load_strategic_context
from migrate_context import migrate_context


class ContextTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.source = self.root / 'goals.json'
        self.destination = self.root / 'strategic-context.json'
        self.legacy = {'whys':[{'id':'w', 'title':'Why'}],
                       'directions':[{'why_id':'w', 'title':'Direction'}],
                       'goals':[{'preserved':'data'}], 'milestones':[], 'task_links':[]}
        self.source.write_text(json.dumps(self.legacy))

    def test_migration_preserves_source_exactly(self):
        before = self.source.read_bytes(), self.source.stat().st_mtime_ns
        result = migrate_context(self.source, self.destination)
        self.assertEqual(result, StrategicContext('Why', 'Direction'))
        self.assertEqual((self.source.read_bytes(), self.source.stat().st_mtime_ns), before)
        self.assertEqual(json.loads(self.destination.read_text()), {'why':'Why', 'direction':'Direction'})
        self.assertEqual(load_strategic_context(self.destination), result)

    def test_idempotent_migration(self):
        migrate_context(self.source, self.destination)
        before = self.destination.read_bytes(), self.destination.stat().st_mtime_ns
        migrate_context(self.source, self.destination)
        self.assertEqual((self.destination.read_bytes(), self.destination.stat().st_mtime_ns), before)

    def test_conflict_refused(self):
        self.destination.write_text('{"why":"Different", "direction":"Direction"}')
        before = self.destination.read_bytes()
        with self.assertRaises(StrategicContextError):
            migrate_context(self.source, self.destination)
        self.assertEqual(self.destination.read_bytes(), before)

    def test_ambiguous_migration_refused(self):
        self.legacy['whys'].append({'id':'other', 'title':'Other'})
        self.source.write_text(json.dumps(self.legacy))
        with self.assertRaises(StrategicContextError):
            migrate_context(self.source, self.destination)
        self.assertFalse(self.destination.exists())

    def test_unlinked_direction_refused(self):
        self.legacy['directions'][0]['why_id'] = 'unknown'
        self.source.write_text(json.dumps(self.legacy))
        with self.assertRaises(StrategicContextError):
            migrate_context(self.source, self.destination)

    def test_failed_publication_preserves_state(self):
        before = self.source.read_bytes()
        with patch('migrate_context.os.link', side_effect=OSError), self.assertRaises(StrategicContextError):
            migrate_context(self.source, self.destination)
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(list(self.root.iterdir()), [self.source])

    def test_publication_is_complete_and_does_not_overwrite(self):
        def concurrent(source, destination):
            self.assertEqual(json.loads(Path(source).read_text()), {'why':'Why', 'direction':'Direction'})
            Path(destination).write_text('concurrent contents')
            raise FileExistsError
        with patch('migrate_context.os.link', side_effect=concurrent), self.assertRaises(StrategicContextError):
            migrate_context(self.source, self.destination)
        self.assertEqual(self.destination.read_text(), 'concurrent contents')

    def test_missing_context_no_legacy_fallback(self):
        with self.assertRaises(StrategicContextError):
            load_strategic_context(self.destination)
        self.assertFalse(self.destination.exists())

    def test_strict_schema_and_no_status(self):
        for content in ('{}', '[]', '{', '{"why":"Why","direction":"D","status":"active"}',
                        '{"why":"Why","why":"Other","direction":"D"}',
                        '{"why":null,"direction":"D"}', '{"why":" ","direction":"D"}'):
            with self.subTest(content=content):
                self.destination.write_text(content)
                with self.assertRaises(StrategicContextError):
                    load_strategic_context(self.destination)

    def test_immutable_context(self):
        context = StrategicContext('Why', 'Direction')
        with self.assertRaises(FrozenInstanceError):
            context.why = 'Changed'
