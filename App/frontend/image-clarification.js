/* Numbered choices beside the original Image request. No model instructions or user text become HTML. */
(function(root) {
  'use strict';
  const text = key => root.StudioI18n?.t(key) || key;
  let pending = null;

  function selection(value, count) {
    if (typeof value !== 'string' || !value.length || value.length > 40 || !/^[0-8\s,;]+$/.test(value)) {
      throw new Error(text('image.clarification_invalid_selection'));
    }
    let ids = [];
    for (const digit of value.match(/[0-8]/g) || []) {
      const id = Number(digit);
      ids = ids.filter(old => old !== id);
      ids.push(id);
    }
    if (!ids.length || ids.some(id => id > count) || (ids.includes(0) && ids.length !== 1)) {
      throw new Error(text('image.clarification_invalid_selection'));
    }
    return ids.join(',');
  }

  function dismiss() { pending?.(null); }

  function show(question) {
    dismiss();
    const doc = root.document, box = doc.getElementById('imageClarification');
    const title = doc.getElementById('imageClarificationQuestion'), list = doc.getElementById('imageClarificationOptions');
    const answer = doc.getElementById('imageClarificationAnswer'), error = doc.getElementById('imageClarificationError');
    const apply = doc.getElementById('imageClarificationApply'), cancel = doc.getElementById('imageClarificationCancel');
    title.textContent = root.StudioI18n.remote(question.question);
    list.replaceChildren();
    for (const option of question.options) {
      const item = doc.createElement('li');
      item.textContent = option.id + '. ' + option.label;
      list.append(item);
    }
    answer.value = ''; error.textContent = ''; box.hidden = false;
    answer.focus();
    return new Promise(resolve => {
      function finish(value) {
        apply.removeEventListener('click', submit);
        cancel.removeEventListener('click', stop);
        answer.removeEventListener('keydown', enter);
        box.hidden = true; pending = null;
        resolve(value);
      }
      function submit() {
        try { finish(selection(answer.value, question.options.length)); }
        catch (failure) { error.textContent = failure.message; answer.focus(); }
      }
      function stop() { finish(null); }
      function enter(event) {
        if (event.key === 'Enter') { event.preventDefault(); event.stopPropagation(); submit(); }
        if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); stop(); }
      }
      apply.addEventListener('click', submit);
      cancel.addEventListener('click', stop);
      answer.addEventListener('keydown', enter);
      pending = finish;
    });
  }

  async function run(send, payload, {ask=show, onState=()=>{}, onCancel=async()=>{}, isCurrent=()=>true}={}) {
    let response = await send(payload), seen = 0;
    while (response.state === 'clarification') {
      if (response.round !== seen + 1 || response.round > 2 || !Array.isArray(response.options) ||
          response.options.length < 2 || response.options.length > 8) {
        throw new Error(text('image.clarification_invalid_request'));
      }
      seen = response.round;
      onState('waiting');
      const value = await ask(response);
      if (value === null || !isCurrent()) {
        await onCancel(response.clarification_token);
        if (value !== null) throw new Error(text('image.clarification_session_expired'));
        return null;
      }
      const answer = selection(value, response.options.length);
      onState(seen === 2 || answer === '0' ? 'accepted' : 'preparing');
      response = await send({...payload, clarification_token:response.clarification_token, clarification_answer:answer});
    }
    if (seen) onState('accepted');
    return response;
  }

  const api = {selection, run, dismiss};
  root.ImageClarification = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis === 'undefined' ? window : globalThis);
