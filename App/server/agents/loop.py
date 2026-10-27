"""Loops for large agent tasks: iterations with a fresh context each, joined by short English handoff notes in the project.

Local models have small contexts and do not know how full theirs is, so the studio decides when an iteration ends: the gateway
measures the context of every model request, and at the chat's threshold the manager interrupts the agent and asks it for a
handoff note as a plain answer (no tools). The studio writes that note to `<project>/.visualforge/loop/<run>/NN-handoff.md`
together with facts it records itself (changed files from git, the result of the user's check command) — a model saying "tests
pass" is not evidence. The next iteration is a new agent session that gets the task, the note and its path."""
import hashlib
import os
from pathlib import Path
import re
import subprocess
import threading

from .opencode import clean_environment

DEFAULTS = dict(threshold=75, iterations=10, check='')
LIMITS = dict(threshold=(30, 95), iterations=(1, 100))
CHECK_TIMEOUT = 15 * 60
MAX_CHECK_OUTPUT = 3000
FOLDER = '.visualforge'

HANDOFF = """Stop working on the task now and do not call any tools. Write the handoff note for the next iteration, in English, at most
250 words, exactly in this Markdown form:

## Done
- what this iteration finished (files, functions, results)

## Next step
- the single most useful next action, concrete enough to start without re-reading everything

## Blocker
- what stops progress, or "none"

## Status
CONTINUE, DONE (the whole task is finished and verified) or BLOCKED"""


def options(data):
    """Validated loop settings from the page: threshold (% of the context), iterations, optional check command."""
    if not isinstance(data, dict) or set(data) - set(DEFAULTS):
        raise ValueError('Настройки цикла содержат неподдерживаемые параметры')
    result = dict(DEFAULTS)
    for key, (low, high) in LIMITS.items():
        if key in data:
            try:
                value = int(data[key])
            except (TypeError, ValueError):
                raise ValueError('Настройки цикла: %s — целое число' % key) from None
            if not low <= value <= high:
                raise ValueError('Порог контекста: от 30 до 95%%' if key == 'threshold' else 'Число итераций: от 1 до 100')
            result[key] = value
    check = data.get('check', '')
    if not isinstance(check, str) or len(check) > 500 or '\n' in check:
        raise ValueError('Команда проверки: одна строка до 500 символов')
    result['check'] = check.strip()
    return result


def iteration_prompt(goal, number, total, previous):
    """The task of one iteration. `previous`: dict(rel, text) of the last handoff note, or None for the first iteration."""
    parts = ['You work on a large task in iterations; this is iteration %d of at most %d. Every iteration starts with a fresh '
             'context, so the handoff note below is all you know about earlier work.' % (number, total), '',
             'Task from the user:', goal.strip(), '']
    if previous:
        parts += ['Handoff note of iteration %d (file %s):' % (number - 1, previous['rel']), '-----', previous['text'].strip(), '-----',
                  'Continue from its "Next step"; do not redo work it lists as done.', '']
    else:
        parts += ['This is the first iteration: look at the project and start with the most important step.', '']
    parts.append('Make real progress and verify what you change. The studio interrupts you when your context is nearly full and then '
                 'asks for a handoff note; until then keep working.')
    return '\n'.join(parts)


def _git(workspace, *args, timeout=60):
    try:
        done = subprocess.run(['git', *args], cwd=workspace, env=clean_environment(), capture_output=True, timeout=timeout,
                              creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except (OSError, subprocess.TimeoutExpired):
        return 1, ''
    return done.returncode, done.stdout.decode('utf-8', 'replace')


def prepare(workspace, run_id):
    """Create the run's folder and keep `.visualforge/` out of git through the local exclude file (.gitignore stays untouched)."""
    folder = Path(workspace) / FOLDER / 'loop' / run_id
    folder.mkdir(parents=True, exist_ok=True)
    code, out = _git(workspace, 'rev-parse', '--git-path', 'info/exclude')
    if code == 0 and out.strip():
        exclude = Path(out.strip())
        exclude = exclude if exclude.is_absolute() else Path(workspace) / exclude
        try:
            lines = exclude.read_text(encoding='utf-8').splitlines() if exclude.is_file() else []
            if '/%s/' % FOLDER not in lines:
                exclude.parent.mkdir(parents=True, exist_ok=True)
                with exclude.open('a', encoding='utf-8') as stream:
                    stream.write(('' if not lines or not lines[-1] else '\n') + '/%s/\n' % FOLDER)
        except OSError:
            pass  # a read-only git folder: the notes then show as untracked files, nothing breaks
    return folder


def head(workspace):
    code, out = _git(workspace, 'rev-parse', 'HEAD')
    return out.strip() if code == 0 else ''


def facts(workspace, start):
    """What git says changed since the run started; `fingerprint` changes whenever the files or commits do."""
    code, status = _git(workspace, 'status', '--short', '--untracked-files=all', '--', '.', ':(exclude)%s' % FOLDER)
    if code != 0:
        return dict(git=False, status='', diffstat='', commits='', fingerprint='')
    _, diff = _git(workspace, 'diff', 'HEAD', '--', '.', ':(exclude)%s' % FOLDER)
    current = head(workspace)
    diffstat = commits = ''
    if start:
        _, diffstat = _git(workspace, 'diff', '--stat', start, '--', '.', ':(exclude)%s' % FOLDER)
        _, commits = _git(workspace, 'log', '--oneline', '%s..HEAD' % start)
    # Not `git status` alone: it names changed files, not their contents, so a second edit of the same file would look like none.
    fingerprint = hashlib.sha1(('%s\n%s\n%s' % (current, status, diff)).encode('utf-8', 'replace')).hexdigest()
    return dict(git=True, status=status.strip(), diffstat=diffstat.strip(), commits=commits.strip(), fingerprint=fingerprint)


def _decode(raw):
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError:
        return raw.decode('oem', 'replace')


def check(command, workspace, cancel):
    """Run the user's check command (cmd.exe) in the project; returns dict(code, output) with the output's tail."""
    try:
        process = subprocess.Popen(command, shell=True, cwd=workspace, env=clean_environment(), stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except OSError as error:
        return dict(code=-1, output=str(error))
    from video.owned import OwnedJob
    try:
        job = OwnedJob(process)
    except OSError:
        job = None
    output = []

    def read():
        for chunk in iter(lambda: process.stdout.read(65536), b''):
            output.append(chunk)
    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    deadline = CHECK_TIMEOUT
    try:
        while process.poll() is None and deadline > 0 and not cancel.is_set():
            try:
                process.wait(1)
            except subprocess.TimeoutExpired:
                deadline -= 1
        timed_out = process.poll() is None
    finally:
        if job:
            job.close()
        if process.poll() is None:
            process.kill()
        reader.join(5)
        process.stdout.close()
    # cmd.exe writes CRLF; the note is written in text mode, which would turn each of them into CR CR LF (doubled blank lines).
    text = _decode(b''.join(output)).replace('\r\n', '\n').strip()
    text = text if len(text) <= MAX_CHECK_OUTPUT else '…' + text[-MAX_CHECK_OUTPUT:]
    if timed_out:
        return dict(code=-1, output=(text + '\n' if text else '') + ('(stopped)' if cancel.is_set() else '(timed out after %d s)' % CHECK_TIMEOUT))
    return dict(code=process.returncode, output=text)


def status_of(note):
    match = re.search(r'#+\s*Status\s*\n+[\s*`_-]*(CONTINUE|DONE|BLOCKED)', note or '', re.IGNORECASE)
    if match:
        return match.group(1).upper()
    words = re.findall(r'\b(CONTINUE|DONE|BLOCKED)\b', note or '')
    return words[-1] if words else 'CONTINUE'


def next_step_of(note):
    match = re.search(r'#+\s*Next step\s*\n(.*?)(?:\n#+\s|\Z)', note or '', re.IGNORECASE | re.DOTALL)
    return ' '.join(match.group(1).split()).lower() if match else ''


ENDED = {'context': 'context threshold reached, the studio interrupted the agent', 'finished': 'the agent finished its turn',
         'user': 'stopped by the user', 'error': 'the agent failed'}


def write(folder, workspace, number, total, run, goal, note, ended, fact, verification):
    """Write NN-handoff.md; returns (path relative to the project with '/', the note for the next iteration's prompt)."""
    used, size = run.get('used') or 0, run.get('size') or 0
    context = ('%d%% of %d tokens%s' % (round(100 * used / size), size, '' if run.get('measured', True) else ', estimated')) if size else 'unknown'
    header = ['# Iteration %02d of %d: handoff' % (number, total), '',
              '- Agent: %s; model: %s' % (run.get('agent', ''), run.get('model', '')),
              '- Ended: %s; context %s' % (ENDED.get(ended, ended), context), '']
    lines = []
    body = (note or '').strip()
    if body:
        lines += [body, '']
    else:
        lines += ['## Status', 'CONTINUE', '', '_No handoff note from the agent (%s). Use the changed files and the check below._' % ENDED.get(ended, ended), '']
    lines += ['## Changed files (recorded by the studio)']
    if not fact['git']:
        lines += ['Not a git repository: changes are not recorded.']
    else:
        lines += ['```', fact['status'] or '(no uncommitted changes)', '```']
        if fact['commits']:
            lines += ['Commits since the start of the loop:', '```', fact['commits'], '```']
        if fact['diffstat']:
            lines += ['Since the start of the loop:', '```', fact['diffstat'], '```']
    if verification is not None:
        lines += ['', '## Verification (run by the studio)', '`%s` exit code %d' % (verification['command'], verification['code']),
                  '```', verification['output'] or '(no output)', '```']
    path = Path(folder) / ('%02d-handoff.md' % number)
    path.write_text('\n'.join(header + ['## Task', goal.strip(), ''] + lines).rstrip() + '\n', encoding='utf-8')
    # The next iteration's prompt carries the task itself; it gets the note without repeating it.
    return os.path.relpath(path, workspace).replace(os.sep, '/'), '\n'.join(header + lines).rstrip() + '\n'
