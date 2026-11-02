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
