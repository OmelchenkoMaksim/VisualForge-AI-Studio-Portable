"""Agent programs the Text tab can drive and the permission levels a chat can choose.

Only an agent whose executable is present is offered as ready; the others are listed as planned so the page can say so."""
AGENTS = {
    'opencode': dict(id='opencode', name='OpenCode', version='1.19.4', license='MIT', source='https://github.com/anomalyco/opencode',
                     executable='Runtime/Agents/opencode/opencode.exe', planned=False,
                     description='Агент для кода и git: читает и правит файлы проекта, запускает команды в терминале.'),
    'aider': dict(id='aider', name='Aider', version='0.86.2', license='Apache-2.0', source='https://github.com/Aider-AI/aider',
                  executable='Runtime/Agents/aider/python/python.exe', planned=False,
                  description='Агент для правок кода в git-папке: показывает правки блоками «было/стало»; при уровне «без вопросов» сам '
                              'коммитит каждую правку, при «спрашивать» оставляет их незакоммиченными.'),
    'dsh': dict(id='dsh', name='DeepSeek Harness', version='0.2.1-rc.2', license='MIT', source='https://github.com/deepseek-ai/deepseek-harness',
                executable='Runtime/Agents/dsh/node/node.exe', planned=False,
                description='Модульный агент DeepSeek на плагинах (предварительная версия): читает и правит файлы, запускает команды '
                            'PowerShell.'),
}
DEFAULT_AGENT = 'opencode'

# What the agent may do without asking. `git push` and anything outside the project folder stay denied in every mode; network tools
# (web fetch/search) are always off because the studio works offline.
PERMISSION_MODES = {
    'read': 'Только чтение',
    'ask': 'Спрашивать перед действиями',
    'auto': 'Правки и коммиты без вопросов',
}
PERMISSION_HINTS = {
    'read': 'Агент читает и ищет по файлам проекта; правки и команды запрещены.',
    'ask': 'Перед каждой правкой файла и каждой командой агент спрашивает разрешения.',
    'auto': 'Правки файлов, git status/diff/log/add/commit — без вопросов; остальные команды — по запросу.',
}
DEFAULT_PERMISSION = 'ask'


def get_agent(ident):
    if ident not in AGENTS:
        raise ValueError('Неизвестный агент')
    return AGENTS[ident]
