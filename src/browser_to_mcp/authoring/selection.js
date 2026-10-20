// Selection clicks do not activate links, buttons, forms, or recorder handlers.
(() => {
  window.__btmSelection = null;
  window.__btmSelect = true;
  window.addEventListener('click', event => {
    if (!window.__btmSelect) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    const el = event.composedPath()[0];
    if (!(el instanceof Element) || el.getRootNode() !== document ||
        el.closest('input,textarea,select,[contenteditable],iframe')) {
      window.__btmSelection = {error: 'Choose ordinary visible page text, outside a form.'};
      return;
    }
    const candidates = [];
    for (const attr of ['data-testid', 'data-test', 'data-qa', 'id', 'aria-label']) {
      const value = el.getAttribute(attr);
      if (value) candidates.push({selector: `[${attr}=${JSON.stringify(value)}]`, strategy: attr});
    }
    // Tag and class names do not depend on the sample output text or element position.
    const structural = [];
    for (let node = el, depth = 0, path = ''; node && depth < 5; node = node.parentElement, depth++) {
      const part = CSS.escape(node.localName) + [...node.classList].map(c => '.' + CSS.escape(c)).join('');
      path = part + (path ? ' > ' + path : '');
      const item = {selector: path, strategy: 'css'};
      // A surrounding section is more reusable than a page-wide h1 or price class.
      if (depth > 0 && depth <= 2 && node.classList.length &&
          !['body', 'html'].includes(node.localName)) structural.unshift(item);
      else structural.push(item);
    }
    candidates.push(...structural);
    const target = candidates.find(item => {
      try {
        const matches = document.querySelectorAll(item.selector);
        return item.selector.length <= 512 && matches.length === 1 && matches[0] === el;
      } catch { return false; }
    });
    window.__btmSelection = target ? {target} : {
      error: 'This text has no unique target. Select a different element or ask the site to add a stable ID.'
    };
  }, true);
})();
