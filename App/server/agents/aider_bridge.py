"""One Aider task for the studio, run by Aider's own Python (Runtime/Agents/aider/python); imports only the standard library and Aider.

Protocol, UTF-8 JSON lines. The studio writes one `start` message to stdin (read through a private duplicate, see REPLIES), then
`reply` messages for permission requests. The bridge writes page events (status, delta, step, permission, permission_done) and finally `done` or `error` to its original stdout.
Aider prints the model's streamed answer with sys.stdout.write, so sys.stdout is replaced by a writer that turns that text into
`delta` events; the protocol keeps a duplicate of the original stdout handle and file descriptor 1 is pointed at stderr (the log),
so stray prints of Aider or of libraries can never corrupt the protocol.

Aider has no permission rules of its own: edits are applied as soon as the model writes them. The bridge therefore asks before
`prepare_to_edit` (one request per answer, with the edit blocks as preview), answers Aider's own yes/no questions itself, runs shell
commands with its own runner (git push refused) and allows network connections only to the studio's model gateway."""
import difflib
import hashlib
import json
import os
import pathlib
import re
import socket
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit

PROTOCOL = os.fdopen(os.dup(1), 'w', encoding='utf-8', newline='\n')
os.dup2(2, 1)
# Replies are read by a thread blocked on the stdin pipe. While a synchronous read is pending on that pipe, loading a DLL in another
# thread hung (numpy's OpenBLAS, imported for Aider's repo map: the C runtime of a loading DLL queries the standard handles). The
# reader keeps a private duplicate of the pipe and the process's standard input becomes NUL, which nothing ever waits on.
REPLIES = os.fdopen(os.dup(0), 'rb', buffering=0)
os.dup2(os.open(os.devnull, os.O_RDONLY), 0)
if os.name == 'nt':
    import ctypes
    import msvcrt
    ctypes.windll.kernel32.SetStdHandle(-10, msvcrt.get_osfhandle(0))  # STD_INPUT_HANDLE

MAX_OUTPUT = 4000
COMMAND_TIMEOUT = 600
PUSH = re.compile(r'\bgit\b[^\n;&|]*\bpush\b')
# The same commands the `auto` level lets OpenCode run without asking.
AUTO_COMMANDS = re.compile(r'^git (status|diff|log|add|commit)(\s|$)')
REFUSED = 'Запрещено правами агента в этом чате.'


def clip(text, limit=MAX_OUTPUT):
    text = str(text or '')
    return text if len(text) <= limit else text[:limit] + '\n… (обрезано: ещё %d симв.)' % (len(text) - limit)


def decode(raw):
    # cmd.exe prints in the OEM code page (cp866 on Russian Windows); git and Python usually print UTF-8.
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError:
        return raw.decode('oem', 'replace')


class Bridge:
    def __init__(self, start):
        self.start = start
        self.workspace = pathlib.Path(start['workspace']).resolve()
        self.mode = start['mode']
        self.writing = threading.Lock()
        self.pending = {}
        self.counter = 0
        self.always_edit = False
        self.always_commands = set()
        self.approved = set()
        self.content, self.thinking = [], []
        self.calls = self.output_tokens = self.context_tokens = 0
        self.errors = []
        self.streaming = False
        self.in_thinking = False
        self.reasoning_tag = ''
        self.wrapping = False  # the studio's loop asked to end the iteration: no further model call of the task
        self.handoff = False

    # ---- protocol -----------------------------------------------------------------------------------------------
    def emit(self, event):
        with self.writing:
            PROTOCOL.write(json.dumps(event, ensure_ascii=False) + '\n')
            PROTOCOL.flush()

    def next_id(self, prefix):
        self.counter += 1
        return '%s_%d' % (prefix, self.counter)

    def step(self, **step):
        self.emit(dict(type='step', step=step))
        return step

    def read_replies(self):
        for line in REPLIES:
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if message.get('type') == 'wrap':
                self.wrapping = True
            elif message.get('type') == 'reply' and message.get('id') in self.pending:
                self.pending[message['id']][1] = message.get('reply')
                self.pending[message['id']][0].set()
        # The studio closed the pipe: nobody can answer any more, so every open question is a refusal.
        for waiter in list(self.pending.values()):
            waiter[1] = waiter[1] or 'reject'
            waiter[0].set()

    def ask(self, **request):
        request.update(id=self.next_id('per'), patterns=request.get('patterns') or [], always=request.get('always') or [])
        waiter = [threading.Event(), None]
        self.pending[request['id']] = waiter
        self.emit(dict(type='permission', request=request))
        waiter[0].wait()
        self.pending.pop(request['id'], None)
        reply = waiter[1] if waiter[1] in ('once', 'always', 'reject') else 'reject'
        self.emit(dict(type='permission_done', id=request['id'], reply=reply))
        return reply

    # ---- the model's text (captured sys.stdout) -------------------------------------------------------------------
    def on_text(self, text):
        if not self.streaming or not text:
            sys.stderr.write(text)
            return
        open_tag, close_tag = '<%s>' % self.reasoning_tag, '</%s>' % self.reasoning_tag
        # Aider wraps a streamed reasoning field in its own tags, each written whole; split the chunk at them.
        for piece in re.split('(%s|%s)' % (re.escape(open_tag), re.escape(close_tag)), text):
            if piece == open_tag:
                self.in_thinking = True
            elif piece == close_tag:
                self.in_thinking = False
            elif piece:
                (self.thinking if self.in_thinking else self.content).append(piece)
                self.emit(dict(type='delta', content='' if self.in_thinking else piece, thinking=piece if self.in_thinking else ''))

    # ---- permissions --------------------------------------------------------------------------------------------
    def inside(self, path):
        try:
            pathlib.Path(path).resolve().relative_to(self.workspace)
            return True
        except ValueError:
            return False

    def allow_read(self, coder, relative):
        if not self.inside(coder.abs_root_path(relative)):
            self.step(id=self.next_id('step'), tool='read', status='error', title=relative, file=relative, error=REFUSED)
            return False
        self.step(id=self.next_id('step'), tool='read', status='completed', title=relative, file=relative, output='Файл добавлен в разговор с моделью.')
        return True

    def allow_command(self, commands):
        lines = [line.strip() for line in str(commands or '').splitlines() if line.strip() and not line.strip().startswith('#')]
        if not lines:
            return False
        refused = self.mode == 'read' or any(PUSH.search(line) for line in lines)
        if refused:
            self.step(id=self.next_id('step'), tool='bash', status='error', title=lines[0][:300], command='\n'.join(lines)[:2000], error=REFUSED)
            return False
        if self.mode == 'auto' and all(AUTO_COMMANDS.match(line) for line in lines):
            return True
        if all(line in self.always_commands for line in lines):
            return True
        reply = self.ask(permission='bash', command='\n'.join(lines)[:2000], patterns=lines[:20], always=lines[:20])
        if reply == 'always':
            self.always_commands.update(lines)
        if reply == 'reject':
            self.step(id=self.next_id('step'), tool='bash', status='error', title=lines[0][:300], command='\n'.join(lines)[:2000], error='Вы отклонили это действие.')
        return reply != 'reject'

    def approve_edits(self, files, preview):
        if self.mode == 'read':
            return False
        if self.mode == 'auto' or self.always_edit:
            return True
        reply = self.ask(permission='edit', patterns=files[:20], file=files[0] if len(files) == 1 else '', diff=clip(preview))
        if reply == 'always':
            self.always_edit = True
        return reply != 'reject'

    # ---- hooks into Aider ---------------------------------------------------------------------------------------
    def run_command(self, command, verbose=False, error_print=None, cwd=None):
        """Replaces aider.run_cmd.run_cmd: runs one approved command without a console, captures its output as a step."""
        step = self.step(id=self.next_id('step'), tool='bash', status='running', title=command[:300], command=command[:2000])
        if PUSH.search(command):
            self.step(**dict(step, status='error', error=REFUSED))
            return 1, 'Refused by the studio: git push is not allowed.'
        try:
            done = subprocess.run(command, shell=True, cwd=cwd or str(self.workspace), stdin=subprocess.DEVNULL, capture_output=True,
                                  timeout=COMMAND_TIMEOUT, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except subprocess.TimeoutExpired:
            self.step(**dict(step, status='error', error='Команда не завершилась за %d с и остановлена.' % COMMAND_TIMEOUT))
            return 1, 'Command timed out after %d seconds.' % COMMAND_TIMEOUT
        except OSError as error:
            self.step(**dict(step, status='error', error=clip(error, 1000)))
            return 1, str(error)
        output = (decode(done.stdout) + decode(done.stderr)).strip()
        self.step(**dict(step, status='completed', output=clip(output + ('\n(код выхода %d)' % done.returncode if done.returncode else ''))))
        return done.returncode, output

    def guard(self, coder):
        bridge = self
        prepare, apply_edits, send, count = coder.prepare_to_edit, coder.apply_edits, coder.send, coder.calculate_and_show_tokens_and_cost

        def prepare_to_edit(edits):
            files = sorted({edit[0] for edit in edits if edit[0]})
            outside = [path for path in files if not bridge.inside(coder.abs_root_path(path))]
            for path in outside:
                bridge.step(id=bridge.next_id('step'), tool='edit', status='error', title=path, file=path, error=REFUSED)
            edits = [edit for edit in edits if edit[0] not in outside]
            files = [path for path in files if path not in outside]
            if not files:
                return [edit for edit in edits if not edit[0]]
            preview = '\n\n'.join(block_preview(edit) for edit in edits if edit[0])
            if not bridge.approve_edits(files, preview):
                for path in files:
                    bridge.step(id=bridge.next_id('step'), tool='edit', status='error', title=path, file=path,
                                error='Вы отклонили это действие.' if bridge.mode != 'read' else REFUSED)
                return []
            bridge.approved.update(files)
            return prepare(edits)

        def guarded_apply(edits, **keywords):
            if keywords.get('dry_run'):
                return apply_edits(edits, **keywords)
            files = sorted({edit[0] for edit in edits if edit[0]})
            before = {path: read(coder.abs_root_path(path)) for path in files}
            result = apply_edits(edits, **keywords)
            for path in files:
                diff = ''.join(difflib.unified_diff(before[path].splitlines(True), read(coder.abs_root_path(path)).splitlines(True), path, path))
                bridge.step(id=bridge.next_id('step'), tool='edit', status='completed', title=path, file=path, output='Файл изменён.', diff=clip(diff))
            return result

        def guarded_send(*args, **keywords):
            if bridge.wrapping and not bridge.handoff:
                # Aider's own Ctrl+C path: send_message catches it, marks the reply interrupted and ends the message cleanly.
                raise KeyboardInterrupt
            bridge.streaming = True
            try:
                yield from send(*args, **keywords)
            finally:
                bridge.streaming = False
                bridge.in_thinking = False

        def counted(messages, completion=None):
            sent, received = coder.message_tokens_sent, coder.message_tokens_received
            result = count(messages, completion)
            bridge.calls += 1
            bridge.output_tokens += coder.message_tokens_received - received
            bridge.context_tokens = coder.message_tokens_sent - sent
            return result

        coder.prepare_to_edit, coder.apply_edits, coder.send, coder.calculate_and_show_tokens_and_cost = prepare_to_edit, guarded_apply, guarded_send, counted


def read(path):
    try:
        return pathlib.Path(path).read_text(encoding='utf-8', errors='replace')
    except OSError:
        return ''


def block_preview(edit):
    """An edit of the diff format is (path, original, updated); show it as a diff of the two snippets."""
    if len(edit) == 3 and isinstance(edit[1], str) and isinstance(edit[2], str):
        return ''.join(difflib.unified_diff(edit[1].splitlines(True), edit[2].splitlines(True), edit[0], edit[0]))
    return '%s: файл будет записан целиком' % edit[0]


class Capture:
    """sys.stdout for Aider: text written while the model streams is the answer, anything else goes to the log."""
    encoding = 'utf-8'
    errors = 'replace'

    def __init__(self, bridge):
        self.bridge = bridge

    def write(self, text):
        self.bridge.on_text(str(text))
        return len(text)

    def flush(self):
        pass

    def isatty(self):
        return False

    def fileno(self):
        return 1


def guard_network(base_url):
    """Only the studio gateway is reachable: no update checks, model price lists, telemetry or tokenizer downloads."""
    parts = urlsplit(base_url)
    allowed = (parts.hostname, parts.port)
    own = set()  # sockets this process listens on itself
    real_bind, real_connect, real_connect_ex, real_getaddrinfo = socket.socket.bind, socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo

    def bind(self, address):
        result = real_bind(self, address)
        if self.family in (socket.AF_INET, socket.AF_INET6):
            own.add(tuple(self.getsockname()[:2]))
        return result

    def check(address):
        # asyncio's event loop on Windows talks to itself through socket.socketpair(), a loopback connection to a socket it has just
        # bound; refusing it leaves the loop half-built ("no attribute '_ssock'"), so connections to the process's own sockets pass.
        if isinstance(address, tuple) and (tuple(address[:2]) == allowed or tuple(address[:2]) in own):
            return
        sys.stderr.write('VisualForge: refused a network connection to %s\n' % (address,))
        raise OSError('VisualForge: network access is disabled for the agent (%s)' % (address,))

    def connect(self, address):
        check(address)
        return real_connect(self, address)

    def connect_ex(self, address):
        check(address)
        return real_connect_ex(self, address)

    def getaddrinfo(host, *args, **keywords):
        if host not in (allowed[0], None):
            sys.stderr.write('VisualForge: refused a name lookup of %s\n' % host)
            raise socket.gaierror('VisualForge: network access is disabled for the agent (%s)' % host)
        return real_getaddrinfo(host, *args, **keywords)

    socket.socket.bind, socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo = bind, connect, connect_ex, getaddrinfo


def main():
    start = json.loads(REPLIES.readline())
    bridge = Bridge(start)
    threading.Thread(target=bridge.read_replies, name='replies', daemon=True).start()
    state = pathlib.Path(start['state'])
    state.mkdir(parents=True, exist_ok=True)
    # Aider keeps caches and its analytics id under Path.home()/.aider; the real profile stays in the environment for git.
    pathlib.Path.home = classmethod(lambda cls: cls(state))
    os.environ.update(OPENAI_API_BASE=start['base_url'], OPENAI_API_KEY=start['token'], LITELLM_LOCAL_MODEL_COST_MAP='True')
    guard_network(start['base_url'])
    sys.stdout = Capture(bridge)
    bridge.emit(dict(type='status', phase='Aider читает проект…'))

    from aider import models
    from aider.coders import Coder, base_coder
    from aider.io import InputOutput
    from aider.repo import GitRepo
    from aider.repomap import RepoMap

    class StudioIO(InputOutput):
        """Aider's terminal I/O, answered by the bridge: nothing is printed and every question has a policy."""
        coder = None

        def confirm_ask(self, question, default='y', subject=None, explicit_yes_required=False, group=None, allow_never=False):
            question = str(question).strip()
            if bridge.handoff:
                return False  # the handoff note is a plain answer: no files added, nothing created or run
            if question in ('Run shell command?', 'Run shell commands?'):
                return bridge.allow_command(subject)
            if question == 'Add file to the chat?':
                return bridge.allow_read(self.coder, subject)
            if question in ('Create new file?', 'Allow edits to file that has not been added to the chat?'):
                return subject in bridge.approved  # asked inside prepare_to_edit, after the user approved these files
            return question == 'Add command output to the chat?'

        def prompt_ask(self, question, default='', subject=None):
            return default

        def tool_output(self, *messages, log_only=False, bold=False):
            pass

        def tool_warning(self, message='', strip=True):
            sys.stderr.write('aider warning: %s\n' % message)

        def tool_error(self, message='', strip=True):
            sys.stderr.write('aider error: %s\n' % message)
            if str(message).strip():
                bridge.errors.append(str(message).strip())

        def assistant_output(self, message, pretty=None):
            pass

        def rule(self):
            pass

        def offer_url(self, url, prompt='', allow_never=True):
            return False

        def ring_bell(self):
            pass

    base_coder.run_cmd = bridge.run_command
    # Raw reasoning tags in the stream let the bridge tell thinking from the answer (Aider would turn them into markdown headings).
    base_coder.replace_reasoning_tags = lambda text, tag_name: text
    workspace = str(bridge.workspace)
    RepoMap.TAGS_CACHE_DIR = str(state / 'tags' / hashlib.sha1(workspace.lower().encode('utf-8')).hexdigest()[:16])
    name = 'openai/' + start['alias']
    output = int(start['output'])
    models.model_info_manager.local_model_metadata[name] = dict(
        max_input_tokens=int(start['context']), max_output_tokens=output, max_tokens=output, input_cost_per_token=0.0,
        output_cost_per_token=0.0, litellm_provider='openai', mode='chat')
    model = models.Model(name, weak_model=name, editor_model=name)
    # Live run on Gemma 4 A4B: with Aider's temperature 0 the model reasoned in a loop until the model's num_predict (8192) and
    # never answered; LiteLLM also drops Ollama's `reasoning` field, so the user would have watched a silent minute. Aider's edit
    # format wants a direct answer: reasoning is switched off (extra_body, which LiteLLM's drop_params leaves alone) and the
    # model keeps its own sampling settings (temperature 1, top_k 64 in its Ollama parameters).
    for each in {id(m): m for m in (model, model.weak_model, model.editor_model) if m is not None}.values():
        each.use_temperature = False
        each.extra_params = dict(each.extra_params or {}, extra_body=dict(reasoning_effort='none'))
    io = StudioIO(pretty=False, fancy_input=False, chat_history_file=None, input_history_file=None, encoding='utf-8', root=workspace)
    # `subtree_only` keeps the files below the *current directory*: started from the state folder, Aider saw no file of the
    # repository at all, so a model asking for `calc.py` never got it added. The project folder is the subtree.
    os.chdir(workspace)
    try:
        # Commits keep the user's own git identity: no "(aider)" names and no co-author lines.
        repo = GitRepo(io, [], workspace, models=model.commit_message_models(), attribute_author=False, attribute_committer=False,
                       attribute_commit_message_author=False, attribute_commit_message_committer=False, attribute_co_authored_by=False,
                       subtree_only=True)
    except FileNotFoundError:
        repo = None  # not a git folder: edits still work, nothing is committed
    system = start.get('system', '').strip()
    coder = Coder.create(main_model=model, edit_format='ask' if bridge.mode == 'read' else 'diff', io=io, repo=repo, fnames=[],
                         use_git=repo is not None, auto_commits=bridge.mode == 'auto' and repo is not None, dirty_commits=False,
                         stream=True, map_tokens=1024 if repo is not None else 0, auto_lint=False, auto_test=False,
                         suggest_shell_commands=bridge.mode != 'read', detect_urls=False, chat_language=None if system else 'Russian',
                         done_messages=[dict(role=m['role'], content=m['content']) for m in start.get('history', [])])
    io.coder = coder
    bridge.reasoning_tag = coder.reasoning_tag_name
    bridge.guard(coder)
    task = start['task'] if not system else '%s\n\n%s' % (system, start['task'])
    coder.run(with_message=task)
    content, thinking = ''.join(bridge.content).strip(), ''.join(bridge.thinking).strip()
    stats = dict(model_calls=bridge.calls, completion_tokens=bridge.output_tokens, context_tokens=bridge.context_tokens,
                 context=int(start['context'])) if bridge.calls else None
    handoff = ''
    if start.get('handoff'):
        # A loop iteration ends with a handoff note in the same context: a plain answer, so the read-only level for this one
        # message (no edits, commands or added files); its text is returned apart from the iteration's answer.
        bridge.emit(dict(type='phase', phase='handoff'))
        bridge.handoff, bridge.mode, bridge.content, bridge.thinking = True, 'read', [], []
        coder.run(with_message=start['handoff'])
        handoff = ''.join(bridge.content).strip()
    bridge.emit(dict(type='done', content=content, thinking=thinking, stats=stats, handoff=handoff,
                     error='' if content or not bridge.errors else clip(bridge.errors[-1], 500),
                     commits=sorted(getattr(coder, 'aider_commit_hashes', None) or [])))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:  # reported to the studio; the traceback goes to the log
        import traceback
        traceback.print_exc()
        try:
            PROTOCOL.write(json.dumps(dict(type='error', message=clip('%s: %s' % (type(error).__name__, error), 500)), ensure_ascii=False) + '\n')
            PROTOCOL.flush()
        except OSError:
            pass
        sys.exit(1)
    sys.exit(0)
