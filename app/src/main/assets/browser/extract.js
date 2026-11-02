(q) => {
  const vis = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const clean = t => (t || '').trim().replace(/\s+/g, ' ');
  const mark = (el, t) => {
    // Up/down shown only by colour, class or icon becomes a visible ▲/▼.
    if (!/^[$£€]?\s?[\d][\d.,]*\s?(%|[kKmMbB])?$/.test(t)) return t;   // only plain numbers, prices and percents
    const html = el.innerHTML.slice(0, 400);
    const leaf = el.querySelector('span,div,p,b') || el;
    const c = (getComputedStyle(leaf).color.match(/\d+/g) || [0, 0, 0]).map(Number);
    if (/(down|neg|loss|decrease|red|danger|fall)/i.test(html + el.className) || (c[0] > 150 && c[1] < 120 && c[2] < 120)) return '▼' + t;
    if (/(\bup\b|caret-up|pos|gain|increase|green|success|rise)/i.test(html + el.className) || (c[1] > 130 && c[0] < 120)) return '▲' + t;
    return t;
  };
  const here = location.href.split('#')[0];
  const link = el => {
    const ok = a => a.href.startsWith('http') && a.href.split('#')[0] !== here;
    const all = [...el.querySelectorAll('a[href]')].filter(ok);
    const a = all.find(a => clean(a.innerText).length > 8);
    if (!a) return '';
    // Other links in the row (e.g. "161 comments") so the model can open the right one.
    const more = []; const seen = new Set([a.href]);
    for (const b of all) {
      const t = clean(b.innerText);
      if (seen.has(b.href) || t.length < 3 || BUTTONISH.test(t) || more.length >= 2) continue;
      if (!/\d|comment|review|discuss|detail/i.test(t)) continue;
      seen.add(b.href); more.push(t.slice(0, 30) + ': ' + b.href.slice(0, 120));
    }
    return ' -> ' + a.href.slice(0, 160) + (more.length ? ' (also ' + more.join('; ') + ')' : ''); };
  const BUTTONISH = /^(buy|sell|select|add|add to (cart|basket|bag)|view|details|more|opens in a new (window|tab)|opens in a new window or tab|watch|compare|share|save)$/i;
  const words = clean(q).toLowerCase().split(' ').filter(Boolean);
  const score = el => words.length ? words.filter(w => el.innerText.toLowerCase().includes(w)).length : 0;
  const parts = [];
  // 1) Tables (header cells can be <th>, also inside rows)
  const tables = [...document.querySelectorAll('table')].filter(t => vis(t) && t.rows.length >= 3)
    .sort((a, b) => score(b) - score(a) || b.rows.length - a.rows.length).slice(0, 3);
  tables.forEach((t, i) => {
    const rows = [...t.rows].filter(vis).slice(0, 60).map(r =>
      [...r.cells].map(c => mark(c, clean(c.innerText))).filter(x => x && !BUTTONISH.test(x)).join(' | ') + link(r)).filter(x => x.replace(/[ |]/g, ''));
    parts.push(`Table ${i + 1} (${t.rows.length} rows${t.rows.length > 60 ? ', first 60 shown' : ''}):\n` + rows.join('\n'));
  });
  // 2) Repeated items (cards, search results, product tiles)
  const groups = [];
  for (const p of document.querySelectorAll('body *')) {
    if (p.closest('table') || !vis(p) || p.children.length < 5) continue;
    const sig = el => el.tagName + '.' + ([...el.classList].filter(c => !/\d/.test(c)).slice(0, 2).join('.') || el.getAttribute('data-component-type') || '');
    const counts = {};
    for (const c of p.children) counts[sig(c)] = (counts[sig(c)] || 0) + 1;
    const [best, n] = Object.entries(counts).sort((a, b) => b[1] - a[1])[0];
    if (n < 5) continue;
    const items = [...p.children].filter(c => sig(c) === best && vis(c) && clean(c.innerText).length > 20);
    if (items.length < 5) continue;
    const avg = items.reduce((s, c) => s + clean(c.innerText).length, 0) / items.length;
    if (avg > 1500) continue;
    groups.push({ items, value: items.length * Math.min(avg, 300) + score(p) * 5000 });
  }
  groups.sort((a, b) => b.value - a.value);
  const used = new Set();
  for (const g of groups.slice(0, 6)) {
    if (parts.length >= 5 || g.items.some(x => used.has(x))) continue;
    g.items.forEach(x => used.add(x));
    const rows = g.items.slice(0, 40).map((it, i) => {
      const texts = []; const w = document.createTreeWalker(it, NodeFilter.SHOW_TEXT); let n;
      while ((n = w.nextNode())) {
        const t = clean(n.textContent);
        if (!t || texts.includes(t) || !n.parentElement || !vis(n.parentElement)) continue;
        if (t.length < 6 && texts.slice(-3).some(x => x.includes(t))) continue;
        if (BUTTONISH.test(t)) continue;   // "£ | 374 | . | 99" pieces of a price already shown
        texts.push(mark(n.parentElement, t));
      }
      return (i + 1) + '. ' + texts.join(' | ').slice(0, 300) + link(it);
    });
    parts.push(`List of ${g.items.length} items${g.items.length > 40 ? ' (first 40 shown)' : ''}:\n` + rows.join('\n'));
  }
  return parts.join('\n\n').slice(0, 9000);
}
