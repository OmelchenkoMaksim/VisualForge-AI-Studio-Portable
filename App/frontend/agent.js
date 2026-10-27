'use strict';
/* Agent mode of the Text tab: the agent panel (agent, project folder, permissions) and the rendering of agent steps and
   permission requests. text.js owns the chat list, sending and streaming; it calls these helpers for agent chats. */
window.StudioAgent = (() => {
  const el = id => document.getElementById(id);
  let info = null, listener = null;
  const api = async (route, data) => {
    let response;
    try {response = await fetch('/api/agents/' + route, data === undefined ? {} : {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)});}
    catch {throw new Error('Нет связи со студией. Проверьте, запущено ли приложение.');}
    let value;
    try {value = await response.json();} catch {throw new Error('Некорректный ответ режима «Агент».');}
    if (!response.ok) throw new Error(value.error || 'Режим «Агент» недоступен');
    return value;
  };
  const STATUS = {pending: 'готовится', running: 'выполняется', completed: 'готово', error: 'ошибка'};
  const TOOLS = {bash: 'Команда', edit: 'Правка файла', write: 'Новый файл', read: 'Чтение файла', glob: 'Поиск файлов', grep: 'Поиск в файлах',
    list: 'Список файлов', todowrite: 'План', task: 'Подзадача', patch: 'Изменения', webfetch: 'Загрузка из сети',
    // DeepSeek Harness tool names
    todo_write: 'План', create_goal: 'Цель', get_goal: 'Цель', update_goal: 'Цель', job_list: 'Фоновые задачи', job_output: 'Фоновая задача',
    job_kill: 'Остановка фоновой задачи', skill: 'Навык', working_directory: 'Рабочая папка', read_image: 'Просмотр картинки', exit_plan_mode: 'План'};
  const PERMISSIONS = {bash: 'выполнить команду', edit: 'изменить файл', external_directory: 'выйти за папку проекта', doom_loop: 'повторить одно и то же действие ещё раз'};

  async function load() {
    info = await api('availability');
    const agents = el('agentKind'), modes = el('agentPermission');
    agents.replaceChildren(...info.agents.map(agent => {
      const option = new Option(agent.ready ? agent.name : `${agent.name} — ${agent.planned ? 'скоро' : 'не установлен'}`, agent.id);
      option.disabled = !agent.ready;option.title = agent.description;return option;
    }));
    modes.replaceChildren(...Object.entries(info.modes).map(([id, label]) => {const option = new Option(label, id);option.title = info.hints?.[id] || label;return option;}));
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
    box.textContent = `Контекст модели: ${event.measured ? '' : '≈'}${event.used.toLocaleString('ru-RU')} из ${event.size.toLocaleString('ru-RU')} токенов (${percent}%)`;
  }
  function describe() {
    const agent = info?.agents.find(a => a.id === el('agentKind').value), hint = info?.hints?.[el('agentPermission').value] || '';
    el('agentPermission').title = hint;
    const missing = agent && !agent.ready && !agent.planned ? ' Не установлен: поставьте его в «Настройки → Компоненты», раздел Text.' : '';
    el('agentInfo').textContent = agent ? `${agent.name}${agent.version ? ' ' + agent.version : ''} · ${agent.license} · ${agent.description}${missing} ${hint} Работает без интернета через локальную модель; git push запрещён.` : '';
  }
  const values = () => ({agent: el('agentKind').value, workspace: el('agentWorkspace').value.trim(), permission: el('agentPermission').value});
  const ready = () => !!info && info.agents.some(a => a.id === el('agentKind').value && a.ready);
  async function choose() {
    const answer = await api('choose-workspace', {initial: el('agentWorkspace').value.trim()});
    if (!answer.cancelled) {el('agentWorkspace').value = answer.workspace;listener?.('workspace', answer.workspace);}
  }
  function bind(onChange) {
    listener = onChange;
    el('agentPermission').onchange = () => {describe();onChange('permission', el('agentPermission').value);};
    el('agentWorkspace').onchange = () => onChange('workspace', el('agentWorkspace').value.trim());
    el('agentKind').onchange = () => {describe();onChange('agent', el('agentKind').value);};
    el('agentChoose').onclick = () => choose().catch(error => onChange('error', error.message));
    el('agentLoop').onchange = () => {el('agentLoopOptions').hidden = !el('agentLoop').checked;onChange('loop', el('agentLoop').checked);};
    el('agentLoopThreshold').onchange = () => onChange('loop_threshold', Number(el('agentLoopThreshold').value));
    el('agentLoopIterations').onchange = () => onChange('loop_iterations', Number(el('agentLoopIterations').value));
    el('agentLoopCheck').onchange = () => onChange('loop_check', el('agentLoopCheck').value.trim());
  }

  // ---- rendering: everything from the agent goes in with textContent ----------------------------------------------
  function pre(className, text) {
    const block = document.createElement('pre');block.className = className;block.textContent = text;return block;
  }
  function step(item) {
    const box = document.createElement('details'), summary = document.createElement('summary'), kind = document.createElement('strong'), title = document.createElement('span'), state = document.createElement('span');
    box.className = 'agent-step agent-' + (item.status || 'pending');box.dataset.step = item.id;
    kind.textContent = TOOLS[item.tool] || item.tool;title.textContent = item.title || '';state.className = 'agent-state';state.textContent = STATUS[item.status] || item.status;
    summary.append(kind, title, state);box.append(summary);
    if (item.command) box.append(pre('agent-command', '$ ' + item.command));
    else if (item.file) box.append(pre('agent-command', item.file));
    if (item.diff) box.append(pre('agent-output agent-diff', item.diff));
    else if (item.output) box.append(pre('agent-output', item.output));
    if (item.error) box.append(pre('agent-output agent-error-text', item.error));
    box.open = item.status === 'error';
    return box;
  }
  function renderSteps(container, steps) {
    const opened = new Set([...container.querySelectorAll('details[open]')].map(node => node.dataset.step));
    container.replaceChildren(...(steps || []).map(item => {const node = step(item);if (opened.has(item.id)) node.open = true;return node;}));
    container.hidden = !(steps && steps.length);
  }
  function permissionCard(request, chatId, onDone) {
    const card = document.createElement('div'), text = document.createElement('p'), actions = document.createElement('div');
    card.className = 'agent-ask';card.dataset.request = request.id;card.setAttribute('role', 'group');card.setAttribute('aria-label', 'Запрос разрешения агента');
    text.textContent = `Агент просит разрешения ${PERMISSIONS[request.permission] || request.permission}:`;
    card.append(text);
    if (request.command) card.append(pre('agent-command', '$ ' + request.command));
    else if (request.patterns.length) card.append(pre('agent-command', request.patterns.join('\n')));
    if (request.file) card.append(pre('agent-command', request.file));
    if (request.diff) card.append(pre('agent-output agent-diff', request.diff));
    actions.className = 'actions';
    const answers = [['once', 'Разрешить один раз', ''], ['always', 'Разрешать такое в этом чате', 'secondary'], ['reject', 'Отклонить', 'secondary']];
    for (const [reply, label, style] of answers) {
      if (reply === 'always' && !request.always.length) continue;
      const button = document.createElement('button');button.type = 'button';button.textContent = label;if (style) button.className = style;
      button.dataset.allowBusy = '1';  // answering is the point while the task runs: the shared busy lock must not disable it
      if (reply === 'always') button.title = 'Без вопросов: ' + request.always.join(', ');
      button.onclick = async () => {
        for (const other of actions.querySelectorAll('button')) other.disabled = true;
        try {await api('permission', {chat_id: chatId, request_id: request.id, reply});resolve(card, reply);}
        catch (error) {for (const other of actions.querySelectorAll('button')) other.disabled = false;onDone?.(error.message);}
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
    note.textContent = reply === 'reject' ? 'Вы отклонили запрос.' : reply === 'always' ? 'Разрешено до конца этого чата.' : 'Разрешено один раз.';
    card.append(note);
  }
  return {api, load, show, values, loop, context, ready, bind, renderSteps, permissionCard, resolve, get info() {return info;}};
})();
