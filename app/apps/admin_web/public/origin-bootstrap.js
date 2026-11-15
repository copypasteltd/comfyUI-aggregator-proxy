/* Scoped adapter for the installed native frontend. Never contains the API Key. */
(() => {
  'use strict';
  const { prefix, csrf, namespace } = window.__originConfig;
  const root = new URL(prefix, location.origin);
  const nativeRoots = /^(?:api|ws|assets|scripts|extensions|view|upload|object_info|queue|prompt|history|settings|userdata|users|models|embeddings|features|system_stats|workflow_templates|global_subgraphs|internal|manager|free|interrupt|v2)(?:\/|$)/;
  function scoped(value) {
    const url = new URL(value, document.baseURI);
    if (url.origin === location.origin && !url.pathname.startsWith(prefix) && nativeRoots.test(url.pathname.slice(1))) url.pathname = prefix + url.pathname.slice(1);
    return url;
  }
  // Native favicons and previews also assign DOM URL properties directly.
  // Rebase before the browser starts a root-relative resource request.
  for (const [type, property] of [[HTMLImageElement, 'src'], [HTMLMediaElement, 'src'], [HTMLSourceElement, 'src'], [HTMLScriptElement, 'src'], [HTMLLinkElement, 'href']]) {
    const descriptor = Object.getOwnPropertyDescriptor(type.prototype, property);
    if (descriptor?.set && descriptor.get && descriptor.configurable) Object.defineProperty(type.prototype, property, {
      ...descriptor, set(value) { return descriptor.set.call(this, scoped(String(value)).href); }
    });
  }
  const setAttribute = Element.prototype.setAttribute;
  Element.prototype.setAttribute = function (name, value) {
    if ((name === 'src' && /^(IMG|VIDEO|AUDIO|SOURCE|SCRIPT)$/.test(this.tagName)) || (name === 'href' && this.tagName === 'LINK')) value = scoped(String(value)).href;
    return setAttribute.call(this, name, value);
  };
  const originalFetch = window.fetch.bind(window);
  window.fetch = async (input, options = {}) => {
    const supplied = input instanceof Request ? input : null;
    const url = scoped(supplied ? supplied.url : String(input));
    if (url.origin !== root.origin || !url.pathname.startsWith(prefix)) return originalFetch(input, options);
    const headers = new Headers(options.headers || supplied?.headers);
    headers.set('X-Origin-CSRF', csrf);
    const method = (options.method || supplied?.method || 'GET').toUpperCase();
    if (method === 'POST' && /\/(?:api\/)?prompt$/.test(url.pathname) && !headers.has('Idempotency-Key')) {
      const bytes = crypto.getRandomValues(new Uint8Array(16));
      headers.set('Idempotency-Key', Array.from(bytes, b => b.toString(16).padStart(2, '0')).join(''));
    }
    const base = supplied ? new Request(url, supplied) : url;
    const response = await originalFetch(base, { ...options, headers, credentials: 'same-origin' });
    if (response.status === 401) document.dispatchEvent(new CustomEvent('origin-session-expired'));
    return response;
  };
  const NativeSocket = window.WebSocket;
  window.WebSocket = class extends NativeSocket {
    constructor(value, protocols) {
      const url = scoped(value);
      // Normalize origin for ws/wss, which URL.origin represents differently.
      if (url.host === location.host && !url.pathname.startsWith(prefix) && url.pathname.endsWith('/ws')) url.pathname = prefix + url.pathname.replace(/^\//, '');
      if (url.host === location.host && url.pathname.startsWith(prefix)) url.searchParams.set('origin_csrf', csrf);
      super(url, protocols);
    }
  };
  const xhrOpen = XMLHttpRequest.prototype.open, xhrSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (method, value, ...rest) { const url = scoped(String(value)); this.__originScoped = url.origin === root.origin && url.pathname.startsWith(prefix); return xhrOpen.call(this, method, url, ...rest); };
  XMLHttpRequest.prototype.send = function (...args) { if (this.__originScoped) this.setRequestHeader('X-Origin-CSRF', csrf); return xhrSend.apply(this, args); };
  for (const name of ['localStorage', 'sessionStorage']) {
    try {
      const storage = window[name], ns = 'comfy-origin:' + namespace + ':';
      const wrapped = {
        getItem: key => storage.getItem(ns + key), setItem: (key, value) => storage.setItem(ns + key, value), removeItem: key => storage.removeItem(ns + key),
        clear: () => { for (let i = storage.length - 1; i >= 0; i--) { const key = storage.key(i); if (key?.startsWith(ns)) storage.removeItem(key); } },
        key: i => Object.keys(storage).filter(key => key.startsWith(ns))[i]?.slice(ns.length) ?? null,
        get length() { return Object.keys(storage).filter(key => key.startsWith(ns)).length; }
      };
      Object.defineProperty(window, name, { value: new Proxy(wrapped, { get: (target, key) => key in target ? target[key] : storage.getItem(ns + String(key)), set: (_target, key, value) => { storage.setItem(ns + String(key), value); return true; } }) });
    } catch { /* Browsers that disable storage may still edit without persistence. */ }
  }
  function toolbar() {
    const bar = document.createElement('div');
    bar.setAttribute('role', 'region'); bar.setAttribute('aria-label', '平台设备操作');
    bar.style.cssText = 'position:fixed;bottom:12px;left:12px;z-index:2147483646;background:#d2f53c;color:#0c0c0c;padding:8px 14px;border-radius:20px;font:12px/1.5 system-ui;box-shadow:0 2px 12px #0003;max-width:calc(100vw - 24px);display:flex;gap:12px;align-items:center';
    const link = document.createElement('a'); link.href = '/origin/list'; link.target = '_blank'; link.rel = 'noopener'; link.textContent = '设备列表 / 归还'; link.style.color = '#0c0c0c';
    const text = document.createElement('span'); text.textContent = '原生模式 · 文件保存在设备';
    bar.append(link, text); document.body.append(bar);
    document.addEventListener('origin-session-expired', () => { text.textContent = '会话已过期，请返回设备列表登录'; });
    let timer;
    async function heartbeat() {
      if (document.hidden) return;
      try { await originalFetch('/origin/api/devices', { credentials: 'same-origin', cache: 'no-store' }); } catch { /* A disconnect does not release execution ownership. */ }
      timer = setTimeout(heartbeat, 60000);
    }
    document.addEventListener('visibilitychange', () => { clearTimeout(timer); if (!document.hidden) void heartbeat(); });
    window.addEventListener('pagehide', () => clearTimeout(timer), { once: true });
    timer = setTimeout(heartbeat, 60000);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', toolbar, { once: true }); else toolbar();
})();
