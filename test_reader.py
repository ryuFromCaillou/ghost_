from dataclasses import FrozenInstanceError
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import reader


def fixture(marker):
    task = dict(id=marker+'-parent', project_id=marker+'-project',
                section_id=marker+'-section', parent_id=None, content=marker,
                checked=False, labels=['label'], due={'date': '2026-09-12'},
                deadline=None, duration=None, extra={'preserved': [1]})
    return {'tasks': [task, dict(task, id=marker+'-child', parent_id=task['id'])],
            'projects': [dict(id=marker+'-project', name=marker)],
            'sections': [dict(id=marker+'-section', project_id=marker+'-project', name=marker)],
            'sync_metadata': dict(source='todoist', synced_at='2026-09-12T23:00:00Z',
                                  task_count=2, project_count=1, section_count=1,
                                  page_counts=dict(tasks=1, projects=1, sections=1))}


class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.a = self.root / 'snapshot_A'
        self.b = self.root / 'snapshot_B'
        self.write(self.a, fixture('A'))
        self.write(self.b, fixture('B'))
        self.current = self.root / 'current'
        self.current.symlink_to(self.a.name)

    def write(self, directory, data):
        directory.mkdir(exist_ok=True)
        for name, value in data.items():
            (directory / (name+'.json')).write_text(json.dumps(value), encoding='utf-8')

    def test_normal_load(self):
        snapshot = reader.load_snapshot(self.current)
        self.assertEqual(snapshot.directory, self.a)
        self.assertTrue(snapshot.directory.is_absolute())
        self.assertEqual(snapshot.projects[0]['name'], 'A')
        self.assertEqual(snapshot.sections[0]['name'], 'A')
        self.assertEqual(snapshot.tasks[1]['parent_id'], snapshot.tasks[0]['id'])
        self.assertEqual(snapshot.metadata['task_count'], 2)
        self.assertEqual(snapshot.tasks[0]['due']['date'], '2026-09-12')

    def test_symlink_switch_mid_read_resolves_once(self):
        read = reader._read_json
        resolve = Path.resolve
        resolved, opened = [], []

        def tracked_resolve(path, *args, **kwargs):
            resolved.append(path)
            return resolve(path, *args, **kwargs)

        def switching_read(path):
            result = read(path)
            opened.append(path)
            if len(opened) == 1:
                replacement = self.root / 'replacement'
                replacement.symlink_to(self.b.name)
                os.replace(replacement, self.current)
            return result

        with patch.object(Path, 'resolve', tracked_resolve), patch.object(reader, '_read_json', switching_read):
            snapshot = reader.load_snapshot(self.current)
        self.assertEqual(resolved, [self.current])
        self.assertEqual(len(opened), 4)
        self.assertTrue(all(path.parent == self.a for path in opened))
        self.assertEqual(snapshot.projects[0]['name'], 'A')
        self.assertEqual(snapshot.sections[0]['name'], 'A')
        self.assertTrue(all(task['content'] == 'A' for task in snapshot.tasks))
        self.assertEqual(self.current.resolve(), self.b)

    def test_missing_file_checked_before_parsing(self):
        for name in reader.REQUIRED_FILES:
            with self.subTest(name=name):
                self.write(self.a, fixture('A'))
                (self.a / name).unlink()
                with patch.object(reader, '_read_json') as read:
                    with self.assertRaises(reader.SnapshotReadError):
                        reader.load_snapshot(self.current)
                    read.assert_not_called()

    def test_malformed_files(self):
        for name in reader.REQUIRED_FILES:
            for content in (b'{broken', b'\xff', b'{"x":1,"x":2}', b'NaN'):
                with self.subTest(name=name, content=content):
                    self.write(self.a, fixture('A'))
                    (self.a / name).write_bytes(content)
                    with self.assertRaises(reader.SnapshotReadError):
                        reader.load_snapshot(self.current)

    def test_broken_parent_and_cycles(self):
        for parent in ('missing', 'A-child'):
            data = fixture('A')
            data['tasks'][0]['parent_id'] = parent
            self.write(self.a, data)
            with self.assertRaises(reader.SnapshotReadError):
                reader.load_snapshot(self.current)

    def test_invalid_schema_and_metadata(self):
        changes = [lambda d: d.update(tasks={}),
                   lambda d: d['tasks'].append(d['tasks'][0]),
                   lambda d: d['tasks'][0].update(labels='label'),
                   lambda d: d['tasks'][0].update(checked=0),
                   lambda d: d['tasks'][0].update(project_id=[]),
                   lambda d: d['tasks'][0].update(section_id='missing'),
                   lambda d: d['sections'][0].update(project_id='missing'),
                   lambda d: d['sync_metadata'].update(task_count=99),
                   lambda d: d['sync_metadata'].update(source='other'),
                   lambda d: d['sync_metadata'].update(synced_at='bad'),
                   lambda d: d['sync_metadata'].update(synced_at='2026-09-12'),
                   lambda d: d['sync_metadata'].update(page_counts={'tasks': 0})]
        for i, change in enumerate(changes):
            with self.subTest(case=i):
                data = fixture('A')
                change(data)
                self.write(self.a, data)
                with self.assertRaises(reader.SnapshotReadError):
                    reader.load_snapshot(self.current)

    def test_missing_dangling_loop_and_non_directory_current(self):
        self.current.unlink()
        with self.assertRaises(reader.SnapshotReadError):
            reader.load_snapshot(self.current)
        for target in ('absent', 'current', 'snapshot_A/tasks.json'):
            self.current.symlink_to(target)
            with self.assertRaises(reader.SnapshotReadError):
                reader.load_snapshot(self.current)
            self.current.unlink()

    def test_linked_input_file_rejected(self):
        file = self.a / 'tasks.json'
        file.unlink()
        file.symlink_to(self.b / 'tasks.json')
        with self.assertRaises(reader.SnapshotReadError):
            reader.load_snapshot(self.current)

    def test_no_mutation_or_network_and_deep_immutability(self):
        def state():
            paths = [self.current, self.a, self.b, *self.a.iterdir(), *self.b.iterdir()]
            return {str(p): (p.lstat().st_mtime_ns, p.lstat().st_ctime_ns,
                            os.readlink(p) if p.is_symlink() else p.read_bytes() if p.is_file() else None)
                    for p in paths}
        before = state()
        with patch('socket.socket', side_effect=AssertionError('Reader attempted networking')):
            snapshot = reader.load_snapshot(self.current)
        self.assertEqual(before, state())
        with self.assertRaises(FrozenInstanceError):
            snapshot.tasks = ()
        with self.assertRaises(TypeError):
            snapshot.tasks[0]['content'] = 'changed'
        with self.assertRaises(TypeError):
            snapshot.tasks[0]['extra']['preserved'][0] = 2
        with self.assertRaises(TypeError):
            snapshot.metadata['task_count'] = 9

    def test_file_disappearing_after_preflight(self):
        read = reader._read_json
        def disappear(path):
            (self.a / 'sync_metadata.json').unlink(missing_ok=True)
            return read(path)
        with patch.object(reader, '_read_json', disappear):
            with self.assertRaises(reader.SnapshotReadError):
                reader.load_snapshot(self.current)


if __name__ == '__main__':
    unittest.main()
