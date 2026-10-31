"""Video names and exclusive copies; preserve the Image naming contract."""
from pathlib import Path
import re
import shutil
import time
from smart_filenames import build_filename_base


def model_suffix(label):
    """` - <model>` for the end of a saved name; characters Windows forbids in names are dropped."""
    label = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', ' ', label or '')
    label = ' '.join(label.split()).strip(' .')
    return ' - ' + label if label else ''


def filename_base(prompt, submitted_at, model=None):
    """Title, time stamp and, when given, the model that made the video (the end of the name tells which network it was)."""
    return _title_and_stamp(prompt, submitted_at) + model_suffix(model)


def _title_and_stamp(prompt, submitted_at):
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
    """Copy `source` to a free name in `directory` (never overwrites); a copy that fails leaves no file behind."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    index = 0
    # The source is opened first: a missing source must not leave an empty target that looks like a finished clip.
    with Path(source).open('rb') as stream:
        while True:
            target = directory / (base + ('' if not index else ' ' + str(index)) + '.mp4')
            try:
                destination = target.open('xb')
            except FileExistsError:
                index += 1
                continue
            try:
                with destination:
                    shutil.copyfileobj(stream, destination)
            except BaseException:
                # A full disk ends the copy halfway; the truncated MP4 under its final name would be taken for the result.
                target.unlink(missing_ok=True)
                raise
            return target
