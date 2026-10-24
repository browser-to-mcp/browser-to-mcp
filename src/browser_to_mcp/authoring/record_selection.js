// Internal row inspector. Only fixed CSS and small metadata leave the page.
({mode, index, source, minimum, nullable}) => {
  const picked = window.__btmPickedElement;
  const visible = el => el?.isConnected && !!el.getClientRects().length;
  const part = node => CSS.escape(node.localName) + [...node.classList]
    .filter(c => !c.startsWith('btm-')).map(c => '.' + CSS.escape(c)).join('');
  const candidates = (node, root) => {
    const values = [];
    for (const attr of ['data-testid', 'data-test', 'data-qa', 'aria-label']) {
      const value = node.getAttribute(attr);
      if (value) values.push(`[${attr}=${JSON.stringify(value)}]`);
    }
    for (const name of [...node.classList].slice(0, 16))
      values.push(CSS.escape(node.localName) + '.' + CSS.escape(name));
    let path = '';
    for (let item = node, depth = 0; item && item !== root && depth < 8;
         item = item.parentElement, depth++) {
      path = part(item) + (path ? ' > ' + path : '');
      values.push(path);
    }
    values.push(CSS.escape(node.localName));
    if (node.id) values.push(`[id=${JSON.stringify(node.id)}]`);
    return values.filter(css => css.length <= 512 && !css.includes('{{') && !css.includes('}}'));
  };
  if (mode === 'areas') {
    window.__btmRecordAreas = [];
    for (let node = picked, depth = 0; visible(node) && depth < 8 &&
         !['body', 'html'].includes(node.localName); node = node.parentElement, depth++) {
      const selector = candidates(node, document).find(css => {
        const found = document.querySelectorAll(css);
        return found.length === 1 && found[0] === node;
      });
      if (selector) window.__btmRecordAreas.push({node, selector, depth});
    }
    return window.__btmRecordAreas.map(({selector, depth}) => ({selector, depth}));
  }
  if (mode === 'area') {
    const selected = window.__btmRecordAreas?.[index];
    if (!selected || !visible(selected.node)) return {error: 'The selected area changed. Select again.'};
    window.__btmRecordArea = selected.node;
    window.__btmRecordRow = null;
    return {selector: selected.selector};
  }
  const area = window.__btmRecordArea;
  if (!visible(area)) return {error: 'The results area changed. Start the output again.'};
  if (mode === 'rows') {
    if (!picked || !area.contains(picked)) return {error: 'Click a row inside the results area.'};
    window.__btmRecordRows = [];
    for (let node = picked, depth = 0; node && area.contains(node) && depth < 8;
         node = node.parentElement, depth++) {
      for (const selector of new Set(node === area ? [':scope'] : [part(node), CSS.escape(node.localName)])) {
        const matches = selector === ':scope' ? [area] : [...area.querySelectorAll(selector)];
        if (matches.includes(node) && matches.length >= minimum && matches.length <= 100 &&
            !window.__btmRecordRows.some(item => item.node === node && item.count === matches.length))
          window.__btmRecordRows.push({node, selector, count: matches.length, depth});
      }
      if (node === area) break;
    }
    return window.__btmRecordRows.map(({selector, count, depth}) => ({selector, count, depth}));
  }
  if (mode === 'row') {
    const selected = window.__btmRecordRows?.[index];
    if (!selected || !visible(selected.node)) return {error: 'The row changed. Start the output again.'};
    window.__btmRecordRow = selected.node;
    window.__btmRecordRowSelector = selected.selector;
    return {selector: selected.selector};
  }
  if (mode === 'field') {
    const row = window.__btmRecordRow;
    if (!visible(row) || !picked || !row.contains(picked))
      return {error: 'Choose a field inside the same example row you selected.'};
    let node = picked;
    if (source === 'href') node = picked.closest('a[href]');
    if (source === 'title') node = picked.closest('[title]');
    if (!node || !row.contains(node) || !visible(node) ||
        node.closest('input,textarea,select,[contenteditable],iframe'))
      return {error: 'Choose visible public text or its product link in the same row.'};
    if (node === row) return {selector: ':scope'};
    const selectorRows = window.__btmRecordRowSelector;
    const allRows = selectorRows === ':scope' ? [area] : [...area.querySelectorAll(selectorRows)];
    const eligible = [];
    for (const selector of candidates(node, row)) {
      const matches = row.querySelectorAll(selector);
      if (matches.length !== 1 || matches[0] !== node) continue;
      let score = 0, valid = true;
      for (const other of allRows) {
        const count = other.querySelectorAll(selector).length;
        if (count > 1 || (!nullable && count !== 1)) { valid = false; break; }
        score += count;
      }
      if (valid) eligible.push({selector, score});
    }
    eligible.sort((a,b) => b.score - a.score);
    const selector = eligible[0]?.selector;
    return selector ? {selector} : {error: 'This field is ambiguous inside the selected row.'};
  }
  return {error: 'Unknown selection mode.'};
}
