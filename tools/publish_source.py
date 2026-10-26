"""Prepare/push a checked source snapshot from the portable folder, without its private development ancestry.

    python tools/publish_source.py --prepare
    python tools/publish_source.py --push

The existing origin must point at the intended GitHub repository. No force push, tags or other branches are sent.
"""
import argparse
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_repo

ROOT = Path(__file__).resolve().parents[1]


def git(*args, env=None):
    return subprocess.check_output(['git', '-C', str(ROOT), *args], env=env)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare', action='store_true')
    parser.add_argument('--push', action='store_true')
    parser.add_argument('--message-file', help='Reviewed UTF-8 commit message')
    options = parser.parse_args(argv)
    if not options.prepare and not options.push:
        parser.error('Choose --prepare or --push')
    if git('status', '--porcelain').strip():
        raise ValueError('Commit/review the source changes before preparing publication')
    remote = git('remote', 'get-url', 'origin').decode().strip()
    if not (remote.startswith('https://github.com/') or remote.startswith('git@github.com:')):
        raise ValueError('origin must be the intended GitHub repository')
    parents = []
    published = git('ls-remote', 'origin', 'refs/heads/main').decode().split()
    if published:
        git('fetch', 'origin', 'refs/heads/main')
        parent = git('rev-parse', 'FETCH_HEAD').decode().strip()
        problems, _ = check_repo.check_revision(ROOT, parent)
        if problems:
            raise ValueError('Remote main has not passed the source-only policy')
        parents = ['-p', parent]
    environment = dict(os.environ, GIT_AUTHOR_NAME='VisualForge', GIT_AUTHOR_EMAIL='noreply@users.noreply.github.com',
        GIT_COMMITTER_NAME='VisualForge', GIT_COMMITTER_EMAIL='noreply@users.noreply.github.com')
    tree = git('rev-parse', 'HEAD^{tree}').decode().strip()
    message = Path(options.message_file).read_text('utf-8-sig').strip() if options.message_file else 'VisualForge AI Studio source update'
    if not message or len(message) > 16000:
        raise ValueError('Empty or oversized commit message')
    snapshot = git('commit-tree', tree, *parents, '-m', message, env=environment).decode().strip()
    problems, summary = check_repo.check_revision(ROOT, snapshot)
    if problems:
        raise ValueError('Publication refused: ' + '; '.join(problems[:10]))
    git('update-ref', 'refs/heads/public-main', snapshot)
    git('config', 'core.hooksPath', '.githooks')
    print('Checked public-main: %d commits, %d source records, %d bytes (10 GB ceiling).' % (summary['commits'], summary['files'], summary['bytes']))
    if options.push:
        subprocess.run(['git', '-C', str(ROOT), 'push', 'origin', 'public-main:refs/heads/main'], check=True)
        actual = git('ls-remote', 'origin', 'refs/heads/main').decode().split()[0]
        if actual != snapshot:
            raise ValueError('Remote main did not match the prepared snapshot')
        print('Published only the checked main source branch; development branches and tags were not sent.')
    else:
        print('Prepared locally. To publish explicitly: python tools/publish_source.py --push')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print('Publication stopped: %s' % error, file=sys.stderr)
        sys.exit(1)
