"""Repository hygiene guard: nothing heavy, private or machine-specific may be committed.

    python tools/check_repo.py              # files Git would add: tracked + untracked, minus ignored
    python tools/check_repo.py --staged     # only what is staged (pre-commit hook)
    python tools/check_repo.py --tracked    # everything tracked (pre-push hook)

Exit code 0 = clean, 1 = problems (listed one per line), 2 = Git is not available.
"""
import argparse
import fnmatch
import os
from pathlib import Path
import re
import subprocess
import sys

ALLOWED_TOP = {
    '.gitignore', '.gitattributes', '.githooks', 'README.md', 'README.ru.md', 'LICENSE', 'LICENSE.md', 'THIRD-PARTY-NOTICES.md',
    'Setup-VisualForge.cmd', 'Start-VisualForge.cmd', 'App', 'docs', 'tests', 'tools', 'Support', '.github',
}
MAX_BYTES = 2 * 1024 * 1024
MAX_REPOSITORY_BYTES = 10_000_000_000
FORBIDDEN_DIRS = {'models', 'runtime', 'userdata', 'browserprofiles', 'cache', 'caches', 'results', 'outputs', 'logs', 'temp', 'tmp',
                  'backups', 'downloads', 'node_modules', '.cache', '.venv', 'venv', '__pycache__', 'dist', 'build', '.pytest_cache', '.mypy_cache',
                  'prompts', 'chats', 'history', 'sessions', 'generated', 'reports'}
PRIVATE_NAMES = ('*prompt*archive*.json', '*prompt*history*.json', '*job*history*.json', '*.settings.json', '*token*.txt', '*credentials*.json', 'prompts.json', 'favorites.json', 'chats.json')
IMAGE_ASSETS = {'App/frontend/visualforge-icon.png', 'App/frontend/visualforge-icon.ico'}
FORBIDDEN_SUFFIXES = {
    '.safetensors', '.ckpt', '.pt', '.pth', '.gguf', '.bin', '.onnx', '.npz', '.7z', '.zip', '.tar', '.gz', '.tgz', '.whl',
    '.dll', '.pyd', '.so', '.exe', '.msi', '.mp4', '.mov', '.webm', '.mkv', '.wav', '.flac', '.mp3', '.pem', '.key', '.pyc',
    '.sqlite', '.sqlite3', '.db', '.jsonl', '.ndjson', '.crdownload', '.dmp', '.sft', '.h5', '.hdf5', '.tflite', '.ot', '.npy',
    '.rar', '.xz', '.bz2', '.zst', '.gif', '.webp', '.jpeg', '.jpg', '.ogg', '.m4a', '.aac', '.avi', '.pdf', '.docx', '.xlsx', '.pptx', '.psd', '.blend',
    '.log', '.tmp', '.part', '.partial', '.bak', '.pyo', '.pyd',
    '.pkl', '.pickle', '.ggml', '.pb', '.tensor',
}
# Text that must never ship: the owner's drive/user names and credentials.
PERSONAL = [
    ('personal Windows user path', re.compile(r'[A-Za-z]:\\+Users\\+(?!\.\.\.|<|%|\{|\$|Public\b|Default\b|User\b|USERNAME\b|name\b|you\b)[^\\\s"\'`<>*]+', re.I)),
    ("owner's drive folder", re.compile('\u0421\u043e\u0444\u0442 - \u041f\u0440\u043e\u0433\u0440\u0430\u043c\u043c\u044b')),
    ('owner workspace path', re.compile(r'Documents\\+Codex')),
    ('token or private key', re.compile(r'(hf_[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY)')),
]
EMAIL = re.compile(r'[\w.+-]+@[\w-]+(?:\.[\w-]+)+')
EMAIL_OK = re.compile(r'(@example\.(com|org)|@users\.noreply\.github\.com|^noreply@anthropic\.com|^git@github\.com|^maksim_omelchenko@zohomail\.eu|@local\.invalid)$', re.I)
# Third-party license texts legitimately name their own legal contacts.
EMAIL_FILES = {'Support/Licenses/Qwen-Research.txt'}


def _git(root, *arguments):
    done = subprocess.run(['git', '-c', 'safe.directory=*', '-C', str(root), *arguments], capture_output=True)
    if done.returncode:
        raise RuntimeError(done.stderr.decode('utf-8', 'replace').strip())
    return done.stdout


def git_files(root, mode):
    if mode == 'staged':
        raw = _git(root, 'diff', '--cached', '--name-only', '--diff-filter=ACMR', '-z')
    elif mode == 'tracked':
        raw = _git(root, 'ls-files', '-z')
    else:
        raw = _git(root, 'ls-files', '-co', '--exclude-standard', '-z')
    return sorted({item.decode('utf-8') for item in raw.split(b'\0') if item})


def check_content(posix, data, mode='100644'):
    """Inspect the actual Git blob, not a possibly different working-tree file."""
    problems = []
    path = Path(posix)
    top = posix.split('/', 1)[0]
    if top not in ALLOWED_TOP:
        problems.append('%s: top-level item "%s" is not on the allow-list (see .gitignore)' % (posix, top))
    if posix.startswith('Support/') and not posix.startswith('Support/Licenses/'):
        problems.append('%s: only Support/Licenses is source' % posix)
    if posix.startswith('.github/') and posix != '.github/workflows/source-guard.yml':
        problems.append('%s: unreviewed GitHub configuration' % posix)
    if mode not in ('100644', '100755'):
        problems.append('%s: symlinks and submodules are not source payloads' % posix)
    if any(part.lower() in FORBIDDEN_DIRS for part in posix.split('/')[:-1]):
        problems.append('%s: generated or private directory' % posix)
    name = path.name.lower()
    if name == '.env' or name.startswith('.env.') or any(fnmatch.fnmatchcase(name, pattern) for pattern in PRIVATE_NAMES):
        problems.append('%s: private settings, history or credentials' % posix)
    suffix = path.suffix.lower()
    if suffix in FORBIDDEN_SUFFIXES or (suffix in ('.png', '.ico') and posix not in IMAGE_ASSETS):
        problems.append('%s: forbidden file type %s (downloaded by Setup, never committed)' % (posix, suffix))
    if len(data) > MAX_BYTES:
        problems.append('%s: %.1f MB exceeds the %d MB source limit' % (posix, len(data) / 1048576, MAX_BYTES // 1048576))
        return problems
    if suffix in FORBIDDEN_SUFFIXES:
        return problems
    if b'\0' in data:
        if posix not in IMAGE_ASSETS:
            problems.append('%s: binary content outside reviewed branding assets' % posix)
        return problems
    if data.startswith((b'MZ', b'PK\x03\x04', b'\x7fELF', b'\x89PNG')) and posix not in IMAGE_ASSETS:
        problems.append('%s: binary payload disguised as source' % posix)
    try:
        text = data.decode('utf-8-sig')
    except UnicodeDecodeError:
        return problems + ['%s: non-UTF-8 payload outside branding assets' % posix]
    for label, pattern in PERSONAL:
        if pattern.search(text):
            problems.append('%s: %s' % (posix, label))
    if posix not in EMAIL_FILES and any(not EMAIL_OK.search(match.group(0)) for match in EMAIL.finditer(text)):
        problems.append('%s: private e-mail address' % posix)
    return problems


def check(root, relative_paths):
    """Inspect candidate working files; commit and push modes use Git objects below."""
    root = Path(root)
    problems = []
    for relative in relative_paths:
        posix = relative.replace('\\', '/')
        path = root / posix
        if path.is_symlink():
            problems.append('%s: symlinks are not source payloads' % posix)
        elif path.is_file():
            if path.stat().st_size > MAX_BYTES:
                problems.append('%s: exceeds the source file limit' % posix)
            else:
                problems.extend(check_content(posix, path.read_bytes()))
    return problems


def _blobs(root, oids):
    """Read small blobs in one batch; reject large objects before allocating their content."""
    oids = list(dict.fromkeys(oids))
    if not oids:
        return {}
    request = ('\n'.join(oids) + '\n').encode('ascii')
    sizes = subprocess.run(['git', '-C', str(root), 'cat-file', '--batch-check=%(objectname) %(objecttype) %(objectsize)'], input=request, capture_output=True, check=True).stdout
    wanted = []
    result = {}
    for line in sizes.decode().splitlines():
        oid, kind, size = line.split()
        if kind != 'blob' or int(size) > MAX_BYTES:
            result[oid] = None
        else:
            wanted.append(oid)
    if wanted:
        raw = subprocess.run(['git', '-C', str(root), 'cat-file', '--batch'], input=('\n'.join(wanted)+'\n').encode(), capture_output=True, check=True).stdout
        offset = 0
        for oid in wanted:
            end = raw.index(b'\n', offset)
            size = int(raw[offset:end].split()[-1])
            result[oid] = raw[end+1:end+1+size]
            offset = end+size+2
    return result


def check_index(root):
    selected = set(git_files(root, 'staged'))
    rows = []
    for item in _git(root, 'ls-files', '--stage', '-z').split(b'\0'):
        if not item:
            continue
        meta, name = item.split(b'\t', 1)
        mode, oid, stage = meta.decode().split()
        relative = name.decode('utf-8')
        if relative in selected:
            rows.append((relative, mode, oid))
    blobs = _blobs(root, [oid for _, _, oid in rows])
    problems = []
    for relative, mode, oid in rows:
        data = blobs[oid]
        problems.extend(['%s: oversized or non-blob Git object' % relative] if data is None else check_content(relative, data, mode))
    return problems, len(rows)


def check_revision(root, revision):
    """Check every tree/blob reachable from the exact ref that would be sent, including deleted old files."""
    commits = _git(root, 'rev-list', revision).decode().splitlines()
    records = set()
    trees = set()
    for commit in commits:
        tree = _git(root, 'rev-parse', commit+'^{tree}').strip()
        if tree in trees:
            continue
        trees.add(tree)
        for item in _git(root, 'ls-tree', '-rlz', commit).split(b'\0'):
            if item:
                meta, name = item.split(b'\t', 1)
                mode, kind, oid, size = meta.decode().split()
                records.add((name.decode('utf-8'), mode, oid, int(size) if size.isdigit() else MAX_BYTES+1))
    sizes = {oid:size for _, _, oid, size in records}
    problems = []
    if sum(sizes.values()) > MAX_REPOSITORY_BYTES:
        return ['Reachable source history exceeds the 10 GB publication budget'], dict(commits=len(commits), files=len(records), bytes=sum(sizes.values()))
    for commit in commits:
        metadata = _git(root, 'show', '-s', '--format=%an <%ae>%n%cn <%ce>%n%B', commit)
        for problem in check_content('docs/commit-metadata.txt', metadata):
            problems.append('Commit metadata: ' + problem.split(': ', 1)[1])
    small = [oid for oid, size in sizes.items() if size <= MAX_BYTES]
    blobs = _blobs(root, small)
    for relative, mode, oid, size in sorted(records):
        if size > MAX_BYTES:
            problems.append('%s: Git history contains an oversized payload' % relative)
        elif blobs.get(oid) is None:
            problems.append('%s: Git history contains a non-blob payload' % relative)
        else:
            problems.extend(check_content(relative, blobs[oid], mode))
    return sorted(set(problems)), dict(commits=len(commits), files=len(records), bytes=sum(sizes.values()))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--staged', action='store_true')
    group.add_argument('--tracked', action='store_true')
    group.add_argument('--revision')
    group.add_argument('--push', action='store_true')
    parser.add_argument('--root', default=str(Path(__file__).resolve().parents[1]))
    arguments = parser.parse_args(argv)
    if arguments.push:
        revisions = []
        for line in sys.stdin:
            local_ref, local_sha, remote_ref, remote_sha = line.split()
            if set(local_sha) == {'0'}:
                continue
            if remote_ref != 'refs/heads/main':
                print('check_repo: only the reviewed main source branch may be published; tags/private branches are blocked')
                return 1
            revisions.append(local_sha)
        for revision in revisions:
            problems, summary = check_revision(arguments.root, revision)
            for line in problems[:30]:
                print(line)
            print('check_repo: publication history %s, %d problem(s)' % (summary, len(problems)))
            if problems:
                print('Publish the reviewed public-main:main snapshot, not private development history.')
                return 1
        return 0
    if arguments.revision:
        problems, summary = check_revision(arguments.root, arguments.revision)
        for line in problems[:30]:
            print(line)
        print('check_repo: revision %s, %d problem(s)' % (summary, len(problems)))
        return bool(problems)
    mode = 'staged' if arguments.staged else 'tracked' if arguments.tracked else 'candidates'
    try:
        files = git_files(arguments.root, mode)
    except (OSError, RuntimeError) as error:
        print('check_repo: Git is not usable here: %s' % error, file=sys.stderr)
        return 2
    problems = check_index(arguments.root)[0] if arguments.staged else check(arguments.root, files)
    for line in problems:
        print(line)
    print('check_repo: %d file(s) checked (%s), %d problem(s)' % (len(files), mode, len(problems)))
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())
