"""Agent programs the Text tab can drive and the permission levels a chat can choose.

Only an agent whose executable is present is offered as ready; the others are listed as planned so the page can say so."""
from ui_strings import source as ui_text
AGENTS = {
    'opencode': dict(id='opencode', name='OpenCode', version='1.19.4', license='MIT', source='https://github.com/anomalyco/opencode',
                     executable='Runtime/Agents/opencode/opencode.exe', planned=False,
                     description=ui_text('agent.registry_agents_code_git_agent_reads_edits_project')),
    'aider': dict(id='aider', name='Aider', version='0.86.2', license='Apache-2.0', source='https://github.com/Aider-AI/aider',
                  executable='Runtime/Agents/aider/python/python.exe', planned=False,
                  description=ui_text('agent.registry_agents_code_editing_agent_git_folders_shows')),
    'dsh': dict(id='dsh', name='DeepSeek Harness', version='0.2.1-rc.2', license='MIT', source='https://github.com/deepseek-ai/deepseek-harness',
                executable='Runtime/Agents/dsh/node/node.exe', planned=False,
                description=ui_text('agent.registry_agents_modular_deep_seek_plugin_agent_preview')),
}
DEFAULT_AGENT = 'opencode'

# What the agent may do without asking. `git push` and anything outside the project folder stay denied in every mode; network tools
# (web fetch/search) are always off because the studio works offline.
PERMISSION_MODES = {
    'read': ui_text('agent.registry_permission_modes_read'),
    'ask': ui_text('agent.registry_permission_modes_ask_before_actions'),
    'auto': ui_text('agent.registry_permission_modes_auto_edit_commit_without_asking'),
}
PERMISSION_HINTS = {
    'read': ui_text('agent.registry_permission_hints_read_agent_reads_searches_project_files_edits'),
    'ask': ui_text('agent.registry_permission_hints_ask_agent_asks_before_every_file_edit'),
    'auto': ui_text('agent.registry_permission_hints_auto_file_edits_git_status_diff_log'),
}
DEFAULT_PERMISSION = 'ask'


def get_agent(ident):
    if ident not in AGENTS:
        raise ValueError(ui_text('agent.agent_selection_unknown_identifier_error'))
    return AGENTS[ident]
