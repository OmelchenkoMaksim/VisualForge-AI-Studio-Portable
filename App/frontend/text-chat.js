'use strict';
(() => {
  const el = id => document.getElementById(id);
  const LAST_KEY = 'visualforge.text.last-chat.v1';
  let available = false, current = null, chats = [], settings = null, models = [], streaming = null, statusTimer = null, pending = [], mode = 'chat', agentError = '';
  // The open chat decides the mode; without one the «Чат | Агент» switch decides what the next chat will be.
  const agentMode = () => current ? current.kind === 'agent' : mode === 'agent';
  // MAX_SIDE: Gemma 4 sees a picture through up to 1120 vision tokens (about 2.6 MP, see VISION_TOKENS in text_manager.py), so a
  // long side of 1536 px threw away the detail the model can now use; screenshots up to 2560 px keep their small text.
  const MAX_PICTURES = 4, MAX_SIDE = 2560, MAX_BYTES = 5 * 1024 * 1024;
  const fmt = (n, digits = 1) => Number(n).toLocaleString(StudioI18n.locale, {maximumFractionDigits: digits});
  const gb = bytes => fmt(bytes / 1024 ** 3);
  const api = async (route, data) => {
    let response;
    try {response = await fetch('/api/text/' + route, data === undefined ? {} : {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)});}
    catch {throw new Error(StudioI18n.t("text.api_request_connect_studio_check_application_running_unavailable"));}
    let value;
    try {value = await response.json();} catch {throw new Error(StudioI18n.t("text.api_request_invalid_text_service_response"));}
    if (!response.ok) throw new Error(StudioI18n.remote(value.error) || StudioI18n.t("text.api_request_text_unavailable"));
    return value;
  };
  const chatTitle = chat => {
    if (!chat) return StudioI18n.t("text.chat_new_conversation_default_title");
    const empty = typeof chat.messages === 'number' ? chat.messages === 0 : !chat.messages?.length;
    return empty && chat.title === StudioI18n.source("text.chat_new_conversation_default_title") ? StudioI18n.t("text.chat_new_conversation_default_title") : chat.title;
  };
  const remember = id => {try {localStorage.setItem(LAST_KEY, id || '');} catch {}};
  const recall = () => {try {return localStorage.getItem(LAST_KEY) || '';} catch {return '';}};
  const status = text => {StudioI18n.bind(el('textStatus'), "textContent", () => (text), true);};

  // ---- rendering: text is only ever inserted with textContent, so model output cannot inject markup ----
  function inline(parent, text) {
    for (const part of text.split(/(`[^`\n]+`|\*\*[^*\n]+\*\*)/)) {
      if (!part) continue;
      if (part.startsWith('`') && part.endsWith('`') && part.length > 2) {const code = document.createElement('code');StudioI18n.bind(code, "textContent", () => (part.slice(1, -1)), false);parent.append(code);}
      else if (part.startsWith('**') && part.endsWith('**') && part.length > 4) {const strong = document.createElement('strong');StudioI18n.bind(strong, "textContent", () => (part.slice(2, -2)), false);parent.append(strong);}
      else parent.append(part);
    }
  }
  function codeBlock(language, code) {
    const box = document.createElement('div'), head = document.createElement('div'), label = document.createElement('span'), copy = document.createElement('button'), pre = document.createElement('pre'), body = document.createElement('code');
    box.className = 'text-code';head.className = 'text-code-head';StudioI18n.bind(label, "textContent", () => (language || StudioI18n.t("text.message_code_block_language_fallback")), true);
    copy.type = 'button';copy.className = 'secondary';StudioI18n.bind(copy, "textContent", () => (StudioI18n.t("text.clipboard_copy_button_label")), true);
    copy.onclick = async () => {try {await navigator.clipboard.writeText(code);StudioI18n.bind(copy, "textContent", () => (StudioI18n.t("text.clipboard_copy_success_feedback")), true);setTimeout(() => {StudioI18n.bind(copy, "textContent", () => (StudioI18n.t("text.clipboard_copy_button_label")), true);}, 1500);} catch {StudioI18n.bind(copy, "textContent", () => (StudioI18n.t("text.clipboard_copy_failed_feedback")), true);}};
    StudioI18n.bind(body, "textContent", () => (code), false);pre.append(body);head.append(label, copy);box.append(head, pre);
    return box;
  }
  function rich(container, text) {
    container.replaceChildren();
    const pattern = /```([\w+#.-]*)[^\S\n]*\n?([\s\S]*?)(?:```|$)/g;
    let last = 0, match;
    const prose = chunk => {
      for (const paragraph of chunk.split(/\n{2,}/)) {
        if (!paragraph.trim()) continue;
        const p = document.createElement('p');
        const heading = paragraph.match(/^#{1,4}\s+(.+)$/);
        if (heading) {const strong = document.createElement('strong');inline(strong, heading[1]);p.append(strong);} else inline(p, paragraph.replace(/^\n+|\n+$/g, ''));
        container.append(p);
      }
    };
    while ((match = pattern.exec(text)) !== null) {
      prose(text.slice(last, match.index));
      container.append(codeBlock(match[1], match[2].replace(/\n$/, '')));
      last = pattern.lastIndex;
      if (match[0] === '') pattern.lastIndex++;
    }
    prose(text.slice(last));
  }
  function bubble(message) {
    const row = document.createElement('article'), head = document.createElement('div'), who = document.createElement('strong'), body = document.createElement('div');
    row.className = 'text-msg text-' + message.role;head.className = 'text-msg-head';StudioI18n.bind(who, "textContent", () => (message.role === 'user' ? StudioI18n.t("text.chat_message_user_author_label") : agentMode() ? StudioI18n.t("text.chat_message_agent_author_label") : StudioI18n.t("text.chat_message_model_author_label")), true);
    head.append(who);
    if (message.created) {const time = document.createElement('span');time.className = 'muted';StudioI18n.bind(time, "textContent", () => (new Date(message.created * 1000).toLocaleString(StudioI18n.locale)), true);head.append(time);}
    body.className = 'text-body';row.append(head);
    if (message.images && message.images.length) {
      const strip = document.createElement('div');strip.className = 'text-pictures';
      for (const meta of message.images) {
        const link = document.createElement('a'), picture = document.createElement('img');
        link.href = meta.local || `/api/text/image?chat=${encodeURIComponent(current ? current.id : '')}&id=${encodeURIComponent(meta.id)}`;link.target = '_blank';link.rel = 'noopener';StudioI18n.bind(link, "title", () => (meta.name || StudioI18n.t("text.attachment_image_fallback_label")), false);
        picture.src = link.href;StudioI18n.bind(picture, "alt", () => (meta.name || StudioI18n.t("text.picture_attached_image")), false);picture.loading = 'lazy';link.append(picture);strip.append(link);
      }
      row.append(strip);
    }
    if (message.role === 'assistant') {
      const think = document.createElement('details'), summary = document.createElement('summary'), thoughts = document.createElement('div');
      think.className = 'text-think';think.hidden = true;StudioI18n.bind(summary, "textContent", () => (StudioI18n.t("text.summary_model_reasoning")), true);thoughts.className = 'text-think-body';think.append(summary, thoughts);row.append(think);
      row.think = think;row.thoughts = thoughts;
      // Agent answers: the commands and edits it made, then any permission request waiting for the user.
      const steps = document.createElement('div'), asks = document.createElement('div');
      steps.className = 'agent-steps';steps.hidden = true;asks.className = 'agent-steps';row.append(steps, asks);row.steps = steps;row.asks = asks;
    }
    row.append(body);row.body = body;row.head = head;
    const foot = document.createElement('div');foot.className = 'text-foot';row.append(foot);row.foot = foot;
    fill(row, message);
    return row;
  }
  function fill(row, message) {
    if (row.renderedContent !== (message.content || '')) {
      rich(row.body, message.content || '');row.renderedContent = message.content || '';
    }
    if (row.think) {
      row.think.hidden = !message.thinking;
      StudioI18n.bind(row.thoughts, "textContent", () => (message.thinking || ''), false);
      StudioI18n.bind(row.think.querySelector('summary'), "textContent", () => (StudioI18n.t("text.summary_model_reasoning_chars", [fmt((message.thinking || '').length, 0)])), true);
    }
    if (row.steps) window.StudioAgent?.renderSteps(row.steps, message.steps);
    row.foot.replaceChildren();
    if (message.role === 'assistant' && message.stats) {
      const s = message.stats, note = document.createElement('span');note.className = 'muted';
      StudioI18n.bind(note, "textContent", () => (s.model_calls === undefined ? StudioI18n.t("text.note_tok_s_context", [fmt(s.tokens_per_sec), s.completion_tokens, s.prompt_tokens + s.completion_tokens, s.context])
        : [StudioI18n.t("text.note_model_calls", [s.model_calls]), s.completion_tokens === undefined ? '' : StudioI18n.t("text.note_response_tokens", [fmt(s.completion_tokens, 0)]),  // agent task totals; dsh reports no output tokens
          StudioI18n.t("text.chat_message_context_usage_template", [fmt(s.context_tokens, 0), fmt(s.context, 0)])].filter(Boolean).join(' · ')), true);
      row.foot.append(note);
    }
    if (message.role === 'assistant' && message.generation && window.StudioUI?.generationInfo) row.foot.append(StudioUI.generationInfo(message.generation));
    if (message.stopped || StudioI18n.remote(message.error)) {
      const note = document.createElement('span');note.className = 'text-warn';StudioI18n.bind(note, "textContent", () => (StudioI18n.remote(message.error) ? StudioI18n.t("text.note_response_interrupted") + StudioI18n.remote(message.error) : StudioI18n.t("text.note_stopped_you")), true);row.foot.append(note);
    }
    if (message.content) {
      const copy = document.createElement('button');copy.type = 'button';copy.className = 'secondary';StudioI18n.bind(copy, "textContent", () => (StudioI18n.t("text.clipboard_copy_button_label")), true);
      copy.onclick = async () => {try {await navigator.clipboard.writeText(message.content);StudioI18n.bind(copy, "textContent", () => (StudioI18n.t("text.clipboard_copy_success_feedback")), true);setTimeout(() => {StudioI18n.bind(copy, "textContent", () => (StudioI18n.t("text.clipboard_copy_button_label")), true);}, 1500);} catch {StudioI18n.bind(copy, "textContent", () => (StudioI18n.t("text.clipboard_copy_failed_feedback")), true);}};
      row.foot.append(copy);
    }
  }
  const atBottom = () => {const box = el('textMessages');return box.scrollHeight - box.scrollTop - box.clientHeight < 80;};
  const toBottom = () => {const box = el('textMessages');box.scrollTop = box.scrollHeight;};
  function renderChat() {
    const box = el('textMessages');box.replaceChildren();
    StudioI18n.bind(el('textTitle'), "textContent", () => (chatTitle(current)), false);
    if (!current || !current.messages.length) {
      const hint = document.createElement('p');hint.className = 'muted text-empty';
      StudioI18n.bind(hint, "textContent", () => (agentMode() ? StudioI18n.t("text.hint_select_project_folder_describe_task_agent")
        : StudioI18n.t("text.hint_write_first_message_model_runs_computer")), true);
      box.append(hint);
    }
    for (const message of current ? current.messages : []) box.append(bubble(message));
    controls();toBottom();
  }
  function renderChats() {
    const query = el('textSearch').value.trim().toLowerCase(), list = el('textChats');list.replaceChildren();
    const shown = chats.filter(c => !query || c.title.toLowerCase().includes(query));
    if (!shown.length) {const empty = document.createElement('p');empty.className = 'muted';StudioI18n.bind(empty, "textContent", () => (chats.length ? StudioI18n.t("text.empty_nothing_found") : StudioI18n.t("text.empty_no_chats_yet")), true);list.append(empty);}
    for (const chat of shown) {
      const item = document.createElement('button');item.type = 'button';item.className = 'text-chat-item';item.setAttribute('role', 'listitem');
      if (current && current.id === chat.id) item.setAttribute('aria-current', 'true');
      const title = document.createElement('strong'), meta = document.createElement('span');
      StudioI18n.bind(title, "textContent", () => (chatTitle(chat)), false);meta.className = 'muted';StudioI18n.bind(meta, "textContent", () => (StudioI18n.t("text.chat_history_message_count_date_template", [chat.messages, new Date(chat.updated * 1000).toLocaleDateString(StudioI18n.locale)])), true);
      if (chat.kind === 'agent') {const badge = document.createElement('span');badge.className = 'agent-badge';StudioI18n.bind(badge, "textContent", () => (StudioI18n.t("text.chat_message_agent_author_label")), true);item.append(badge);}
      item.append(title, meta);item.onclick = () => openChat(chat.id);list.append(item);
    }
  }
  function controls() {
    const busy = !!streaming, agent = agentMode();
    const agentReady = !agent || (!!window.StudioAgent?.ready() && (!!current || !!StudioAgent.values().workspace));
    const ready = available && models.some(model => model.name === el('textModel').value) && !busy && agentReady;
    el('textSend').disabled = !ready;el('textStop').disabled = !busy;el('textInput').disabled = !available;
    StudioI18n.bind(el('textSend'), "textContent", () => (agent ? StudioI18n.t("text.send_assign_task") : StudioI18n.t("text.chat_message_send_button_label")), true);
    StudioI18n.bind(el('textInput'), "placeholder", () => (agent ? StudioI18n.t("text.input_describe_agent_s_task_enter_send") : StudioI18n.t("text.input_write_message_enter_send_shift_new")), true);
    const vision = visionAvailable();
    el('textAttach').disabled = !ready || !vision || agent || pending.length >= MAX_PICTURES;
    StudioI18n.bind(el('textAttach'), "title", () => (agent ? StudioI18n.t("text.attach_images_not_yet_supported_agent_mode") : vision ? StudioI18n.t("text.attach_png_jpeg_paste_image_ctrl_v") : StudioI18n.t("text.attach_selected_model_does_not_support_images")), true);
    const last = current && current.messages[current.messages.length - 1];
    el('textRegenerate').disabled = !ready || !last || agent;
    el('textRename').disabled = !current || busy;el('textDelete').disabled = !current || busy;el('textNewChat').disabled = busy;
    el('textSystem').disabled = !current || busy;el('textThink').disabled = !current || busy || agent;
    el('agentPanel').hidden = !agent;
    for (const radio of document.querySelectorAll('input[name="textMode"]')) {radio.checked = radio.value === (agent ? 'agent' : 'chat');radio.disabled = busy;}
    for (const id of ['agentKind', 'agentWorkspace', 'agentChoose', 'agentPermission']) el(id).disabled = busy || !window.StudioAgent?.info;
    if (agent && agentError) StudioI18n.bind(el('agentInfo'), "textContent", () => (agentError), true);
  }

  // ---- attachments: pictures are shrunk in the browser (long side 2560) and sent as PNG/JPEG ---------------------
  const visionAvailable = () => !!models.find(m => m.name === el('textModel').value && m.vision);
  const readData = blob => new Promise((resolve, reject) => {const reader = new FileReader();reader.onload = () => resolve(String(reader.result));reader.onerror = () => reject(new Error(StudioI18n.t("text.reader_read_file_failed")));reader.readAsDataURL(blob);});
  async function prepare(file) {
    if (!/^image\//.test(file.type)) throw new Error(StudioI18n.t("text.prepare_not_image", [file.name]));
    if (file.size > 40 * 1024 * 1024) throw new Error(StudioI18n.t("text.prepare_exceeds_40_mi_b", [file.name]));
    let bitmap;
    try {bitmap = await createImageBitmap(file);} catch {throw new Error(StudioI18n.t("text.prepare_open_failed", [file.name]));}
    const scale = Math.min(1, MAX_SIDE / Math.max(bitmap.width, bitmap.height));
    if (scale === 1 && (file.type === 'image/png' || file.type === 'image/jpeg') && file.size <= MAX_BYTES) {bitmap.close();return {name: file.name, data: await readData(file)};}
    const canvas = document.createElement('canvas');canvas.width = Math.max(1, Math.round(bitmap.width * scale));canvas.height = Math.max(1, Math.round(bitmap.height * scale));
    const context = canvas.getContext('2d');context.fillStyle = '#fff';context.fillRect(0, 0, canvas.width, canvas.height);context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);bitmap.close();
    for (const quality of [0.92, 0.8, 0.65]) {
      const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg', quality));
      if (blob && blob.size <= MAX_BYTES) return {name: file.name.replace(/\.[^.]+$/, '') + '.jpg', data: await readData(blob)};
    }
    throw new Error(StudioI18n.t("text.prepare_too_large_even_after_compression", [file.name]));
  }
  async function addFiles(files) {
    if (!visionAvailable()) {status(StudioI18n.t("text.add_files_selected_model_does_not_support_images"));return;}
    for (const file of files) {
      if (pending.length >= MAX_PICTURES) {status(StudioI18n.t("text.add_files_you_attach_up_images_per_message", [MAX_PICTURES]));break;}
      try {pending.push(await prepare(file));} catch (error) {status(StudioI18n.remote(error.message));}
    }
    renderPending();
  }
  function renderPending() {
    const strip = el('textAttachments');strip.replaceChildren();strip.hidden = !pending.length;
    pending.forEach((item, index) => {
      const figure = document.createElement('figure'), picture = document.createElement('img'), remove = document.createElement('button');
      picture.src = item.data;StudioI18n.bind(picture, "alt", () => (item.name), false);StudioI18n.bind(picture, "title", () => (item.name), false);
      remove.type = 'button';remove.className = 'secondary';StudioI18n.bind(remove, "textContent", () => ('×'), true);StudioI18n.bind(remove, "@aria-label", () => (StudioI18n.t("text.attachment_remove_image_accessibility_prefix") + item.name), false);
      remove.onclick = () => {pending.splice(index, 1);renderPending();};
      figure.append(picture, remove);strip.append(figure);
    });
    controls();
  }

  const PARAMS = ['num_ctx', 'temperature', 'top_p', 'top_k', 'num_predict'];
  function modelReleaseId(model) {
    // Only the known imported builds: unrelated/custom models retain their own labels.
    return {'Q38Light': 'text-qwen38-27b', 'HauhauQ6': 'text-qwen36-35b-a3b', 'Gemma4A4B': 'text-gemma4-26b-a4b'}[model.label.replace(/^\d+ts-/, '').split('-')[0]] || '';
  }
  function modelLabel(model) {
    const name = model.label.replace(/^\d+ts-/, '').replace(/-\d+\.\d+-.*$/, '').replace(/(Gemma)(\d+)(A\d+B)/, '$1 $2 $3').replace(/(Q\d+)(Light)$/, '$1 $2').replace(/(Hauhau)(Q\d+)$/, '$1 $2');
    return window.StudioUI?.modelLabel(modelReleaseId(model), name) || name;
  }
  function modelTitle(model) {
    const source = window.StudioUI?.modelSource(modelReleaseId(model));
    return source ? StudioI18n.t("text.model_base_release_date_custom_fine_tuning", [model.name, source]) : model.name;
  }
  function header() {
    const model = models.find(row => row.name === el('textModel').value);
    StudioUI.setHeader('text', {model:model ? modelLabel(model).split(' · ')[0] : 'Text',
      description:agentMode() ? StudioI18n.t("text.header_local_agent_project_files_commands_git") : model?.vision ? StudioI18n.t("text.header_local_model_chat_text_images") : StudioI18n.t("text.header_local_text_model_chat"),
      tone:available && model ? 'good' : 'bad',
      status:available && model ? StudioI18n.t("text.header_ollama_runtime_model_files_available_loads") : available ? StudioI18n.t("text.header_chat_s_model_disabled_unavailable_enable") : el('textAvailability').textContent});
  }
  function modelSettings() {
    header();
    if (!settings) return;
    const model = models.find(m => m.name === el('textModel').value), native = el('textNative').checked;
    for (const key of PARAMS) {
      const field = el('textP-' + key);
      field.disabled = native;
      field.value = native ? (model?.parameters?.[key] ?? (key === 'num_ctx' ? settings.num_ctx : '')) : settings[key];
      StudioI18n.bind(field, "placeholder", () => (native ? StudioI18n.t("text.field_ollama_default") : ''), true);
    }
    if (model && available) {
      StudioI18n.bind(el('textModel'), "title", () => (modelTitle(model)), true);
      StudioI18n.bind(el('textAvailability'), "textContent", () => (StudioI18n.t("text.model_selection_name_size_capability_template", [modelLabel(model), gb(model.size), model.vision ? StudioI18n.t("text.availability_text_images") : StudioI18n.t("text.model_capability_text_only_label")])
        + (model.size > 16 * 1024 ** 3 ? StudioI18n.t("text.availability_part_model_may_run_ram") : '')), true);
    }
  }

  // ---- data ---------------------------------------------------------------------------------------------------
  async function loadChats() {chats = (await api('chats')).chats;renderChats();}
  async function openChat(id) {
    try {
      current = await api('chat?id=' + encodeURIComponent(id));remember(id);
      // Restoring a chat must also restore the mode used when it is cleared or replaced.
      mode = current.kind === 'agent' ? 'agent' : 'chat';
      el('textSystem').value = current.system || '';el('textThink').checked = current.think !== false;
      if (current.model) {
        // Preserve the chat's chosen model; silently selecting another build would misrepresent its next answer.
        if (![...el('textModel').options].some(o => o.value === current.model)) {
          const option = StudioI18n.option(() => (current.model + StudioI18n.t("text.open_chat_disabled_unavailable")), current.model);
          option.disabled = true;el('textModel').append(option);
        }
        el('textModel').value = current.model;
      }
      window.StudioAgent?.show(current);
      modelSettings();renderChat();renderChats();
    } catch (error) {status(StudioI18n.remote(error.message));remember('');current = null;renderChat();}
  }
  async function newChat() {
    if (streaming) return;
    if (mode === 'agent') current = await StudioAgent.api('chats', {...StudioAgent.values(), system: el('textSystem').value, model: el('textModel').value});
    else current = await api('chats', {system: el('textSystem').value, model: el('textModel').value, think: el('textThink').checked});
    remember(current.id);await loadChats();renderChat();el('textInput').focus();
  }
  async function refresh() {
    try {
      const info = await api('availability');
      available = info.available;settings = info.settings;models = info.models.filter(m => m.ok);
      el('textModel').replaceChildren(...models.map(m => {const option = StudioI18n.option(() => (StudioI18n.t("text.model_selection_name_size_template", [modelLabel(m), gb(m.size)])), m.name);StudioI18n.bind(option, "title", () => (modelTitle(m)), true);return option;}));
      el('textModel').value = info.default_model || '';
      el('textNative').checked = settings.native_parameters !== false;
      el('textP-keep_alive').value = settings.keep_alive;
      StudioI18n.bind(el('textAvailability'), "textContent", () => (available ? StudioI18n.t("text.availability_model_runs_locally_through_ollama_inside", [info.default_model]) : StudioI18n.remote(info.reason)), true);
      try {await StudioAgent.load();agentError = '';StudioAgent.show(current);} catch (error) {agentError = StudioI18n.remote(error.message);}
      modelSettings();
      await loadChats();
      const wanted = current ? current.id : recall();
      if (wanted && chats.some(c => c.id === wanted)) await openChat(wanted);
      else if (chats.length && !current) await openChat(chats[0].id);
      else renderChat();
      pollStatus();
    } catch (error) {StudioI18n.bind(el('textAvailability'), "textContent", () => (StudioI18n.remote(error.message)), true);available = false;controls();header();}
  }
  async function pollStatus() {
    if (el('textWorkspace').hidden) return;
    try {
      const info = await api('status'), model = info.loaded[0];
      if (!streaming) status(model ? StudioI18n.t("text.poll_model_memory_gb_including_gpu_unloads", [gb(model.size), gb(model.size_vram), info.settings.keep_alive]) : info.running ? StudioI18n.t("text.poll_model_not_loaded_gpu_free") : StudioI18n.t("text.poll_ready_model_load_first_message"));
    } catch {}
  }

  // ---- sending ------------------------------------------------------------------------------------------------
  async function send(regenerate) {
    if (streaming || !available) return;
    const text = el('textInput').value, sentPictures = regenerate || agentMode() ? [] : pending;
    if (!regenerate && !text.trim() && !sentPictures.length) return;
    if (agentMode() && (regenerate || !text.trim())) return;
    try {
      if (!current) await newChat();
      const agent = current.kind === 'agent';
      const wasRegenerating = regenerate && current.messages.length && current.messages[current.messages.length - 1].role === 'assistant';
      if (wasRegenerating) {current.messages.pop();renderChat();}
      if (!regenerate) {
        if (!current.messages.length && current.title === StudioI18n.source("text.chat_new_conversation_default_title")) {const flat = text.split(/\s+/).filter(Boolean).join(' ');current.title = flat.length > 80 ? flat.slice(0, 79).trimEnd() + '…' : (flat || StudioI18n.t("text.attachment_image_fallback_label"));}
        current.messages.push({role: 'user', content: text, created: Date.now() / 1000, images: sentPictures.map(p => ({local: p.data, name: p.name}))});renderChat();el('textInput').value = '';
        pending = [];renderPending();
      }
      const assistant = {role: 'assistant', content: '', thinking: '', steps: []}, row = bubble(assistant);
      el('textMessages').append(row);toBottom();
      const controller = new AbortController();streaming = {controller, chat: current.id, agent};controls();
      status(agent ? StudioI18n.t("text.send_agent_receiving_task") : StudioI18n.t("text.chat_message_sending_status"));
      const loop = agent ? StudioAgent.loop() : null;
      const body = agent ? {chat_id: current.id, content: text, ...(loop ? {loop} : {})} : regenerate ? {chat_id: current.id, regenerate: true} : {chat_id: current.id, content: text, images: sentPictures.map(p => ({name: p.name, data: p.data}))};
      const response = await fetch(agent ? '/api/agents/send' : '/api/text/send', {method: 'POST', headers: {'Content-Type': 'application/json'}, signal: controller.signal,
        body: JSON.stringify(body)})
        .catch(error => {
          // Preserve AbortError so Stop stays silent; other fetch failures use the connection message.
          if (error.name === 'AbortError') throw error;
          throw new Error(StudioI18n.t("text.send_connect_studio_unavailable"));
        });
      if (!response.ok) {
        let message = StudioI18n.t("text.send_message_failed");
        try {message = StudioI18n.remote(await response.json().error) || message;} catch {}
        if (!regenerate) {current.messages.pop();el('textInput').value = text;pending = sentPictures;renderPending();}
        row.remove();renderChat();status(message);StudioUI.notify({module: 'Text', error: true, message});
        return;
      }
      await readStream(response, assistant, row);
    } catch (error) {
      if (error.name !== 'AbortError') {status(StudioI18n.remote(error.message));StudioUI.notify({module: 'Text', error: true, message: StudioI18n.remote(error.message)});}
    } finally {
      const id = streaming && streaming.chat;streaming = null;controls();
      if (id) {await settle(id);}
    }
  }
  async function readStream(response, assistant, row) {
    const reader = response.body.getReader(), decoder = new TextDecoder();
    let buffer = '', scheduled = false, finished = false;
    const paint = () => {scheduled = false;const stick = atBottom();fill(row, assistant);row.think.open = !assistant.content && !!assistant.thinking;if (stick) toBottom();};
    const later = () => {if (!scheduled) {scheduled = true;requestAnimationFrame(paint);}};
    // A loop answers with one stored message per iteration: the next bubble opens when the next iteration shows something.
    const open = () => {
      if (!finished) return;
      finished = false;assistant = {role: 'assistant', content: '', thinking: '', steps: []};row = bubble(assistant);
      el('textMessages').append(row);toBottom();
    };
    const handle = event => {
      if (['delta', 'step', 'permission', 'done'].includes(event.type)) open();
      if (event.type === 'status') status(StudioI18n.remote(event.phase));
      else if (event.type === 'context') StudioAgent.context(event);
      else if (event.type === 'iteration') {
        Object.assign(assistant, event.message);paint();finished = true;
        status(StudioI18n.t("text.read_stream_iteration_finished_handoff_saved_next_starts", [event.number, event.total, event.path]));
      } else if (event.type === 'delta') {
        assistant.content += event.content || '';assistant.thinking += event.thinking || '';status(streaming?.agent ? StudioI18n.t("text.read_stream_agent_working") : StudioI18n.t("text.read_stream_model_responding"));
        later();
      } else if (event.type === 'step') {
        const index = assistant.steps.findIndex(item => item.id === event.step.id);
        if (index < 0) assistant.steps.push(event.step); else assistant.steps[index] = event.step;
        // A step update arrives right after its permission request: keep the request's status line until it is answered.
        if (!row.asks.querySelector('.agent-ask:not([data-answered])')) status(StudioI18n.t("text.read_stream_agent", [event.step.title || event.step.tool]));
        later();
      } else if (event.type === 'permission') {
        const stick = atBottom();
        row.asks.append(StudioAgent.permissionCard(event.request, streaming.chat, message => status(message)));
        status(StudioI18n.t("text.read_stream_agent_waiting_decision_allow_reject_action"));
        if (stick) toBottom();
      } else if (event.type === 'permission_done') {
        StudioAgent.resolve(row.asks.querySelector(`[data-request="${CSS.escape(event.id)}"]`), event.reply);
      } else if (event.type === 'done') {
        Object.assign(assistant, event.message);paint();
        const s = event.message.stats;
        status(StudioI18n.remote(event.error) ? StudioI18n.t("text.note_response_interrupted") + StudioI18n.remote(event.error) : streaming?.agent ? (event.message.stopped ? StudioI18n.t("text.read_stream_agent_stopped") : StudioI18n.t("text.read_stream_agent_finished_steps", [(event.message.steps || []).length]))
          : s ? StudioI18n.t("text.read_stream_done_tok_s_tokens_model_load", [fmt(s.tokens_per_sec), s.completion_tokens, fmt(s.load_sec)]) : StudioI18n.t("text.generation_stopped_status_message"));
        if (StudioI18n.remote(event.error)) StudioUI.notify({module: 'Text', error: true, message: StudioI18n.remote(event.error)});
      } else if (event.type === 'error') {
        row.remove();status(StudioI18n.remote(event.error));StudioUI.notify({module: 'Text', error: true, message: StudioI18n.remote(event.error)});
      }
    };
    for (;;) {
      const {done, value} = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, {stream: true});
      let index;
      while ((index = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, index).trim();buffer = buffer.slice(index + 1);
        if (line) {try {handle(JSON.parse(line));} catch (error) {if (error instanceof SyntaxError) continue;throw error;}}
      }
    }
  }
  async function settle(id) {
    // The server saved the answer (or its partial text); reload it so the page shows exactly what is stored.
    for (let attempt = 0; attempt < 6; attempt++) {
      try {
        if (attempt) await new Promise(r => setTimeout(r, 400));
        const fresh = await api('chat?id=' + encodeURIComponent(id));
        // The server may still be saving the last words (Stop, error): wait until the chat ends with the model's turn.
        if (attempt < 5 && fresh.messages[fresh.messages.length - 1]?.role !== 'assistant') continue;
        if (current && current.id === id) {current = fresh;renderChat();}
        await loadChats();return;
      } catch {}
    }
  }
  async function stop() {
    if (!streaming) return;
    const {controller, chat, agent} = streaming;el('textStop').disabled = true;
    try {if (agent) await StudioAgent.api('stop', {chat_id: chat}); else await api('stop', {chat_id: chat});} catch {}
    controller.abort();status(StudioI18n.t("text.stop_stopped_received_text_remains_saved_chat"));
  }

  // ---- wiring -------------------------------------------------------------------------------------------------
  function showModule() {
    for (const name of ['image', 'video', 'music', 'text']) {
      el(name + 'Workspace').hidden = name !== 'text';
      el(name + 'Tab').setAttribute('aria-selected', String(name === 'text'));
    }
    document.body.dataset.studioModule = 'text';
    refresh();
    clearInterval(statusTimer);statusTimer = setInterval(pollStatus, 5000);
  }
  el('textTab').onclick = showModule;
  for (const name of ['imageTab', 'videoTab', 'musicTab', 'voiceoverTab']) el(name).addEventListener('click', () => {el('textWorkspace').hidden = true;el('textTab').setAttribute('aria-selected', 'false');clearInterval(statusTimer);});
  el('textForm').onsubmit = event => {event.preventDefault();send(false);};
  el('textInput').addEventListener('keydown', event => {if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {event.preventDefault();send(false);}});
  el('textAttach').onclick = () => el('textFile').click();
  el('textFile').onchange = async () => {const files = [...el('textFile').files];el('textFile').value = '';await addFiles(files);};
  el('textInput').addEventListener('paste', event => {
    const files = [...(event.clipboardData?.files || [])].filter(f => f.type.startsWith('image/'));
    if (files.length) {event.preventDefault();addFiles(files);}
  });
  for (const type of ['dragover', 'drop']) el('textForm').addEventListener(type, event => {
    if (![...(event.dataTransfer?.types || [])].includes('Files')) return;
    event.preventDefault();
    if (type === 'drop') addFiles([...event.dataTransfer.files].filter(f => f.type.startsWith('image/')));
  });
  el('textStop').onclick = stop;
  el('textRegenerate').onclick = () => send(true);
  el('textNewChat').onclick = () => newChat().catch(error => status(StudioI18n.remote(error.message)));
  el('textSearch').oninput = renderChats;
  el('textRename').onclick = async () => {
    if (!current) return;
    const title = window.prompt(StudioI18n.t("text.rename_chat_title"), current.title);
    if (title && title.trim()) {try {current = await api('chat-rename', {id: current.id, title});renderChat();await loadChats();} catch (error) {status(StudioI18n.remote(error.message));}}
  };
  el('textDelete').onclick = async () => {
    if (!current || !window.confirm(StudioI18n.t("text.delete_chat_history", [current.title]))) return;
    try {await api('chat-delete', {id: current.id});current = null;remember('');await loadChats();if (chats.length)await openChat(chats[0].id);else renderChat();} catch (error) {status(StudioI18n.remote(error.message));}
  };
  const saveChat = async data => {if (!current) return;try {current = await api('chat-update', {id: current.id, ...data});await loadChats();status(StudioI18n.t("text.save_chat_settings_saved"));} catch (error) {status(StudioI18n.remote(error.message));}};
  el('textSystem').onchange = () => saveChat({system: el('textSystem').value});
  el('textThink').onchange = () => saveChat({think: el('textThink').checked});
  el('textModel').onchange = () => {if (current)saveChat({model: el('textModel').value});modelSettings();controls();};
  el('textNative').onchange = modelSettings;
  for (const radio of document.querySelectorAll('input[name="textMode"]')) radio.onchange = () => {
    if (!radio.checked || streaming) return;
    mode = radio.value;
    // Switching the mode starts a new chat of that kind; the open chat stays in the list unchanged.
    if (current && (current.kind === 'agent') !== (mode === 'agent')) {current = null;remember('');}
    StudioAgent.show(current);renderChat();renderChats();header();
  };
  StudioAgent.bind(async (field, value) => {
    if (field === 'error') {status(value);return;}
    try {
      if (field.startsWith('loop')) {
        // Loop settings are the user's defaults for every agent task, not a property of one chat.
        await StudioAgent.api('settings', {[field]: value});
        if (StudioAgent.info) StudioAgent.info.settings[field] = value;
        status(field === 'loop' ? (value ? StudioI18n.t("text.agent_task_loop_enabled_status_hint") : StudioI18n.t("text.agent_task_loop_disabled_status")) : StudioI18n.t("text.agent_task_loop_settings_saved_status"));
      } else if (field === 'agent') {
        mode = 'agent';el('agentContext').hidden = true;
        // A chat belongs to one agent: choosing another one starts a new chat with it on the next task.
        if (current && current.kind === 'agent' && current.agent.id !== value) {current = null;remember('');renderChat();status(StudioI18n.t("text.agent_selection_next_task_new_chat_hint"));}
        await StudioAgent.api('settings', {agent: value});
        if (StudioAgent.info) StudioAgent.info.settings.agent = value;
      } else if (current && current.kind === 'agent') {
        current = await StudioAgent.api('chat-update', {id: current.id, [field]: value});StudioAgent.show(current);
        status(field === 'workspace' ? StudioI18n.t("text.agent_project_folder_new_session_hint") : StudioI18n.t("text.agent_chat_permissions_changed_status"));
      } else if (value) {
        await StudioAgent.api('settings', {[field]: value});
        if (StudioAgent.info) StudioAgent.info.settings[field] = value;
      }
    } catch (error) {status(StudioI18n.remote(error.message));if (current) StudioAgent.show(current);}
    controls();
  });
  el('agentWorkspace').addEventListener('input', controls);
  el('textSaveSettings').onclick = async () => {
    try {
      const number = key => Number(el('textP-' + key).value);
      const native = el('textNative').checked;
      settings = await api('settings', {...(native ? {} : Object.fromEntries(PARAMS.map(key => [key, number(key)]))), native_parameters: native, keep_alive: el('textP-keep_alive').value, model: el('textModel').value});
      modelSettings();
      status(StudioI18n.t("text.save_settings_saved_changes_apply_next_response"));
    } catch (error) {status(StudioI18n.remote(error.message));}
  };
  el('textUnload').onclick = async () => {
    try {const result = await api('unload', {});status(result.unloaded.length ? StudioI18n.t("text.unload_model_unloaded_memory_gpu_free") : StudioI18n.t("text.unload_model_already_unloaded"));} catch (error) {status(StudioI18n.remote(error.message));}
  };
  window.addEventListener('beforeunload', event => {if (streaming) {event.preventDefault();event.returnValue = '';}});
  controls();
})();
