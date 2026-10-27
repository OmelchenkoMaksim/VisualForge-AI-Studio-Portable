"""Video names and exclusive copies; preserve the Image naming contract."""
from pathlib import Path
import re
import shutil
import time
from smart_filenames import build_filename_base


def filename_base(prompt, submitted_at):
    first = re.split(r'[.!?\n]', prompt.strip(), maxsplit=1)[0]
    words = re.findall(r'[A-Za-z]+(?:-[A-Za-z]+)*', first)
    if len(words) >= 3 and not re.search(r'[^\x00-\x7f]', first):
        if words[0].lower() in ('a', 'an', 'the'):
            words = words[1:]
        words = words[:10]
        while len(words) > 1 and words[-1].lower() in ('a', 'an', 'the', 'from', 'to', 'across', 'on', 'in', 'at', 'with'):
            words.pop()
        title = ' '.join(words).title()[:90].rstrip()
        stamp = time.strftime('%Y-%m-%d %H-%M-%S', time.localtime(submitted_at))
        return title + ' - ' + stamp
    return build_filename_base(prompt, 'prompt', submitted_at).replace('Generated Image', 'Generated Video')


def copy_unique(source, directory, base):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    index = 0
    while True:
        target = directory / (base + ('' if not index else ' ' + str(index)) + '.mp4')
        try:
            with target.open('xb') as destination, Path(source).open('rb') as stream:
                shutil.copyfileobj(stream, destination)
            return target
        except FileExistsError:
            index += 1
