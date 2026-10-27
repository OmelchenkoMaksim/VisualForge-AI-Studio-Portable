'use strict';
(() => {
  const el = id => document.getElementById(id);
  const LAST_KEY = 'visualforge.text.last-chat.v1';
  let available = false, current = null, chats = [], settings = null, models = [], streaming = null, statusTimer = null, pending = [], mode = 'chat', agentError = '';
  // The open chat decides the mode; without one the «Чат | Агент» switch decides what the next chat will be.
  const agentMode = () => current ? current.kind === 'agent' : mode === 'agent';
  const MAX_PICTURES = 4, MAX_SIDE = 1536, MAX_BYTES = 5 * 1024 * 1024;
  const fmt = (n, digits = 1) => Number(n).toLocaleString('ru-RU', {maximumFractionDigits: digits});
  const gb = bytes => fmt(bytes / 1024 ** 3);
  const api = async (route, data) => {
    let response;
    try {response = await fetch('/api/text/' + route, data === undefined ? {} : {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)});}
    catch {throw new Error('Нет связи со студией. Проверьте, запущено ли приложение.');}
    let value;
    try {value = await response.json();} catch {throw new Error('Некорректный ответ сервиса Text.');}
    if (!response.ok) throw new Error(value.error || 'Text недоступен');
    return value;
  };
  const remember = id => {try {localStorage.setItem(LAST_KEY, id || '');} catch {}};
  const recall = () => {try {return localStorage.getItem(LAST_KEY) || '';} catch {return '';}};
  const status = text => {el('textStatus').textContent = text;};

  // ---- rendering: text is only ever inserted with textContent, so model output cannot inject markup ----
  function inline(parent, text) {
    for (const part of text.split(/(`[^`\n]+`|\*\*[^*\n]+\*\*)/)) {
      if (!part) continue;
      if (part.startsWith('`') && part.endsWith('`') && part.length > 2) {const code = document.createElement('code');code.textContent = part.slice(1, -1);parent.append(code);}
      else if (part.startsWith('**') && part.endsWith('**') && part.length > 4) {const strong = document.createElement('strong');strong.textContent = part.slice(2, -2);parent.append(strong);}
      else parent.append(part);
    }
  }
  function codeBlock(language, code) {
    const box = document.createElement('div'), head = document.createElement('div'), label = document.createElement('span'), copy = document.createElement('button'), pre = document.createElement('pre'), body = document.createElement('code');
    box.className = 'text-code';head.className = 'text-code-head';label.textContent = language || 'код';
    copy.type = 'button';copy.className = 'secondary';copy.textContent = 'Копировать';
    copy.onclick = async () => {try {await navigator.clipboard.writeText(code);copy.textContent = 'Скопировано';setTimeout(() => {copy.textContent = 'Копировать';}, 1500);} catch {copy.textContent = 'Не удалось';}};
    body.textContent = code;pre.append(body);head.append(label, copy);box.append(head, pre);
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
    row.className = 'text-msg text-' + message.role;head.className = 'text-msg-head';who.textContent = message.role === 'user' ? 'Вы' : agentMode() ? 'Агент' : 'Модель';
    head.append(who);
    if (message.created) {const time = document.createElement('span');time.className = 'muted';time.textContent = new Date(message.created * 1000).toLocaleString('ru-RU');head.append(time);}
    body.className = 'text-body';row.append(head);
    if (message.images && message.images.length) {
      const strip = document.createElement('div');strip.className = 'text-pictures';
      for (const meta of message.images) {
        const link = document.createElement('a'), picture = document.createElement('img');
        link.href = meta.local || `/api/text/image?chat=${encodeURIComponent(current ? current.id : '')}&id=${encodeURIComponent(meta.id)}`;link.target = '_blank';link.rel = 'noopener';link.title = meta.name || 'Изображение';
        picture.src = link.href;picture.alt = meta.name || 'Прикреплённое изображение';picture.loading = 'lazy';link.append(picture);strip.append(link);
      }
      row.append(strip);
    }
    if (message.role === 'assistant') {
      const think = document.createElement('details'), summary = document.createElement('summary'), thoughts = document.createElement('div');
      think.className = 'text-think';think.hidden = true;summary.textContent = 'Размышления модели';thoughts.className = 'text-think-body';think.append(summary, thoughts);row.append(think);
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
    rich(row.body, message.content || '');
    if (row.think) {
      row.think.hidden = !message.thinking;
      row.thoughts.textContent = message.thinking || '';
      row.think.querySelector('summary').textContent = `Размышления модели · ${fmt((message.thinking || '').length, 0)} симв.`;
    }
    if (row.steps) window.StudioAgent?.renderSteps(row.steps, message.steps);
    row.foot.replaceChildren();
    if (message.role === 'assistant' && message.stats) {
      const s = message.stats, note = document.createElement('span');note.className = 'muted';
      note.textContent = s.model_calls === undefined ? `${fmt(s.tokens_per_sec)} ток/с · ${s.completion_tokens} ток. · контекст ${s.prompt_tokens + s.completion_tokens} из ${s.context}`
        : [`${s.model_calls} обращ. к модели`, s.completion_tokens === undefined ? '' : `${fmt(s.completion_tokens, 0)} ток. ответа`,  // agent task totals; dsh reports no output tokens
          `контекст ${fmt(s.context_tokens, 0)} из ${fmt(s.context, 0)}`].filter(Boolean).join(' · ');
      row.foot.append(note);
    }
    if (message.stopped || message.error) {
      const note = document.createElement('span');note.className = 'text-warn';note.textContent = message.error ? 'Ответ прерван: ' + message.error : 'Остановлено вами';row.foot.append(note);
    }
    if (message.content) {
      const copy = document.createElement('button');copy.type = 'button';copy.className = 'secondary';copy.textContent = 'Копировать';
      copy.onclick = async () => {try {await navigator.clipboard.writeText(message.content);copy.textContent = 'Скопировано';setTimeout(() => {copy.textContent = 'Копировать';}, 1500);} catch {copy.textContent = 'Не удалось';}};
      row.foot.append(copy);
    }
  }
  const atBottom = () => {const box = el('textMessages');return box.scrollHeight - box.scrollTop - box.clientHeight < 80;};
  const toBottom = () => {const box = el('textMessages');box.scrollTop = box.scrollHeight;};
  function renderChat() {
    const box = el('textMessages');box.replaceChildren();
    el('textTitle').textContent = current ? current.title : 'Новый чат';
    if (!current || !current.messages.length) {
      const hint = document.createElement('p');hint.className = 'muted text-empty';
      hint.textContent = agentMode() ? 'Выберите папку проекта и опишите задачу. Агент читает файлы, предлагает правки и команды; перед каждым действием он спросит разрешения (если права это требуют). Всё работает на вашем компьютере, без интернета.'
        : 'Напишите первое сообщение. Модель работает прямо на вашем компьютере, без интернета; история чатов хранится в папке программы.';
      box.append(hint);
    }
    for (const message of current ? current.messages : []) box.append(bubble(message));
    controls();toBottom();
  }
  function renderChats() {
    const query = el('textSearch').value.trim().toLowerCase(), list = el('textChats');list.replaceChildren();
    const shown = chats.filter(c => !query || c.title.toLowerCase().includes(query));
    if (!shown.length) {const empty = document.createElement('p');empty.className = 'muted';empty.textContent = chats.length ? 'Ничего не найдено' : 'Чатов пока нет';list.append(empty);}
    for (const chat of shown) {
      const item = document.createElement('button');item.type = 'button';item.className = 'text-chat-item';item.setAttribute('role', 'listitem');
      if (current && current.id === chat.id) item.setAttribute('aria-current', 'true');
      const title = document.createElement('strong'), meta = document.createElement('span');
      title.textContent = chat.title;meta.className = 'muted';meta.textContent = `${chat.messages} сообщ. · ${new Date(chat.updated * 1000).toLocaleDateString('ru-RU')}`;
      if (chat.kind === 'agent') {const badge = document.createElement('span');badge.className = 'agent-badge';badge.textContent = 'Агент';item.append(badge);}
      item.append(title, meta);item.onclick = () => openChat(chat.id);list.append(item);
    }
  }
  function controls() {
    const busy = !!streaming, agent = agentMode();
    const agentReady = !agent || (!!window.StudioAgent?.ready() && (!!current || !!StudioAgent.values().workspace));
    const ready = available && models.some(model => model.name === el('textModel').value) && !busy && agentReady;
    el('textSend').disabled = !ready;el('textStop').disabled = !busy;el('textInput').disabled = !available;
    el('textSend').textContent = agent ? 'Дать задание' : 'Отправить';
    el('textInput').placeholder = agent ? 'Опишите задачу для агента. Enter — отправить, Shift+Enter — новая строка' : 'Напишите сообщение. Enter — отправить, Shift+Enter — новая строка';
    const vision = visionAvailable();
    el('textAttach').disabled = !ready || !vision || agent || pending.length >= MAX_PICTURES;
    el('textAttach').title = agent ? 'Агенту изображения пока не передаются' : vision ? 'PNG или JPEG; картинку можно также вставить из буфера (Ctrl+V) или перетащить в поле ввода' : 'Выбранная модель не умеет работать с изображениями';
    const last = current && current.messages[current.messages.length - 1];
    el('textRegenerate').disabled = !ready || !last || agent;
    el('textRename').disabled = !current || busy;el('textDelete').disabled = !current || busy;el('textNewChat').disabled = busy;
    el('textSystem').disabled = !current || busy;el('textThink').disabled = !current || busy || agent;
    el('agentPanel').hidden = !agent;
    for (const radio of document.querySelectorAll('input[name="textMode"]')) {radio.checked = radio.value === (agent ? 'agent' : 'chat');radio.disabled = busy;}
    for (const id of ['agentKind', 'agentWorkspace', 'agentChoose', 'agentPermission']) el(id).disabled = busy || !window.StudioAgent?.info;
    el('agentKind').disabled ||= !!current;  // a chat keeps the agent it was started with
    if (agent && agentError) el('agentInfo').textContent = agentError;
  }

  // ---- attachments: pictures are shrunk in the browser (long side 1536) and sent as PNG/JPEG ---------------------
  const visionAvailable = () => !!models.find(m => m.name === el('textModel').value && m.vision);
  const readData = blob => new Promise((resolve, reject) => {const reader = new FileReader();reader.onload = () => resolve(String(reader.result));reader.onerror = () => reject(new Error('Не удалось прочитать файл'));reader.readAsDataURL(blob);});
  async function prepare(file) {
    if (!/^image\//.test(file.type)) throw new Error(`«${file.name}» — не изображение`);
    if (file.size > 40 * 1024 * 1024) throw new Error(`«${file.name}» больше 40 МиБ`);
    let bitmap;
    try {bitmap = await createImageBitmap(file);} catch {throw new Error(`Не удалось открыть «${file.name}»`);}
    const scale = Math.min(1, MAX_SIDE / Math.max(bitmap.width, bitmap.height));
    if (scale === 1 && (file.type === 'image/png' || file.type === 'image/jpeg') && file.size <= MAX_BYTES) {bitmap.close();return {name: file.name, data: await readData(file)};}
    const canvas = document.createElement('canvas');canvas.width = Math.max(1, Math.round(bitmap.width * scale));canvas.height = Math.max(1, Math.round(bitmap.height * scale));
    const context = canvas.getContext('2d');context.fillStyle = '#fff';context.fillRect(0, 0, canvas.width, canvas.height);context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);bitmap.close();
    for (const quality of [0.92, 0.8, 0.65]) {
      const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg', quality));
      if (blob && blob.size <= MAX_BYTES) return {name: file.name.replace(/\.[^.]+$/, '') + '.jpg', data: await readData(blob)};
    }
    throw new Error(`«${file.name}» слишком тяжёлое даже после сжатия`);
  }
  async function addFiles(files) {
    if (!visionAvailable()) {status('Выбранная модель не умеет работать с изображениями.');return;}
    for (const file of files) {
      if (pending.length >= MAX_PICTURES) {status(`К сообщению можно прикрепить до ${MAX_PICTURES} изображений.`);break;}
      try {pending.push(await prepare(file));} catch (error) {status(error.message);}
    }
    renderPending();
  }
  function renderPending() {
    const strip = el('textAttachments');strip.replaceChildren();strip.hidden = !pending.length;
    pending.forEach((item, index) => {
      const figure = document.createElement('figure'), picture = document.createElement('img'), remove = document.createElement('button');
      picture.src = item.data;picture.alt = item.name;picture.title = item.name;
      remove.type = 'button';remove.className = 'secondary';remove.textContent = '×';remove.setAttribute('aria-label', 'Убрать изображение ' + item.name);
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
    return source ? `${model.name}\nДата выпуска базовой модели; дата пользовательской настройки не установлена.\n${source}` : model.name;
  }
  function header() {
    const model = models.find(row => row.name === el('textModel').value);
    StudioUI.setHeader('text', {model:model ? modelLabel(model).split(' · ')[0] : 'Text',
      description:agentMode() ? 'Локальный агент: файлы проекта, команды и git' : model?.vision ? 'Локальный диалог с моделью — текст и изображения' : 'Локальный диалог с текстовой моделью',
      tone:available && model ? 'good' : 'bad',
      status:available && model ? 'Ollama: программа и файлы модели доступны. Модель загружается при отправке сообщения.' : available ? 'Модель этого чата отключена или недоступна. Включите её в «Компонентах» либо выберите другую модель.' : el('textAvailability').textContent});
  }
  function modelSettings() {
    header();
    if (!settings) return;
    const model = models.find(m => m.name === el('textModel').value), native = el('textNative').checked;
    for (const key of PARAMS) {
      const field = el('textP-' + key);
      field.disabled = native;
      field.value = native ? (model?.parameters?.[key] ?? (key === 'num_ctx' ? settings.num_ctx : '')) : settings[key];
      field.placeholder = native ? 'По умолчанию в Ollama' : '';
    }
    if (model && available) {
      el('textModel').title = modelTitle(model);
      el('textAvailability').textContent = `${modelLabel(model)} · ${gb(model.size)} ГБ · ${model.vision ? 'текст и изображения' : 'только текст'}`
        + (model.size > 16 * 1024 ** 3 ? ' · часть модели может работать в оперативной памяти' : '');
    }
  }

  // ---- data ---------------------------------------------------------------------------------------------------
  async function loadChats() {chats = (await api('chats')).chats;renderChats();}
  async function openChat(id) {
    try {
      current = await api('chat?id=' + encodeURIComponent(id));remember(id);
      el('textSystem').value = current.system || '';el('textThink').checked = current.think !== false;
      if (current.model) {
        // Preserve the chat's chosen model; silently selecting another build would misrepresent its next answer.
        if (![...el('textModel').options].some(o => o.value === current.model)) {
          const option = new Option(current.model + ' · отключена или недоступна', current.model);
          option.disabled = true;el('textModel').append(option);
        }
        el('textModel').value = current.model;
      }
      window.StudioAgent?.show(current);
      modelSettings();renderChat();renderChats();
    } catch (error) {status(error.message);remember('');current = null;renderChat();}
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
      el('textModel').replaceChildren(...models.map(m => {const option = new Option(`${modelLabel(m)} · ${gb(m.size)} ГБ`, m.name);option.title = modelTitle(m);return option;}));
      el('textModel').value = info.default_model || '';
      el('textNative').checked = settings.native_parameters !== false;
      el('textP-keep_alive').value = settings.keep_alive;
      el('textAvailability').textContent = available ? `Модель: ${info.default_model} · работает локально через Ollama, внутри программы` : info.reason;
      try {await StudioAgent.load();agentError = '';StudioAgent.show(current);} catch (error) {agentError = error.message;}
      modelSettings();
      await loadChats();
      const wanted = current ? current.id : recall();
      if (wanted && chats.some(c => c.id === wanted)) await openChat(wanted);
      else if (chats.length && !current) await openChat(chats[0].id);
      else renderChat();
      pollStatus();
    } catch (error) {el('textAvailability').textContent = error.message;available = false;controls();header();}
  }
  async function pollStatus() {
    if (el('textWorkspace').hidden) return;
    try {
      const info = await api('status'), model = info.loaded[0];
      if (!streaming) status(model ? `Модель в памяти: ${gb(model.size)} ГБ, из них на видеокарте ${gb(model.size_vram)} ГБ. Выгрузится сама через ${info.settings.keep_alive} простоя.` : info.running ? 'Модель не загружена: видеокарта свободна.' : 'Готово. Модель загрузится при первом сообщении.');
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
        if (!current.messages.length && current.title === 'Новый чат') {const flat = text.split(/\s+/).filter(Boolean).join(' ');current.title = flat.length > 80 ? flat.slice(0, 79).trimEnd() + '…' : (flat || 'Изображение');}
        current.messages.push({role: 'user', content: text, created: Date.now() / 1000, images: sentPictures.map(p => ({local: p.data, name: p.name}))});renderChat();el('textInput').value = '';
        pending = [];renderPending();
      }
      const assistant = {role: 'assistant', content: '', thinking: '', steps: []}, row = bubble(assistant);
      el('textMessages').append(row);toBottom();
      const controller = new AbortController();streaming = {controller, chat: current.id, agent};controls();
      status(agent ? 'Агент получает задание…' : 'Отправка…');
      const loop = agent ? StudioAgent.loop() : null;
      const body = agent ? {chat_id: current.id, content: text, ...(loop ? {loop} : {})} : regenerate ? {chat_id: current.id, regenerate: true} : {chat_id: current.id, content: text, images: sentPictures.map(p => ({name: p.name, data: p.data}))};
      const response = await fetch(agent ? '/api/agents/send' : '/api/text/send', {method: 'POST', headers: {'Content-Type': 'application/json'}, signal: controller.signal,
        body: JSON.stringify(body)})
        .catch(error => {
          // Preserve AbortError so Stop stays silent; other fetch failures use the connection message.
          if (error.name === 'AbortError') throw error;
          throw new Error('Нет связи со студией.');
        });
      if (!response.ok) {
        let message = 'Не удалось отправить сообщение';
        try {message = (await response.json()).error || message;} catch {}
        if (!regenerate) {current.messages.pop();el('textInput').value = text;pending = sentPictures;renderPending();}
        row.remove();renderChat();status(message);StudioUI.notify({module: 'Text', error: true, message});
        return;
      }
      await readStream(response, assistant, row);
    } catch (error) {
      if (error.name !== 'AbortError') {status(error.message);StudioUI.notify({module: 'Text', error: true, message: error.message});}
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
      if (event.type === 'status') status(event.phase);
      else if (event.type === 'context') StudioAgent.context(event);
      else if (event.type === 'iteration') {
        Object.assign(assistant, event.message);paint();finished = true;
        status(`Итерация ${event.number} из ${event.total} закончена, итог записан в ${event.path}. Следующая начинается со свежим контекстом…`);
      } else if (event.type === 'delta') {
        assistant.content += event.content || '';assistant.thinking += event.thinking || '';status(streaming?.agent ? 'Агент работает…' : 'Модель отвечает…');
        later();
      } else if (event.type === 'step') {
        const index = assistant.steps.findIndex(item => item.id === event.step.id);
        if (index < 0) assistant.steps.push(event.step); else assistant.steps[index] = event.step;
        // A step update arrives right after its permission request: keep the request's status line until it is answered.
        if (!row.asks.querySelector('.agent-ask:not([data-answered])')) status(`Агент: ${event.step.title || event.step.tool}`);
        later();
      } else if (event.type === 'permission') {
        const stick = atBottom();
        row.asks.append(StudioAgent.permissionCard(event.request, streaming.chat, message => status(message)));
        status('Агент ждёт вашего решения: разрешите или отклоните действие.');
        if (stick) toBottom();
      } else if (event.type === 'permission_done') {
        StudioAgent.resolve(row.asks.querySelector(`[data-request="${CSS.escape(event.id)}"]`), event.reply);
      } else if (event.type === 'done') {
        Object.assign(assistant, event.message);paint();
        const s = event.message.stats;
        status(event.error ? 'Ответ прерван: ' + event.error : streaming?.agent ? (event.message.stopped ? 'Агент остановлен.' : `Агент закончил: шагов — ${(event.message.steps || []).length}.`)
          : s ? `Готово: ${fmt(s.tokens_per_sec)} ток/с, ${s.completion_tokens} токенов, загрузка модели ${fmt(s.load_sec)} с.` : 'Остановлено.');
        if (event.error) StudioUI.notify({module: 'Text', error: true, message: event.error});
      } else if (event.type === 'error') {
        row.remove();status(event.error);StudioUI.notify({module: 'Text', error: true, message: event.error});
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
    controller.abort();status('Остановлено: уже полученный текст сохраняется в чате.');
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
  for (const name of ['imageTab', 'videoTab', 'musicTab']) el(name).addEventListener('click', () => {el('textWorkspace').hidden = true;el('textTab').setAttribute('aria-selected', 'false');clearInterval(statusTimer);});
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
  el('textNewChat').onclick = () => newChat().catch(error => status(error.message));
  el('textSearch').oninput = renderChats;
  el('textRename').onclick = async () => {
    if (!current) return;
    const title = window.prompt('Название чата', current.title);
    if (title && title.trim()) {try {current = await api('chat-rename', {id: current.id, title});renderChat();await loadChats();} catch (error) {status(error.message);}}
  };
  el('textDelete').onclick = async () => {
    if (!current || !window.confirm(`Удалить чат «${current.title}» вместе с историей?`)) return;
    try {await api('chat-delete', {id: current.id});current = null;remember('');await loadChats();if (chats.length)await openChat(chats[0].id);else renderChat();} catch (error) {status(error.message);}
  };
  const saveChat = async data => {if (!current) return;try {current = await api('chat-update', {id: current.id, ...data});await loadChats();status('Настройки чата сохранены.');} catch (error) {status(error.message);}};
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
        status(field === 'loop' ? (value ? 'Цикл включён: следующее задание пойдёт итерациями.' : 'Цикл выключен.') : 'Настройки цикла сохранены.');
      } else if (field === 'agent') {
        // A chat belongs to one agent: choosing another one starts a new chat with it on the next task.
        if (current && current.kind === 'agent' && current.agent.id !== value) {current = null;remember('');renderChat();status('Следующее задание начнёт новый чат с выбранным агентом.');}
        await StudioAgent.api('settings', {agent: value});
        if (StudioAgent.info) StudioAgent.info.settings.agent = value;
      } else if (current && current.kind === 'agent') {
        current = await StudioAgent.api('chat-update', {id: current.id, [field]: value});StudioAgent.show(current);
        status(field === 'workspace' ? 'Папка проекта изменена: агент начнёт с ней новую сессию.' : 'Права агента для этого чата изменены.');
      } else if (value) {
        await StudioAgent.api('settings', {[field]: value});
        if (StudioAgent.info) StudioAgent.info.settings[field] = value;
      }
    } catch (error) {status(error.message);if (current) StudioAgent.show(current);}
    controls();
  });
  el('agentWorkspace').addEventListener('input', controls);
  el('textSaveSettings').onclick = async () => {
    try {
      const number = key => Number(el('textP-' + key).value);
      const native = el('textNative').checked;
      settings = await api('settings', {...(native ? {} : Object.fromEntries(PARAMS.map(key => [key, number(key)]))), native_parameters: native, keep_alive: el('textP-keep_alive').value, model: el('textModel').value});
      modelSettings();
      status('Параметры сохранены. Изменения применяются со следующего ответа.');
    } catch (error) {status(error.message);}
  };
  el('textUnload').onclick = async () => {
    try {const result = await api('unload', {});status(result.unloaded.length ? 'Модель выгружена из памяти: видеокарта свободна.' : 'Модель и так не загружена.');} catch (error) {status(error.message);}
  };
  window.addEventListener('beforeunload', event => {if (streaming) {event.preventDefault();event.returnValue = '';}});
  controls();
})();
