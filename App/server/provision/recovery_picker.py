"""Windows picker with a hard timeout matching the server recovery deadline."""
from ui_strings import source as ui_text
import base64
import os
import json
from pathlib import Path
import subprocess
from urllib.parse import urlsplit


def standalone(root):
    def request(item, cancel):
        if cancel():
            from .net import Cancelled
            raise Cancelled()
        try:
            result = subprocess.run(['powershell.exe', '-NoProfile', '-STA', '-ExecutionPolicy', 'Bypass', '-File',
                                     str(Path(root) / 'App/Recovery-Source.ps1'), '-Name', item['name'], '-ExpectedHash', item['sha256']],
                                    capture_output=True, timeout=32, creationflags=subprocess.CREATE_NO_WINDOW)
        except subprocess.TimeoutExpired:
            return None
        if result.returncode or not result.stdout.strip():
            return None
        choice = json.loads(result.stdout.decode('utf-8-sig'))
        if choice.get('kind') not in ('url', 'file') or not isinstance(choice.get('value'), str):
            return None
        if choice['kind'] == 'url':
            parsed = urlsplit(choice['value'])
            if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
                return None
        return choice
    return request


def choose_file(remaining):
    if os.name != 'nt':
        raise ValueError(ui_text('components.choose_file_paste_full_path_manually'))
    if remaining <= 0:
        raise ValueError(ui_text('components.choose_file_selection_timed_out'))
    script = r'''
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.OpenFileDialog
$dialog.Title = 'Выберите скачанный файл зависимости VisualForge'
$dialog.CheckFileExists = $true
$owner = New-Object System.Windows.Forms.Form
$owner.TopMost = $true
$owner.ShowInTaskbar = $false
try {
    if ($dialog.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) {
        [Console]::Write($dialog.FileName)
    }
} finally { $dialog.Dispose(); $owner.Dispose() }
'''
    encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
    try:
        result = subprocess.run(['powershell.exe', '-NoProfile', '-STA', '-EncodedCommand', encoded],
                                capture_output=True, timeout=max(.1, remaining), creationflags=subprocess.CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired:
        return ''
    if result.returncode:
        raise ValueError(ui_text('components.choose_file_open_picker_paste_full_path_manually_failed'))
    return result.stdout.decode('utf-8-sig').strip()
