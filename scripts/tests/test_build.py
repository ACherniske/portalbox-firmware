"""Host tests for scripts/build.py. Run: py -3 -m unittest discover -s scripts/tests"""
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.dirname(HERE))

import build  # noqa: E402

SRC = os.path.join(REPO, 'src')


class TestBuild(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_blob_sha_matches_git(self):
        self.assertEqual(build.git_blob_sha(b'hello\n'),
                         'ce013625030ba8dba906f756967f9e9ca394464a')

    def test_ship_list_maps_and_skips(self):
        ship = build.ship_list(SRC)
        for pair in (('core/fsm.py', 'core/fsm.mpy'), ('boot.py', 'boot.py'),
                     ('main.py', 'main.py'), ('recovery.py', 'recovery.py'),
                     ('certs/github_roots.pem', 'certs/github_roots.pem'),
                     ('core/api_mock.py', 'core/api_mock.mpy')):
            self.assertIn(pair, ship)
        devices = [d for _, d in ship]
        self.assertFalse([d for d in devices if d.startswith(('config/', 'tools/'))])
        self.assertNotIn('manifest.json', devices)
        self.assertEqual(devices, sorted(devices))

    def test_required_in_ship_list(self):
        """The box refuses a bundle without its REQUIRED files: the build
        must always ship them (read from src/net/ota_target.py)."""
        import ast
        with open(os.path.join(SRC, 'net', 'ota_target.py'), encoding='utf-8') as f:
            tree = ast.parse(f.read())
        required = None
        for node in tree.body:
            if isinstance(node, ast.Assign) and getattr(node.targets[0], 'id', '') == 'REQUIRED':
                required = ast.literal_eval(node.value)
        self.assertTrue(required, 'REQUIRED not found')
        devices = [d for _, d in build.ship_list(SRC)]
        self.assertEqual([p for p in required if p not in devices], [])

    def test_ship_list_refuses_unlisted_module(self):
        src = os.path.join(self.tmp, 'src')
        shutil.copytree(SRC, src, ignore=shutil.ignore_patterns('__pycache__'))
        with open(os.path.join(src, 'net', 'extra.py'), 'w') as f:
            f.write('x = 1\n')
        with self.assertRaises(ValueError) as e:
            build.ship_list(src)
        self.assertEqual(str(e.exception), 'not in manifest: net/extra.py')

    def test_bundle_round_trip(self):
        path = os.path.join(self.tmp, 'b.bundle')
        build.write_bundle([('a/b.mpy', b'xy'), ('c.pem', b'')], 'v9', path)
        header, bodies = build.read_bundle(path)
        self.assertEqual(header['format'], 1)
        self.assertEqual(header['mpy'], '6.3')
        self.assertEqual(header['version'], 'v9')
        self.assertEqual(header['files'], [['a/b.mpy', 2, build.git_blob_sha(b'xy')],
                                           ['c.pem', 0, build.git_blob_sha(b'')]])
        self.assertEqual(bodies, {'a/b.mpy': b'xy', 'c.pem': b''})
        with open(path, 'rb') as f:
            first = f.readline()
        self.assertTrue(first.endswith(b'\n'))
        self.assertEqual(json.loads(first), header)

    def test_bundle_deterministic(self):
        files = [('z.mpy', b'1'), ('a.mpy', b'22'), ('m/x.pem', b'333')]
        p1, p2, p3 = (os.path.join(self.tmp, n) for n in ('1', '2', '3'))
        build.write_bundle(files, 'v1', p1)
        build.write_bundle(files, 'v1', p2)
        build.write_bundle(list(reversed(files)), 'v1', p3)
        data = [open(p, 'rb').read() for p in (p1, p2, p3)]
        self.assertEqual(data[0], data[1])
        self.assertEqual(data[0], data[2])

    def test_header_limit(self):
        files = [('dir/%s_%03d.mpy' % ('x' * 20, i), b'') for i in range(400)]
        with self.assertRaises(ValueError):
            build.write_bundle(files, 'v1', os.path.join(self.tmp, 'big'))

    def test_check_mpy_cross_rejects_other_version(self):
        with self.assertRaises(RuntimeError):
            build.check_mpy_cross([sys.executable, '-c',
                                   'print("mpy-cross emitting mpy v6.2")', '--'])
        build.check_mpy_cross([sys.executable, '-c',
                               'print("mpy-cross emitting mpy v6.3")', '--'])


if __name__ == '__main__':
    unittest.main()
