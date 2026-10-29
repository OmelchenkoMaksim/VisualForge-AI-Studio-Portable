/* Explicit presentation bindings keep language changes separate from user text and running jobs. */
(() => {
  'use strict';
  const catalog = globalThis.VisualForgeStrings || {};
  const storageKey = 'vf-language';
  const supported = new Set(['ru', 'en']);
  const doc = globalThis.document;
  let language = 'ru';
  try { const saved = localStorage.getItem(storageKey); if (supported.has(saved)) language = saved; } catch {}
  if (doc?.documentElement) doc.documentElement.lang = language;
  const nodes = new Set(), bindings = new WeakMap(), owned = new WeakMap();
  // A translated template can contain user parameters that look like UI labels.
  // The compatibility translator must leave those freshly formatted values intact.
  const formatted = new Set();
  const exact = {ru:new Map(), en:new Map()}, patterns = {ru:[], en:[]}, fragments = {ru:[], en:[]};
  const token = /\{(\d+)}|%(?:[-+#0]*\d*(?:\.\d+)?[sdifg]|%)/g;
  const escape = text => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  for (const [key, entry] of Object.entries(catalog)) {
    for (const from of ['ru', 'en']) {
      const source = entry[from];
      if (!exact[from].has(source)) exact[from].set(source, key);
      const tokens = [...source.matchAll(token)].filter(item => item[0] !== '%%');
      if (tokens.length && source.replace(token, '').trim()) {
        let cursor = 0, expression = '^', number = 0;
        const order = [];
        for (const item of tokens) {
          expression += escape(source.slice(cursor, item.index).replace(/%%/g, '%')) + '([\\s\\S]*?)';
          order.push(item[1] === undefined ? number++ : Number(item[1]));
          cursor = item.index + item[0].length;
        }
        expression += escape(source.slice(cursor).replace(/%%/g, '%')) + '$';
        patterns[from].push({key, regex:new RegExp(expression), order, size:source.replace(token, '').length});
      } else if (source.length >= 4 && source.trim() && (source.startsWith(' ') || /[ :：]$/.test(source))) {
        fragments[from].push({key, source});
      }
    }
  }
  for (const from of ['ru', 'en']) {
    patterns[from].sort((a,b) => b.size-a.size);
    fragments[from].sort((a,b) => b.source.length-a.source.length);
  }
  function format(text, values=[]) {
    return text.replace(/\{(\d+)}/g, (original, index) => index in values ? String(values[index]) : original);
  }
  function t(key, values=[]) {
    const value=format((catalog[key] || {})[language] || catalog[key]?.ru || key, values);
    if(formatted.size>4096)formatted.clear();
    formatted.add(value);
    return value;
  }
  function source(key, values=[]) { return format(catalog[key]?.ru || key, values); }
  function percent(text, values) {
    let index=0;
    return text.replace(token, original => original==='%%' ? '%' : values[index++] ?? original);
  }
  function remote(value, depth=0) {
    if (typeof value !== 'string' || !value || value.length > 16384 || depth > 4) return value;
    if (formatted.has(value)) return value;
    const from = language === 'en' ? 'ru' : 'en';
    const key=exact[from].get(value);
    if (key) return t(key);
    if (/^[✓!×] /.test(value)) return value.slice(0,2)+remote(value.slice(2),depth+1);
    for (const item of patterns[from]) {
      const match=item.regex.exec(value);
      if (!match) continue;
      const args=[];item.order.forEach((index,i) => {args[index]=match[i+1];});
      const translated=catalog[item.key][language];
      // Captured parameters can be filenames, paths or user text. Never translate their contents.
      return /\{\d+}/.test(translated) ? format(translated,args) : percent(translated,args);
    }
    const separated=value.split(/( · |; |, |\n)/);
    if (separated.length>1) return separated.map((part,index)=>index%2?part:remote(part,depth+1)).join('');
    for (const {key:fragmentKey,source:part} of fragments[from]) {
      if (value.startsWith(part)) return t(fragmentKey)+remote(value.slice(part.length),depth+1);
      if (value.endsWith(part)) return remote(value.slice(0,-part.length),depth+1)+t(fragmentKey);
    }
    return value;
  }
  function write(target, property, binding) {
    const value=binding.read();
    const shown=binding.system ? remote(value) : value;
    if (property.startsWith('@')) {
      const name=property.slice(1);if(target.getAttribute?.(name)!==String(shown))target.setAttribute(name,shown);
    } else if (target[property] !== shown) target[property]=shown;
    return value;
  }
  function bind(target, property, read, system=true) {
    if (['@title','@placeholder','@alt'].includes(property)) property=property.slice(1);
    let fields=bindings.get(target);
    if (!fields) {fields=new Map();bindings.set(target,fields);nodes.add(new WeakRef(target));}
    if(!owned.has(target))owned.set(target,new Set());
    owned.get(target).add(property);
    const binding={read,system};
    fields.set(property,binding);
    const value=write(target,property,binding);
    // Clearing a reused container must also discard its previous translation binding.
    if (value === '') fields.delete(property);
    return value;
  }
  function refresh() {
    for (const reference of nodes) {
      const node=reference.deref();
      if (!node) {nodes.delete(reference);continue;}
      const fields=bindings.get(node);
      if (!fields || node.isConnected === false) continue;
      for (const [property,binding] of fields) {
        try {write(node,property,binding);} catch {
          // A completed task may invalidate a captured view model. Keep its last rendered value.
        }
      }
    }
  }
  function setLanguage(next, persist=true) {
    if (!supported.has(next)) return false;
    if (persist) {try {localStorage.setItem(storageKey,next);} catch {}}
    if (next===language) return true;
    language=next;
    formatted.clear();
    if(doc?.documentElement)doc.documentElement.lang=language;
    refresh();
    globalThis.dispatchEvent?.(new CustomEvent('studio-languagechange',{detail:{language}}));
    return true;
  }
  function initStatic() {
    if (!doc?.createTreeWalker) return;
    const walker=doc.createTreeWalker(doc.documentElement,128);
    let comment;
    while ((comment=walker.nextNode())) {
      const match=/^i18n:([a-z][\w.]+)$/.exec(comment.data);
      const text=comment.nextSibling;
      if (!match || text?.nodeType!==3 || !catalog[match[1]]) continue;
      const original=text.nodeValue, leading=original.match(/^\s*/)[0], trailing=original.match(/\s*$/)[0];
      bind(text,'nodeValue',()=>leading+t(match[1])+trailing,false);
    }
    for (const node of doc.querySelectorAll('[data-i18n-attrs]')) {
      for (const [attribute,key] of Object.entries(JSON.parse(node.dataset.i18nAttrs))) {
        const property=['title','placeholder','alt'].includes(attribute)?attribute:'@'+attribute;
        // Deferred scripts can update attributes before DOMContentLoaded. Static fallbacks must not replace that state.
        if(!owned.get(node)?.has(property))bind(node,property,()=>t(key),false);
      }
    }
    const select=doc.getElementById('studioLanguage');
    if(select){select.value=language;select.addEventListener('change',()=>setLanguage(select.value));}
    globalThis.addEventListener?.('studio-languagechange',()=>{if(select)select.value=language;});
  }
  function option(read, value, defaultSelected=false, selected=false) {
    const node=new Option('',value,defaultSelected,selected);
    bind(node,'textContent',read);
    return node;
  }
  function value(target, property, snapshot, system=true) {return bind(target,property,()=>snapshot,system);}
  function append(target,property,read,system=true) {
    const previous=bindings.get(target)?.get(property)?.read;
    const snapshot=target[property];
    return bind(target,property,()=>String(previous?previous():snapshot)+read(),system);
  }
  globalThis.StudioI18n={t,source,remote,bind,value,append,option,setLanguage,refresh,initStatic,get language(){return language;},get locale(){return language==='en'?'en-US':'ru-RU';}};
  globalThis.addEventListener?.('storage',event=>{if(event.key===storageKey||event.key===null)setLanguage(event.newValue===null?'ru':event.newValue,false);});
  if(doc?.readyState==='loading')doc.addEventListener('DOMContentLoaded',initStatic,{once:true});else initStatic();
})();
