(() => {
  const vis = el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
    return r.width > 2 && r.height > 2 && s.visibility !== 'hidden' && s.display !== 'none' && s.opacity !== '0'; };
  document.querySelectorAll('[data-lf-id]').forEach(e => e.removeAttribute('data-lf-id'));
  const sel = 'a[href],button,input:not([type=hidden]),textarea,select,[role=button],[role=link],[role=tab],[role=menuitem],[role=checkbox],[role=option],[contenteditable=true],[onclick],summary,label[for]';
  const out = []; let n = 0;
  for (const el of document.querySelectorAll(sel)) {
    if (!vis(el) || n >= 150) continue;
    n++; el.setAttribute('data-lf-id', n);
    const tag = el.tagName.toLowerCase();
    let label = (el.getAttribute('aria-label') || el.innerText || el.value || el.placeholder || el.title || el.alt || el.name || '').trim().replace(/\s+/g, ' ').slice(0, 80);
    let kind = tag === 'a' ? 'link' : tag === 'input' ? 'input:' + (el.type || 'text') : tag === 'textarea' ? 'textbox' : tag === 'select' ? 'select' : tag === 'button' ? 'button' : (el.getAttribute('role') || tag);
    let extra = '';
    if (tag === 'input' || tag === 'textarea') { extra = el.type === 'password' ? ' value=***' : (el.value ? ' value="' + el.value.slice(0, 60) + '"' : ''); if (el.placeholder) extra += ' placeholder="' + el.placeholder.slice(0, 40) + '"'; }
    if (tag === 'select') extra = ' options=' + [...el.options].slice(0, 12).map(o => o.text.trim()).join('|');
    if (el.type === 'checkbox' || el.type === 'radio') extra += el.checked ? ' [checked]' : ' [unchecked]';
    if (tag === 'a') { try { extra += ' -> ' + new URL(el.href).href.slice(0, 100); } catch (e) {} }
    if (label.length < 4 || el.type === 'checkbox' || el.type === 'radio') {
      // Short labels like "X" or "on": add the text of the row/item it sits in, so it's clear which one it is.
      const row = el.closest('li,tr,[role=row],[role=listitem],label,.item,.row,div');
      const ctx = row ? row.innerText.trim().replace(/\s+/g, ' ').replace(label, '').trim().slice(0, 50) : '';
      if (ctx) extra += ' (in: "' + ctx + '")';
      if (el.type === 'checkbox' || el.type === 'radio') label = label === 'on' ? '' : label;
    }
    out.push('[' + n + '] ' + kind + ' "' + label + '"' + extra);
  }
  let text = (document.body ? document.body.innerText : '').replace(/\n{3,}/g, '\n\n').trim();
  // Meaning shown only by style: struck-through (done) items and red/down numbers lose it in plain text.
  const struck = [], down = [], up = [];
  for (const el of document.querySelectorAll('body *')) {
    if (struck.length + down.length + up.length > 80) break;
    if (!el.childNodes.length || el.children.length > 3) continue;
    const t = (el.innerText || '').trim().replace(/\s+/g, ' ');
    if (!t || t.length > 80) continue;
    const cs = getComputedStyle(el);
    if (cs.textDecorationLine.includes('line-through')) { struck.push(t); continue; }
    if (/%|[\d.,]+/.test(t) && t.length < 25) {
      const cls = ((el.className && el.className.baseVal !== undefined ? el.className.baseVal : el.className) || '') + ' ' +
                  ((el.querySelector('i,svg,span') || {}).className || '');
      const c = cs.color.match(/\d+/g) || [0, 0, 0];
      const red = +c[0] > 150 && +c[1] < 110 && +c[2] < 110, green = +c[1] > 130 && +c[0] < 110;
      if (/down|neg|loss|decrease|fall|red|danger/i.test(cls) || red || /^[▼↓]/.test(t)) down.push(t);
      else if (/\bup\b|pos|gain|increase|rise|green|success/i.test(cls) || green || /^[▲↑]/.test(t)) up.push(t);
    }
  }
  const cues = [];
  if (struck.length) cues.push('Struck through (e.g. done/completed): ' + struck.slice(0, 30).join(' | '));
  if (down.length) cues.push('Shown as negative/down (red or down arrow): ' + down.slice(0, 40).join(' | '));
  if (up.length) cues.push('Shown as positive/up (green or up arrow): ' + up.slice(0, 40).join(' | '));
  return JSON.stringify({ url: location.href, title: document.title, text, elements: out, cues });
})()
