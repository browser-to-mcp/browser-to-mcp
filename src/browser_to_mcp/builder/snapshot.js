// Fixed application code. Never execute code supplied by a page or model.
() => {
  const nodes = [];
  let clipped = false;
  const excluded = 'script,style,noscript,template,input,textarea,select,option,[contenteditable],iframe';
  const walk = (element, parent, nth, depth) => {
    if (element.matches(excluded) || !element.checkVisibility()) return;
    if (nodes.length >= 600 || depth > 18) { clipped = true; return; }
    const text = [...element.childNodes].filter(n => n.nodeType === Node.TEXT_NODE)
      .map(n => n.textContent).join(' ').replace(/\s+/g, ' ').trim();
    const attrs = {};
    for (const key of ['id', 'class', 'role', 'title', 'href', 'aria-label']) {
      const value = element.getAttribute(key);
      if (value) attrs[key] = value.slice(0, 512);
    }
    const id = nodes.length;
    nodes.push({id, parent, nth, tag: element.tagName.toLowerCase(), text: text.slice(0, 512), attrs});
    const counts = {};
    for (const child of element.children) {
      if (clipped) break;
      const tag = child.tagName.toLowerCase();
      counts[tag] = (counts[tag] || 0) + 1;
      walk(child, id, counts[tag], depth + 1);
    }
  };
  if (document.body) walk(document.body, null, 1, 0);
  return {nodes, clipped, has_password: !!document.querySelector('input[type="password"]')};
}
