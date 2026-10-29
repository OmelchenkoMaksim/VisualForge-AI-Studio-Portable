'use strict';
/* Agent mode of the Text tab: the agent panel (agent, project folder, permissions) and the rendering of agent steps and
   permission requests. text-chat.js owns the chat list, sending and streaming; it calls these helpers for agent chats. */
window.StudioAgent = (() => {
  const el = id => document.getElementById(id);
  let info = null, listener = null;
  const api = async (route, data) => {
    let response;
    try {response = await fetch('/api/agents/' + route, data === undefined ? {} : {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)});}
    catch {throw new Error(StudioI18n.t("agent.api_request_studio_connection_failed"));}
    let value;
    try {value = await response.json();} catch {throw new Error(StudioI18n.t("agent.api_request_invalid_response_error"));}
    if (!response.ok) throw new Error(StudioI18n.remote(value.error) || StudioI18n.t("agent.api_request_agent_mode_unavailable"));
    return value;
  };
  const STATUS = {pending: StudioI18n.t("agent.agent_step_preparing_status"), running: StudioI18n.t("agent.agent_step_running_status"), completed: StudioI18n.t("agent.agent_step_completed_status"), error: StudioI18n.t("agent.agent_step_failed_status")};
  const TOOLS = {bash: StudioI18n.t("agent.tools_bash_command"), edit: StudioI18n.t("agent.tools_edit_file"), write: StudioI18n.t("agent.tools_write_new_file"), read: StudioI18n.t("agent.tools_read_file"), glob: StudioI18n.t("agent.tools_glob_find_files"), grep: StudioI18n.t("agent.tools_grep_search_files"),
    list: StudioI18n.t("agent.tools_list_files"), todowrite: StudioI18n.t("agent.tools_todowrite_plan"), task: StudioI18n.t("agent.tools_task_subtask"), patch: StudioI18n.t("agent.tools_patch_changes"), webfetch: StudioI18n.t("agent.tools_webfetch_network_download"),
    // DeepSeek Harness tool names
    todo_write: StudioI18n.t("agent.tools_todowrite_plan"), create_goal: StudioI18n.t("agent.tools_create_goal"), get_goal: StudioI18n.t("agent.tools_create_goal"), update_goal: StudioI18n.t("agent.tools_create_goal"), job_list: StudioI18n.t("agent.tools_job_list_background_tasks"), job_output: StudioI18n.t("agent.tools_job_output_background_task"),
    job_kill: StudioI18n.t("agent.tools_job_kill_stop_background_task"), skill: StudioI18n.t("agent.tool_step_skill_execution_label"), working_directory: StudioI18n.t("agent.tools_working_directory_folder"), read_image: StudioI18n.t("agent.tools_read_image_view"), exit_plan_mode: StudioI18n.t("agent.tools_todowrite_plan")};
  const PERMISSIONS = {bash: StudioI18n.t("agent.permissions_bash_run_command"), edit: StudioI18n.t("agent.permissions_edit_file"), external_directory: StudioI18n.t("agent.permissions_external_directory_access_outside_project_folder"), doom_loop: StudioI18n.t("agent.permissions_doom_loop_repeat_same_action")};

  async function load() {
    info = await api('availability');
    const agents = el('agentKind'), modes = el('agentPermission');
    agents.replaceChildren(...info.agents.map(agent => {
      const option = StudioI18n.option(() => (agent.ready ? agent.name : `${agent.name} — ${agent.planned ? StudioI18n.t("agent.option_coming_soon") : StudioI18n.t("agent.component_installation_missing_label")}`), agent.id);
      option.disabled = !agent.ready;StudioI18n.bind(option, "title", () => (StudioI18n.remote(agent.description)), true);return option;
    }));
    modes.replaceChildren(...Object.entries(info.modes).map(([id, label]) => {const option = StudioI18n.option(() => (label), id);StudioI18n.bind(option, "title", () => (info.hints?.[id] || label), true);return option;}));
    return info;
  }
  function show(chat) {
    // An agent chat shows its own folder and rules; a new one starts from the last choice.
    const source = chat && chat.kind === 'agent' ? {agent: chat.agent.id, workspace: chat.agent.workspace, permission: chat.agent.permission} : info?.settings || {};
    if (source.agent) el('agentKind').value = source.agent;
    if (source.permission) el('agentPermission').value = source.permission;
    el('agentWorkspace').value = source.workspace || '';
    const settings = info?.settings || {};
    el('agentLoop').checked = !!settings.loop;el('agentLoopOptions').hidden = !settings.loop;
    el('agentLoopThreshold').value = settings.loop_threshold ?? 75;el('agentLoopIterations').value = settings.loop_iterations ?? 10;
    el('agentLoopCheck').value = settings.loop_check || '';
    el('agentContext').hidden = true;  // the measure belongs to the task that produced it
    describe();
  }
  // The loop settings of the next task, or null without a loop.
  const loop = () => el('agentLoop').checked ? {threshold: Number(el('agentLoopThreshold').value), iterations: Number(el('agentLoopIterations').value),
    check: el('agentLoopCheck').value.trim()} : null;
  function context(event) {
    const box = el('agentContext'), percent = Math.round(100 * event.used / Math.max(event.size, 1));
    box.hidden = false;
    StudioI18n.bind(box, "textContent", () => (StudioI18n.t("agent.box_model_context_tokens", [event.measured ? '' : '≈', event.used.toLocaleString(StudioI18n.locale), event.size.toLocaleString(StudioI18n.locale), percent])), true);
  }
  function describe() {
    const agent = info?.agents.find(a => a.id === el('agentKind').value), hint = info?.hints?.[el('agentPermission').value] || '';
    StudioI18n.bind(el('agentPermission'), "title", () => (hint), true);
    const missing = agent && !agent.ready && !agent.planned ? StudioI18n.t("agent.describe_not_installed_install_under_settings_components") : '';
    StudioI18n.bind(el('agentInfo'), "textContent", () => (agent ? StudioI18n.t("agent.info_runs_offline_through_local_model_git", [agent.name, agent.version ? ' ' + agent.version : '', agent.license, StudioI18n.remote(agent.description), StudioI18n.remote(missing), StudioI18n.remote(hint)]) : ''), true);
  }
  const values = () => ({agent: el('agentKind').value, workspace: el('agentWorkspace').value.trim(), permission: el('agentPermission').value});
  const ready = () => !!info && info.agents.some(a => a.id === el('agentKind').value && a.ready);
  async function choose() {
    // A detached native dialog can open behind the studio; use the same in-window picker as the creative modules.
    window.chooseStudioFolder(el('agentWorkspace').value.trim(), async workspace => {
      el('agentWorkspace').value = workspace;
      await listener?.('workspace', workspace);
    }, el('agentChoose'), {
      titleKey: 'agent.workspace_path_select_project_folder_agent',
      hintKey: 'agent.workspace_folder_picker_existing_folder_hint'
    });
  }
  function bind(onChange) {
    listener = onChange;
    el('agentPermission').onchange = () => {describe();onChange('permission', el('agentPermission').value);};
    el('agentWorkspace').onchange = () => onChange('workspace', el('agentWorkspace').value.trim());
    el('agentKind').onchange = () => {describe();onChange('agent', el('agentKind').value);};
    el('agentChoose').onclick = () => choose().catch(error => onChange('error', StudioI18n.remote(error.message)));
    el('agentLoop').onchange = () => {el('agentLoopOptions').hidden = !el('agentLoop').checked;onChange('loop', el('agentLoop').checked);};
    el('agentLoopThreshold').onchange = () => onChange('loop_threshold', Number(el('agentLoopThreshold').value));
    el('agentLoopIterations').onchange = () => onChange('loop_iterations', Number(el('agentLoopIterations').value));
    el('agentLoopCheck').onchange = () => onChange('loop_check', el('agentLoopCheck').value.trim());
  }

  // ---- rendering: everything from the agent goes in with textContent ----------------------------------------------
  function pre(className, text) {
    const block = document.createElement('pre');block.className = className;StudioI18n.bind(block, "textContent", () => (text), false);return block;
  }
  function step(item) {
    const box = document.createElement('details'), summary = document.createElement('summary'), kind = document.createElement('strong'), title = document.createElement('span'), state = document.createElement('span');
    box.className = 'agent-step agent-' + (item.status || 'pending');box.dataset.step = item.id;
    StudioI18n.bind(kind, "textContent", () => (TOOLS[item.tool] || item.tool), true);StudioI18n.bind(title, "textContent", () => (item.title || ''), false);state.className = 'agent-state';StudioI18n.bind(state, "textContent", () => (STATUS[item.status] || item.status), true);
    summary.append(kind, title, state);box.append(summary);
    if (item.command) box.append(pre('agent-command', '$ ' + item.command));
    else if (item.file) box.append(pre('agent-command', item.file));
    if (item.diff) box.append(pre('agent-output agent-diff', item.diff));
    else if (item.output) box.append(pre('agent-output', item.output));
    if (StudioI18n.remote(item.error)) box.append(pre('agent-output agent-error-text', StudioI18n.remote(item.error)));
    box.open = item.status === 'error';
    return box;
  }
  function renderSteps(container, steps) {
    // Preserve unchanged DOM (and expanded details) while text tokens keep arriving.
    const existing = new Map([...container.children].map(node => [node.dataset.step, node]));
    const wanted = new Set();
    for (const item of steps || []) {
      wanted.add(String(item.id));
      const old = existing.get(String(item.id)), signature = JSON.stringify(item);
      if (old && old.stepSignature === signature) continue;
      const node = step(item);node.stepSignature = signature;
      if (old) {node.open = old.open;old.replaceWith(node);} else container.append(node);
    }
    for (const [id, node] of existing) if (!wanted.has(id)) node.remove();
    container.hidden = !(steps && steps.length);
  }
  function permissionCard(request, chatId, onDone) {
    const card = document.createElement('div'), text = document.createElement('p'), actions = document.createElement('div');
    card.className = 'agent-ask';card.dataset.request = request.id;card.setAttribute('role', 'group');StudioI18n.bind(card, "@aria-label", () => (StudioI18n.t("agent.card_agent_permission_request")), true);
    StudioI18n.bind(text, "textContent", () => (StudioI18n.t("agent.permission_card_agent_requests", [StudioI18n.remote(PERMISSIONS[request.permission] || request.permission)])), true);
    card.append(text);
    if (request.command) card.append(pre('agent-command', '$ ' + request.command));
    else if (request.patterns.length) card.append(pre('agent-command', request.patterns.join('\n')));
    if (request.file) card.append(pre('agent-command', request.file));
    if (request.diff) card.append(pre('agent-output agent-diff', request.diff));
    actions.className = 'actions';
    const answers = [['once', StudioI18n.t("agent.permission_card_allow_once"), ''], ['always', StudioI18n.t("agent.permission_card_allow_action"), 'secondary'], ['reject', StudioI18n.t("agent.permission_card_reject"), 'secondary']];
    for (const [reply, label, style] of answers) {
      if (reply === 'always' && !request.always.length) continue;
      const button = document.createElement('button');button.type = 'button';StudioI18n.bind(button, "textContent", () => (label), true);if (style) button.className = style;
      button.dataset.allowBusy = '1';  // answering is the point while the task runs: the shared busy lock must not disable it
      if (reply === 'always') StudioI18n.bind(button, "title", () => (StudioI18n.t("agent.button_allowed_without_asking") + request.always.join(', ')), true);
      button.onclick = async () => {
        for (const other of actions.querySelectorAll('button')) other.disabled = true;
        try {await api('permission', {chat_id: chatId, request_id: request.id, reply});resolve(card, reply);}
        catch (error) {for (const other of actions.querySelectorAll('button')) other.disabled = false;onDone?.(StudioI18n.remote(error.message));}
      };
      actions.append(button);
    }
    card.append(actions);
    return card;
  }
  function resolve(card, reply) {
    if (!card || card.dataset.answered) return;
    card.dataset.answered = '1';
    for (const button of card.querySelectorAll('button')) button.disabled = true;
    const note = document.createElement('p');note.className = 'muted';
    StudioI18n.bind(note, "textContent", () => (reply === 'reject' ? StudioI18n.t("agent.note_you_rejected_request") : reply === 'always' ? StudioI18n.t("agent.note_permission_saved_current_agent_session") : StudioI18n.t("agent.note_allowed_once")), true);
    card.append(note);
  }
  return {api, load, show, values, loop, context, ready, bind, renderSteps, permissionCard, resolve, get info() {return info;}};
})();
