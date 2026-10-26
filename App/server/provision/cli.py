"""Terminal front end: python App/Setup-VisualForge.py [options]   (normally started by Setup-VisualForge.cmd)

Without options in a terminal it shows a menu: the programs are always installed, every model is a checkbox.
With options it runs unattended, e.g.  --software-only,  --preset light,  --all,  --models image.model.upscale,video.model.wan-ti2v-5b.
"""
import argparse
import ctypes
import locale
from pathlib import Path
import shutil
import sys
import textwrap
import time

from . import catalog as catalog_module
from . import net
from .engine import Busy, Engine, NotEnoughSpace, ProvisionError, default_root

TEXT = {
    'ru': dict(
        title='VisualForge AI Studio: установка компонентов',
        software='Программы (ставятся всегда, нужны всем разделам)', models='Модели (по желанию, можно докачать позже)',
        installed='установлено', partial='частично', missing='нет',
        total='К загрузке: %s, места на диске: около %s (свободно %s)',
        help='Номера через запятую или диапазон (3,5-7) переключают модели; a - все; n - ни одной; l - лёгкий набор; i - установить; q - выйти',
        prompt='Выбор > ', nothing='Нечего устанавливать: всё уже на месте.', done='Готово.', failed='Не удалось: %s',
        start='Начинаю установку...', component='[%d/%d] %s', bye='Выход без установки.',
        noterm='Запущено без терминала: укажите --software-only, --preset, --all или --models. Подробнее: --help.',
        space='Недостаточно места на диске: нужно около %s, свободно %s.', busy='Уже запущена другая установка.',
        later='Модели можно докачать позже: Setup-VisualForge.cmd или вкладка «Компоненты» в программе.',
        first='Это первый запуск. Что установить?', cancelled='Остановлено. Докачка продолжится с того же места.',
        longpath='Предупреждение: путь установки длинный (%d символов). Лучше короткий путь, например D:\\VisualForge.'),
    'en': dict(
        title='VisualForge AI Studio: component setup',
        software='Programs (always installed, every section needs them)', models='Models (optional, can be downloaded later)',
        installed='installed', partial='partial', missing='not installed',
        total='To download: %s, disk space: about %s (free %s)',
        help='Numbers (3,5-7) toggle models; a = all; n = none; l = light set; i = install; q = quit',
        prompt='Choice > ', nothing='Nothing to install: everything is in place.', done='Done.', failed='Failed: %s',
        start='Starting installation...', component='[%d/%d] %s', bye='Quit without installing.',
        noterm='No terminal: pass --software-only, --preset, --all or --models. See --help.',
        space='Not enough disk space: about %s needed, %s free.', busy='Another installation is already running.',
        later='Models can be downloaded later: Setup-VisualForge.cmd or the Components tab in the program.',
        first='This is the first start. What should be installed?', cancelled='Stopped. Downloads resume where they stopped.',
        longpath='Warning: the installation path is long (%d characters). A short path such as D:\\VisualForge is safer.'),
}


def guess_language():
    try:
        lang = ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3ff  # primary language id
        return 'ru' if lang == 0x19 else 'en'
    except (AttributeError, OSError):
        return 'ru' if (locale.getlocale()[0] or '').lower().startswith('ru') else 'en'


def human(size):
    size = float(size)
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if size < 1000 or unit == 'TB':
            return '%.0f %s' % (size, unit) if unit in ('B', 'KB') else '%.1f %s' % (size, unit)
        size /= 1000


def clock(seconds):
    seconds = int(max(0, seconds))
    return '%d:%02d:%02d' % (seconds // 3600, seconds // 60 % 60, seconds % 60) if seconds >= 3600 else '%d:%02d' % (seconds // 60, seconds % 60)


class Printer:
    def __init__(self, stream=None):
        self.stream = stream or sys.stdout
        self.last = 0.0
        self.inline = False

    def __call__(self, event):
        kind = event['type']
        if kind == 'progress':
            now = time.monotonic()
            if now - self.last < 0.4 and event['done'] != event.get('total'):
                return
            self.last = now
            total, done, speed = event.get('total'), event['done'], event.get('speed') or 0
            percent = ('%3d%%' % (100 * done / total)) if total else '   ?'
            eta = ' ETA ' + clock((total - done) / speed) if total and speed > 1 else ''
            line = '   %-44s %s %9s / %-9s %9s/s%s' % (event['name'][:44], percent, human(done), human(total) if total else '?', human(speed), eta)
            self.stream.write('\r' + line[:118].ljust(118))
            self.stream.flush()
            self.inline = True
            return
        if self.inline:
            self.stream.write('\n')
            self.inline = False
        if kind == 'component_start':
            self.stream.write('\n' + '[%d/%d] %s\n' % (event['index'], event['total'], event['title']))
        elif kind == 'note':
            self.stream.write('   ' + event['text'] + '\n')
        elif kind == 'component_failed':
            self.stream.write('   !! %s\n' % event['error'])
        elif kind == 'download_start':
            self.stream.write('   > %s (%s)\n' % (event['name'], human(event['bytes']) if event.get('bytes') else '?'))
        self.stream.flush()


def state_label(texts, state):
    return texts[{'installed': 'installed', 'partial': 'partial'}.get(state, 'missing')]


def parse_numbers(text, count):
    picked = set()
    for part in text.replace(' ', '').split(','):
        if not part:
            continue
        low, _, high = part.partition('-')
        if not low.isdigit() or (high and not high.isdigit()):
            raise ValueError(part)
        first, last = int(low), int(high or low)
        if not 1 <= first <= last <= count:
            raise ValueError(part)
        picked.update(range(first, last + 1))
    return sorted(picked)


def menu(engine, texts, preselected, stdin=None, out=None):
    """Interactive selection; returns the chosen model ids or None to quit."""
    stdin, out = stdin or sys.stdin, out or sys.stdout
    rows = []
    description = engine.describe()
    chosen = set(preselected)
    while True:
        rows = [(row, module['title']) for module in description for row in module['components'] if row['kind'] == 'model']
        out.write('\n=== %s ===\n' % texts['title'])
        out.write('\n%s\n' % texts['software'])
        for module in description:
            for row in module['components']:
                if row['kind'] == 'software':
                    out.write('   [x] %-62s %s\n' % (row['title'][:62], state_label(texts, row['state'])))
        out.write('\n%s\n' % texts['models'])
        current = None
        for number, (row, module_title) in enumerate(rows, 1):
            if module_title != current:
                out.write('  %s\n' % module_title)
                current = module_title
            mark = 'x' if row['id'] in chosen or row['state'] == 'installed' else ' '
            size = human(row['download_bytes'])
            out.write('   %2d. [%s] %-58s %9s  %s\n' % (number, mark, row['title'][:58], size, state_label(texts, row['state'])))
            if row.get('note') and row['state'] != 'installed':
                for number_of_line, piece in enumerate(textwrap.wrap(row['note'], 96)):
                    out.write('          %s %s\n' % ('!' if number_of_line == 0 else ' ', piece))
        plan = engine.plan(engine.select(models=chosen))
        free = shutil.disk_usage(engine.root if engine.root.exists() else engine.root.parent).free
        out.write('\n' + texts['total'] % (human(plan['download_bytes']), human(plan['disk_bytes']), human(free)) + '\n')
        out.write(texts['help'] + '\n')
        out.write(texts['prompt'])
        out.flush()
        answer = stdin.readline()
        if not answer:
            return None
        answer = answer.strip().lower()
        if answer in ('q', 'quit', 'exit', 'в'):
            return None
        if answer in ('i', 'install', 'ю', ''):
            return sorted(chosen)
        if answer == 'a':
            chosen = {row['id'] for row, _ in rows}
        elif answer == 'n':
            chosen = set()
        elif answer == 'l':
            chosen = set(resolve_preset(engine, 'light'))
        else:
            try:
                for number in parse_numbers(answer, len(rows)):
                    ident = rows[number - 1][0]['id']
                    chosen.symmetric_difference_update({ident})
            except ValueError:
                out.write('?\n')


def resolve_preset(engine, name):
    presets = engine.catalog.get('presets', {})
    if name not in presets:
        raise ProvisionError('Unknown preset %s (known: %s)' % (name, ', '.join(presets)))
    models = presets[name]['models']
    return engine.model_ids() if models == 'all' else list(models)


def build_parser():
    parser = argparse.ArgumentParser(prog='Setup-VisualForge', description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--root', help='installation folder (default: the folder that contains App/)')
    parser.add_argument('--catalog', help='component catalog (default: App/config/components.json)')
    parser.add_argument('--lang', choices=('ru', 'en'))
    parser.add_argument('--status', action='store_true', help='show what is installed and exit')
    parser.add_argument('--verify', action='store_true', help='re-hash installed model files and report damage (slow)')
    parser.add_argument('--software-only', action='store_true', help='install the programs, no models')
    parser.add_argument('--all', action='store_true', help='install the programs and every model (about 160 GB)')
    parser.add_argument('--preset', help='a preset from the catalog: software-only, light, everything')
    parser.add_argument('--models', help='comma-separated model component ids to install in addition to the programs')
    parser.add_argument('--module', action='append', default=[], help='install every model of a module (image, video, music, text)')
    parser.add_argument('--text-model', action='append', default=[], help='an Ollama registry model for the Text section, e.g. gemma4:e4b')
    parser.add_argument('--import-ollama', action='append', default=[], metavar='NAME', help='copy a model from your own Ollama (e.g. "mymodel:latest")')
    parser.add_argument('--ollama-source', help='model store of that Ollama (default: OLLAMA_MODELS or %%USERPROFILE%%\\.ollama\\models)')
    parser.add_argument('--yes', '-y', action='store_true', help='do not ask questions')
    parser.add_argument('--force', action='store_true', help='reinstall components that look installed')
    parser.add_argument('--keep-downloads', action='store_true', help='keep downloaded archives and wheels after installing')
    parser.add_argument('--no-space-check', action='store_true')
    parser.add_argument('--dry-run', action='store_true', help='show the plan and exit')
    return parser


def run(argv=None, stdin=None, out=None, engine_factory=Engine, interactive=None):
    parser = build_parser()
    arguments = parser.parse_args(argv)
    out = out or sys.stdout
    texts = TEXT[arguments.lang or guess_language()]
    root = Path(arguments.root) if arguments.root else default_root()
    printer = Printer(out)
    engine = engine_factory(root, arguments.catalog, emit=printer, keep_downloads=arguments.keep_downloads)
    if len(str(root)) > 100:
        out.write(texts['longpath'] % len(str(root)) + '\n')
    if arguments.status:
        for module in engine.describe():
            out.write('%s\n' % module['title'])
            for row in module['components']:
                out.write('   %-34s %-9s %-10s %s\n' % (row['id'], row['kind'], state_label(texts, row['state']), human(row['download_bytes'])))
        return 0
    wanted = list(arguments.models.split(',')) if arguments.models else []
    for tag in arguments.text_model:
        wanted.append(engine.text_model_component(tag))
    for name in arguments.import_ollama:
        wanted.append(engine.ollama_import_component(name, arguments.ollama_source))
    if arguments.preset:
        wanted += resolve_preset(engine, arguments.preset)
    if arguments.verify:
        problems = engine.verify([w for w in wanted] or None)
        bad = {k: v for k, v in problems.items() if v}
        for ident, found in bad.items():
            out.write('%s:\n' % ident + ''.join('   %s\n' % f for f in found))
        out.write('OK\n' if not bad else '%d component(s) have problems\n' % len(bad))
        return 1 if bad else 0
    explicit = bool(wanted or arguments.all or arguments.software_only or arguments.module)
    if interactive is None:
        interactive = not explicit and not arguments.yes and (stdin or sys.stdin).isatty()
    if not explicit and not interactive:
        out.write(texts['noterm'] + '\n')
        return 2
    if interactive:
        out.write(texts['first'] + '\n' if not engine.first_run_done() else '')
        picked = menu(engine, texts, [i for i in engine.model_ids() if engine.status()[i]['state'] == 'installed'], stdin, out)
        if picked is None:
            out.write(texts['bye'] + '\n')
            return 0
        wanted = picked
    ids = engine.select(models=wanted, all_models=arguments.all, modules=arguments.module)
    plan = engine.plan(ids, arguments.force)
    if arguments.dry_run:
        for ident in plan['todo']:
            out.write('%s  %s\n' % (ident, human(engine.status()[ident]['remaining_bytes'])))
        out.write('download %s, disk about %s\n' % (human(plan['download_bytes']), human(plan['disk_bytes'])))
        return 0
    if not plan['todo']:
        out.write(texts['nothing'] + '\n')
        engine.mark_first_run(wanted)
        return 0
    if sys.platform == 'win32' and not arguments.yes:
        from .recovery_picker import standalone
        engine.recover = standalone(root)
    out.write(texts['start'] + '\n')
    try:
        results = engine.install(ids, arguments.force, check_space=not arguments.no_space_check)
    except NotEnoughSpace as error:
        out.write(str(error) + '\n')
        return 3
    except Busy:
        out.write(texts['busy'] + '\n')
        return 4
    except net.Cancelled:
        out.write(texts['cancelled'] + '\n')
        return 130
    failed = {k: v for k, v in results.items() if v.startswith('failed')}
    engine.mark_first_run(wanted)
    out.write('\n')
    for ident, outcome in results.items():
        out.write('  %-34s %s\n' % (ident, outcome))
    if failed:
        out.write(texts['failed'] % ', '.join(failed) + '\n')
        return 1
    out.write(texts['done'] + '\n' + texts['later'] + '\n')
    return 0


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError):
            pass
    try:
        code = run()
    except KeyboardInterrupt:
        code = 130
    sys.exit(code)
