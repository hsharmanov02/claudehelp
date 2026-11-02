(() => {
  const words = /^(reject all|reject all cookies|reject|decline|decline all|refuse all|necessary only|only necessary|use necessary cookies only|essential only|only essential cookies|continue without accepting)$/i;
  const roots = [document, ...[...document.querySelectorAll('iframe')].map(f => { try { return f.contentDocument; } catch (e) { return null; } }).filter(Boolean)];
  for (const d of roots) {
    for (const b of d.querySelectorAll('button, [role=button], a, input[type=submit], input[type=button]')) {
      const t = (b.innerText || b.value || '').trim().replace(/\s+/g, ' ');
      if (t && t.length < 45 && words.test(t)) { b.click(); return t; }
    }
  }
  return '';
})()
