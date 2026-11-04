(() => {
  if (window.__lfInit) return;
  window.__lfInit = true;
  try {
    // Keep new tabs and popups in the same view.
    window.open = function (u) { if (u) location.href = new URL(u, location.href).href; return window; };
    document.addEventListener('click', e => {
      const a = e.target && e.target.closest && e.target.closest('a[target]');
      if (a && a.target !== '_self') a.target = '_self';
    }, true);
  } catch (e) {}

  // Network capture: the page's own calls to its backend (fetch, XHR, beacons) with request and response
  // headers, bodies and timing, plus WebSocket connections and their messages. Read with <network .../>.
  try {
    const MAXN = 150, BODY = 20000, WSMSG = 60;
    const net = window.__lfNet = [];
    const sockets = window.__lfWs = [];
    let seq = 0;
    const textish = t => /json|text|csv|xml|javascript|graphql|x-www-form-urlencoded/.test(t || '');
    const abs = u => { try { return new URL(String(u), location.href).href; } catch (e) { return String(u); } };
    const hdrs = h => {
      const o = {};
      try {
        if (!h) return o;
        if (typeof h.forEach === 'function' && !Array.isArray(h)) h.forEach((v, k) => { o[k] = String(v); });
        else if (Array.isArray(h)) h.forEach(p => { if (p && p.length > 1) o[String(p[0]).toLowerCase()] = String(p[1]); });
        else Object.keys(h).forEach(k => { o[k.toLowerCase()] = String(h[k]); });
      } catch (e) {}
      return o;
    };
    const bodyStr = b => {
      try {
        if (b == null) return '';
        if (typeof b === 'string') return b.slice(0, BODY);
        if (b instanceof URLSearchParams) return b.toString().slice(0, BODY);
        if (typeof FormData !== 'undefined' && b instanceof FormData) {
          const p = []; b.forEach((v, k) => p.push(k + '=' + (typeof v === 'string' ? v : '[file ' + (v.name || '') + ', ' + v.size + ' bytes]')));
          return p.join('&').slice(0, BODY);
        }
        if (typeof Blob !== 'undefined' && b instanceof Blob) return '[blob, ' + b.size + ' bytes]';
        if (b instanceof ArrayBuffer || ArrayBuffer.isView(b)) return '[binary, ' + b.byteLength + ' bytes]';
        return String(b).slice(0, BODY);
      } catch (e) { return ''; }
    };
    const add = e => {
      e.id = ++seq; e.page = location.href; e.started = Date.now();
      net.push(e); if (net.length > MAXN) net.shift();
      return e;
    };
    const finish = (e, status, statusText, resHeaders, type, body, size) => {
      e.status = status; e.statusText = statusText || ''; e.resHeaders = resHeaders || {};
      e.type = (type || '').split(';')[0]; e.body = body || ''; e.size = size;
      e.time = Date.now() - e.started;
    };

    const of = window.fetch;
    if (of) window.fetch = function (input, init) {
      let e;
      try {
        const req = (typeof Request !== 'undefined' && input instanceof Request) ? input : null;
        const method = ((init && init.method) || (req && req.method) || 'GET').toUpperCase();
        const h = Object.assign({}, hdrs(req && req.headers), hdrs(init && init.headers));
        e = add({ kind: 'fetch', method, url: abs(req ? req.url : input), reqHeaders: h, reqBody: bodyStr(init && init.body) });
      } catch (x) {}
      return of.apply(this, arguments).then(r => {
        try {
          if (e) {
            const t = r.headers.get('content-type') || '', len = r.headers.get('content-length');
            finish(e, r.status, r.statusText, hdrs(r.headers), t, '', len ? +len : undefined);
            if (textish(t)) r.clone().text().then(b => { e.body = b.slice(0, BODY); e.size = e.size || b.length; e.time = Date.now() - e.started; }).catch(() => {});
          }
        } catch (x) {}
        return r;
      }, err => { try { if (e) { e.error = String(err && err.message || err); e.time = Date.now() - e.started; } } catch (x) {} throw err; });
    };

    const X = XMLHttpRequest.prototype, xo = X.open, xs = X.send, xh = X.setRequestHeader;
    X.open = function (m, u) { this.__lf = { method: String(m || 'GET').toUpperCase(), url: abs(u), reqHeaders: {} }; return xo.apply(this, arguments); };
    X.setRequestHeader = function (k, v) { try { if (this.__lf) this.__lf.reqHeaders[String(k).toLowerCase()] = String(v); } catch (x) {} return xh.apply(this, arguments); };
    X.send = function (b) {
      try {
        if (this.__lf) {
          const e = add(Object.assign({ kind: 'xhr', reqBody: bodyStr(b) }, this.__lf));
          this.addEventListener('loadend', () => {
            try {
              const raw = this.getAllResponseHeaders() || '', rh = {};
              raw.trim().split(/[\r\n]+/).forEach(l => { const i = l.indexOf(':'); if (i > 0) rh[l.slice(0, i).trim().toLowerCase()] = l.slice(i + 1).trim(); });
              const t = this.getResponseHeader('content-type') || '';
              const txt = (this.responseType === '' || this.responseType === 'text') ? this.responseText
                : (this.responseType === 'json' ? JSON.stringify(this.response) : '');
              finish(e, this.status, this.statusText, rh, t, String(txt || '').slice(0, BODY), txt ? txt.length : undefined);
              if (this.status === 0) e.error = 'failed or blocked (status 0)';
            } catch (x) {}
          });
        }
      } catch (x) {}
      return xs.apply(this, arguments);
    };

    if (navigator.sendBeacon) {
      const sb = navigator.sendBeacon.bind(navigator);
      navigator.sendBeacon = function (u, d) {
        const ok = sb(u, d);
        try { const e = add({ kind: 'beacon', method: 'POST', url: abs(u), reqHeaders: {}, reqBody: bodyStr(d) }); finish(e, ok ? 'queued' : 'refused', '', {}, '', '', 0); } catch (x) {}
        return ok;
      };
    }

    const OWS = window.WebSocket;
    if (OWS) {
      const W = function (url, protocols) {
        const s = protocols === undefined ? new OWS(url) : new OWS(url, protocols);
        try {
          const rec = { id: sockets.length + 1, url: abs(s.url || url), state: 'connecting', opened: Date.now(), sent: 0, received: 0, msgs: [] };
          sockets.push(rec); if (sockets.length > 20) sockets.shift();
          const keep = (dir, d) => {
            let txt = typeof d === 'string' ? d : (d && d.byteLength !== undefined ? '[binary, ' + d.byteLength + ' bytes]' : (d && d.size !== undefined ? '[blob, ' + d.size + ' bytes]' : String(d)));
            rec.msgs.push({ dir, t: Date.now(), data: txt.slice(0, 2000) }); if (rec.msgs.length > WSMSG) rec.msgs.shift();
          };
          s.addEventListener('open', () => { rec.state = 'open'; rec.protocol = s.protocol; });
          s.addEventListener('close', ev => { rec.state = 'closed (' + ev.code + (ev.reason ? ' ' + ev.reason : '') + ')'; });
          s.addEventListener('error', () => { rec.state = 'error'; });
          s.addEventListener('message', ev => { rec.received++; keep('in', ev.data); });
          const send = s.send;
          s.send = function (d) { try { rec.sent++; keep('out', d); } catch (x) {} return send.apply(this, arguments); };
        } catch (x) {}
        return s;
      };
      W.prototype = OWS.prototype;
      ['CONNECTING', 'OPEN', 'CLOSING', 'CLOSED'].forEach(k => { try { Object.defineProperty(W, k, { value: OWS[k] }); } catch (x) {} });
      window.WebSocket = W;
    }
  } catch (e) {}

  // Dropdowns, date, time and colour fields: LocalForge shows its own picker (the phone's built-in one
  // often doesn't open inside an app's browser). The AI's own clicks set window.__lfAi and are left alone.
  const PICK = ['date', 'time', 'datetime-local', 'month', 'week', 'color'];
  const target = t => {
    if (!t || !t.closest) return null;
    const inp = t.closest('input');
    if (inp && PICK.includes((inp.type || '').toLowerCase()) && !inp.disabled && !inp.readOnly) return inp;
    const s = t.closest('select');
    if (s && !s.disabled && !s.multiple && !(s.size > 1)) return s;
    return null;
  };
  let lastPick = 0, startX = 0, startY = 0;
  const open = (el) => {
    if (window.__lfAi || !window.LFB) return false;
    if (Date.now() - lastPick < 700) return true;
    lastPick = Date.now();
    document.querySelectorAll('[data-lf-pick]').forEach(e => e.removeAttribute('data-lf-pick'));
    el.setAttribute('data-lf-pick', '1');
    let info;
    if (el.tagName === 'SELECT') {
      info = { kind: 'select', selected: el.selectedIndex, label: (el.getAttribute('aria-label') || el.name || '').slice(0, 60),
        options: [...el.options].map(o => ({ text: (o.label || o.text).trim().slice(0, 120), disabled: o.disabled,
          group: o.parentElement.tagName === 'OPTGROUP' ? o.parentElement.label : '' })) };
    } else {
      info = { kind: 'input', type: el.type.toLowerCase(), value: el.value, min: el.min || '', max: el.max || '' };
    }
    try { LFB.pick(JSON.stringify(info)); } catch (e) { return false; }
    return true;
  };
  document.addEventListener('touchstart', e => { const t = e.touches[0]; if (t) { startX = t.clientX; startY = t.clientY; } }, { capture: true, passive: true });
  document.addEventListener('touchend', e => {
    const el = target(e.target); if (!el) return;
    const t = e.changedTouches[0];
    if (t && (Math.abs(t.clientX - startX) > 12 || Math.abs(t.clientY - startY) > 12)) return;   // a scroll, not a tap
    if (open(el)) e.preventDefault();
  }, { capture: true, passive: false });
  document.addEventListener('mousedown', e => { const el = target(e.target); if (el && open(el)) e.preventDefault(); }, true);
  document.addEventListener('click', e => {
    const el = target(e.target);
    if (el && !window.__lfAi && Date.now() - lastPick < 900) e.preventDefault();
  }, true);
})();
