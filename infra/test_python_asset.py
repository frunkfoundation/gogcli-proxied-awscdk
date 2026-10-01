"""Build the deployable Python payload, including Linux binary wheels and UI assets."""
import struct
import tempfile
import unittest
from pathlib import Path

from stacks.python_asset import LocalPythonBundling


class PythonAssetTests(unittest.TestCase):
    def test_real_linux_python311_payload(self):
        entry = Path(__file__).parent / 'lambda' / 'google_proxy'
        with tempfile.TemporaryDirectory() as directory:
            self.assertTrue(LocalPythonBundling(entry).try_bundle(directory, None))
            output = Path(directory)
            for name in ('handler.py', 'onboarding.py', 'onboarding_services.py',
                         'google/auth/__init__.py', 'authlib/__init__.py',
                         'requests/__init__.py', 'assets/onboarding/index.html',
                         'assets/onboarding/app.js', 'assets/onboarding/style.css',
                         'assets/onboarding/connect.html', 'assets/onboarding/connect.js',
                         'onboarding_links.py', 'onboarding_links_store.py',
                         'onboarding_connection_http.py'):
                self.assertTrue((output / name).is_file(), name)
            self.assertFalse(list(output.glob('test_*.py')))
            self.assertFalse(list(output.rglob('__pycache__')))
            binaries = list(output.rglob('*.so'))
            self.assertTrue(binaries, 'cryptography/cffi binary wheels are present')
            for binary in binaries:
                header = binary.read_bytes()[:20]
                self.assertEqual(header[:4], b'\x7fELF', binary.name)
                self.assertEqual(header[4:6], bytes([2, 1]), binary.name)  # 64-bit, little endian.
                self.assertEqual(struct.unpack('<H', header[18:20])[0], 62, binary.name)  # x86_64.
            tags = [line for wheel in output.glob('*.dist-info/WHEEL')
                    for line in wheel.read_text().splitlines() if line.startswith('Tag:')]
            self.assertTrue(any('manylinux' in tag for tag in tags))
            for tag in tags:
                interpreter, abi, platform = tag.removeprefix('Tag: ').split('-')
                if platform != 'any':
                    self.assertTrue(interpreter.startswith('cp'), tag)
                    self.assertTrue((abi == 'abi3' and int(interpreter[2:]) <= 311)
                                    or (interpreter == 'cp311' and abi == 'cp311'), tag)
            self.assertTrue(all('macosx' not in tag and 'aarch64' not in tag for tag in tags))


if __name__ == '__main__':
    unittest.main()
