"""Native tray lifecycle and owned-process cleanup without loading GPU models."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

class TrayTests(unittest.TestCase):
    def probe(self, folder):
        shell = Path(os.environ['SystemRoot']) / folder / 'WindowsPowerShell/v1.0/powershell.exe'
        if not shell.exists():
            self.skipTest(f'{shell} is not installed')
        self.run_probe(shell)

    def run_probe(self, shell):
        with tempfile.TemporaryDirectory() as scratch:
            result = subprocess.run([str(shell), '-NoProfile', '-STA', '-ExecutionPolicy', 'Bypass',
                '-File', str(ROOT/'tests/tray_probe.ps1'), '-Root', str(ROOT), '-Scratch', scratch],
                capture_output=True, text=True, timeout=50)
            self.assertEqual(result.returncode, 0, result.stdout[-2000:] + result.stderr[-2000:])
            self.assertIn('owned tree cleanup passed', result.stdout)

    @unittest.skipUnless(os.name == 'nt', 'Windows notification area')
    def test_native_host_and_verified_tree_exit(self):
        self.probe('System32')

    @unittest.skipUnless(os.name == 'nt', 'Windows notification area')
    def test_native_host_under_32_bit_powershell(self):
        # The launcher EXE is a 32-bit program, so on 64-bit Windows it starts the 32-bit (SysWOW64) PowerShell.
        self.probe('SysWOW64')

    @unittest.skipUnless(os.name == 'nt', 'Windows notification area')
    def test_native_host_under_powershell_7(self):
        # Where PowerShell 7 is installed the launcher EXE starts pwsh.exe, whose .NET keeps Icon in System.Drawing.Common.
        shell = shutil.which('pwsh')
        if not shell:
            self.skipTest('PowerShell 7 (pwsh) is not installed')
        self.run_probe(shell)
