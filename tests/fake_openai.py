"""A tiny stand-in for an OpenAI-compatible chat server (Ollama's /v1) used by the agent tests: streaming chunks, one tool call, no model.

The last message decides the answer: a user message containing TOOL asks the `bash` tool for `git status` (PUSH: for `git push`), a tool result is acknowledged,
anything else is echoed. SLOW streams for a few seconds so a test can stop it midway.

Aider reads edits and shell commands from the text itself: AIDER_EDIT answers with a SEARCH/REPLACE block for calc.py, AIDER_OUT with
one for a file outside the project, AIDER_ASK asks for calc.py to be added, AIDER_RUN / AIDER_PUSH with a ```bash block (`git status` / `git push origin main`); Aider's
request for a commit message gets a fixed one-line message.

RUN_CMD: <command> ## makes any agent run that shell command (a tool call, or a ```bash block for Aider).

dsh calls tools step by step: DSH_EDIT reads calc.py, then edits it; DSH_OUT writes a file outside the project; DSH_RUN / DSH_PUSH
call its shell tool (`pwsh` on Windows) with `git status` / `git push origin main`.

Loops: a request containing "Stop working on the task now" gets a handoff note (Status DONE from the second iteration on);
LOOP_BIG reports a 30000-token prompt (past a loop's threshold); LOOP_TOOLS keeps calling the shell tool (Aider: names calc.py)."""
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOG = os.environ.get('FAKE_OPENAI_LOG')


def _text(content):
    if isinstance(content, list):
        return ''.join(part.get('text', '') for part in content if isinstance(part, dict))
    return content or ''


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *args):
        pass

    def reply(self, status, value):
        raw = json.dumps(value).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path.rstrip('/') == '/v1/models':
            return self.reply(200, {'object': 'list', 'data': [{'id': 'fake-model', 'object': 'model', 'owned_by': 'fake'}]})
        self.reply(404, {'error': {'message': 'not found'}})

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0') or 0)) or b'{}')
        if LOG:
            with open(LOG, 'a', encoding='utf-8') as stream:
                stream.write(json.dumps({'path': self.path, 'auth': self.headers.get('Authorization', ''), 'body': data}, ensure_ascii=False) + '\n')
        if self.path != '/v1/chat/completions':
            return self.reply(404, {'error': {'message': 'not found'}})
        last = (data.get('messages') or [{}])[-1]
        question = _text(last.get('content'))
        if question.startswith('I added these files to the chat'):
            # Aider adds the files a reply names and asks again; a model then repeats its answer for the task before it.
            users = [_text(m.get('content')) for m in data['messages'][:-1] if m.get('role') == 'user']
            question = users[-1] if users else question
        if 'FAIL' in question:
            return self.reply(500, {'error': {'message': 'model failed to load: out of memory'}})
        tool_names = {tool.get('function', {}).get('name') for tool in data.get('tools') or []}
        system = ' '.join(_text(m.get('content')) for m in data.get('messages') or [] if m.get('role') == 'system')
        fence = '`' * 3
        shell = 'bash' if 'bash' in tool_names else 'pwsh' if 'pwsh' in tool_names else None
        # Tool-calling agents (dsh) work in steps: the task is the last run of user messages (dsh adds its reminders as separate user
        # messages after the request), the step is the number of tool results after it.
        messages = data.get('messages') or []
        start = max((i for i, m in enumerate(messages) if m.get('role') == 'user'), default=-1)
        while start > 0 and messages[start - 1].get('role') == 'user':
            start -= 1
        users = [_text(m.get('content')) for m in messages[start:] if m.get('role') == 'user'] if start >= 0 else []
        task, done_tools = ' '.join(users), sum(1 for m in messages[start + 1:] if m.get('role') == 'tool')
        plan = []
        if 'DSH_EDIT' in task:
            plan = [('read', {'file_path': 'calc.py'}), ('edit', {'file_path': 'calc.py', 'old_string': 'return a - b', 'new_string': 'return a + b'})]
        elif 'DSH_OUT' in task:
            plan = [('write', {'file_path': '../outside.txt', 'content': 'outside\n'})]
        elif 'RUN_CMD:' in task and shell:
            plan = [(shell, {'description': 'Команда из задания', 'command': task.split('RUN_CMD:', 1)[1].split('##', 1)[0].strip()})]
        elif 'DSH_CMD:' in task:
            plan = [(shell, {'description': 'Команда из задания', 'command': task.split('DSH_CMD:', 1)[1].split('##', 1)[0].strip()})]
        elif 'DSH_RUN' in task or 'DSH_PUSH' in task:
            plan = [(shell, {'description': 'Показать состояние репозитория', 'command': 'git push origin main' if 'DSH_PUSH' in task else 'git status'})]
        everything = ' '.join(_text(m.get('content')) for m in messages)
        prompt_tokens = 30000 if 'LOOP_BIG' in everything and 'Stop working on the task now' not in task else 12
        if 'Stop working on the task now' in task:
            # The studio's request for a loop iteration's handoff note: the second iteration reports the task done.
            done = 'iteration 2 of' in everything
            note = '## Done\n- iteration work\n\n## Next step\n- %s\n\n## Blocker\n- none\n\n## Status\n%s\n' % (
                'nothing' if done else 'finish the remaining part', 'DONE' if done else 'CONTINUE')
            chunks = [{'content': note}]
        elif 'LOOP_TOOLS' in task and shell and done_tools < 50:
            # An agent that would keep calling tools forever: only the studio's context threshold ends its iteration.
            arguments = json.dumps({'command': 'git status', 'description': 'Показать состояние репозитория'})
            chunks = [{'tool_calls': [{'index': 0, 'id': 'call_loop_%d' % done_tools, 'type': 'function', 'function': {'name': shell, 'arguments': arguments}}]}]
        elif 'LOOP_TOOLS' in question and not tool_names:
            chunks = [{'content': 'Посмотрю calc.py, затем продолжу.'}]
        elif 'LOOP_' in task:
            chunks = [{'content': 'Итерация: часть работы сделана.'}]
        elif 'commit message' in system.lower():
            chunks = [{'content': 'fix: make add return the sum'}]
        elif 'AIDER_EDIT' in question or 'AIDER_OUT' in question:
            name = 'calc.py' if 'AIDER_EDIT' in question else '../outside.txt'
            block = '%s\n%spython\n<<<<<<< SEARCH\n    return a - b\n=======\n    return a + b\n>>>>>>> REPLACE\n%s\n' % (name, fence, fence)
            chunks = [{'content': 'Исправляю сложение.\n\n'}, {'content': block}]
        elif 'AIDER_ASK' in question:
            chunks = [{'content': 'Добавьте, пожалуйста, calc.py в чат.'}]
        elif 'RUN_CMD:' in question and not tool_names:
            command = question.split('RUN_CMD:', 1)[1].split('##', 1)[0].strip()
            chunks = [{'content': 'Выполните:\n\n'}, {'content': '%sbash\n%s\n%s\n' % (fence, command, fence)}]
        elif 'AIDER_RUN' in question or 'AIDER_PUSH' in question:
            command = 'git push origin main' if 'AIDER_PUSH' in question else 'git status'
            chunks = [{'content': 'Выполните:\n\n'}, {'content': '%sbash\n%s\n%s\n' % (fence, command, fence)}]
        elif plan and done_tools < len(plan):
            name, arguments = plan[done_tools]
            chunks = [{'tool_calls': [{'index': 0, 'id': 'call_fake_%d' % (done_tools + 1), 'type': 'function',
                                       'function': {'name': name, 'arguments': json.dumps(arguments, ensure_ascii=False)}}]}]
        elif last.get('role') == 'tool':
            chunks = [{'content': 'Готово: '}, {'content': question[:40]}]
        elif ('TOOL' in question or 'PUSH' in question) and shell:
            command = 'git push origin main' if 'PUSH' in question else 'git status'
            arguments = json.dumps({'command': command, 'description': 'Показать состояние репозитория'})
            chunks = [{'tool_calls': [{'index': 0, 'id': 'call_fake_1', 'type': 'function', 'function': {'name': shell, 'arguments': arguments}}]}]
        elif 'SLOW' in question or 'SLOW' in task:
            chunks = [{'content': 'слово%d ' % i} for i in range(60)]
        else:
            chunks = [{'content': 'Эхо: '}, {'content': question[:40]}]
        if not data.get('stream'):
            text = ''.join(chunk.get('content', '') for chunk in chunks)
            message = {'role': 'assistant', 'content': text}
            if 'tool_calls' in chunks[0]:
                message['tool_calls'] = chunks[0]['tool_calls']
            return self.reply(200, {'id': 'fake', 'object': 'chat.completion', 'created': int(time.time()), 'model': data.get('model'),
                                    'choices': [{'index': 0, 'message': message, 'finish_reason': 'tool_calls' if 'tool_calls' in message else 'stop'}],
                                    'usage': {'prompt_tokens': prompt_tokens, 'completion_tokens': len(chunks), 'total_tokens': prompt_tokens + len(chunks)}})
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Connection', 'close')
        self.end_headers()
        self.close_connection = True
        try:
            for index, delta in enumerate(chunks):
                self.chunk(data, dict(delta, role='assistant') if index == 0 else delta, None)
                if 'SLOW' in question or 'SLOW' in task:
                    time.sleep(0.1)
            self.chunk(data, {}, 'tool_calls' if 'tool_calls' in chunks[0] else 'stop',
                       usage={'prompt_tokens': prompt_tokens, 'completion_tokens': len(chunks), 'total_tokens': prompt_tokens + len(chunks)})
            self.wfile.write(b'data: [DONE]\n\n')
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def chunk(self, data, delta, finish, usage=None):
        value = {'id': 'fake', 'object': 'chat.completion.chunk', 'created': int(time.time()), 'model': data.get('model'),
                 'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}]}
        if usage:
            value['usage'] = usage
        self.wfile.write(b'data: ' + json.dumps(value, ensure_ascii=False).encode('utf-8') + b'\n\n')
        self.wfile.flush()


if __name__ == '__main__':
    ThreadingHTTPServer(('127.0.0.1', int(sys.argv[1])), Handler).serve_forever()
