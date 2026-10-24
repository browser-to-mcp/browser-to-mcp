// Runs before site scripts in every document. Only small, explicit events leave the page.
(() => {
  if (window.__btmRecorderInstalled) return;
  window.__btmRecorderInstalled = true;
  let stopped = false;
  const pending = new Set();
  const send = payload => {
    if (!stopped) {
      const delivered = window.__btmCapture(payload).catch(() => {});
      pending.add(delivered);
      void delivered.finally(() => pending.delete(delivered));
    }
  };
  const diagnostic = code => send({kind: 'diagnostic', code});
  if (window.top !== window) {
    diagnostic('frame');
    return;
  }
  let keyboardActivation = false;
  const sensitive = el => el instanceof Element && (
    el.matches('input[type=password]') ||
    /password|token|secret|username|authorization|session|credit.?card|cc-|one-time-code/i.test(
      [el.getAttribute('name'), el.id, el.getAttribute('autocomplete')].join(' '))
  );
  const guard = el => {
    if (sensitive(el)) {
      diagnostic('sensitive_field');
      stopped = true;
      return false;
    }
    return true;
  };
  const quote = value => JSON.stringify(value);
  const normalized = text => text.replace(/\s+/g, ' ').trim().toLowerCase();
  const safeTargetText = text => text.length <= 160 && !/[\x00-\x1f]|\{\{|\}\}/.test(text);
  const unique = (selector, el) => {
    try {
      const matches = document.querySelectorAll(selector);
      return matches.length === 1 && matches[0] === el;
    } catch { return false; }
  };
  const target = el => {
    if (!(el instanceof Element) || el.getRootNode() !== document) return null;
    for (const attr of ['data-testid', 'data-test', 'data-qa', 'id', 'aria-label']) {
      const value = el.getAttribute(attr);
      if (value && safeTargetText(value)) {
        const selector = `[${attr}=${quote(value)}]`;
        if (unique(selector, el)) return {selector, strategy: attr};
      }
    }
    // Replay accepts Playwright's CSS :has-text pseudo-class; labels stay executable CSS.
    if (el.labels?.length === 1) {
      const label = el.labels[0];
      const text = label.textContent.trim();
      if (text && safeTargetText(text) && label.contains(el)) {
        const selector = `label:has-text(${quote(text)}) ${el.localName}`;
        const labels = [...document.querySelectorAll('label')]
          .filter(item => normalized(item.textContent).includes(normalized(text)));
        if (labels.length === 1 && label.querySelectorAll(el.localName).length === 1)
          return {selector, strategy: 'label'};
      }
    }
    for (const attr of ['name', 'placeholder']) {
      const value = el.getAttribute(attr);
      if (value && safeTargetText(value)) {
        const selector = `${el.localName}[${attr}=${quote(value)}]`;
        if (unique(selector, el)) return {selector, strategy: attr};
      }
    }
    const role = el.getAttribute('role');
    const text = el.textContent.trim();
    if ((el.matches('button,a') || role === 'button') && text && safeTargetText(text)) {
      const base = role === 'button' ? '[role="button"]' : el.localName;
      const matches = [...document.querySelectorAll(base)]
        .filter(item => normalized(item.textContent).includes(normalized(text)));
      if (matches.length === 1)
        return {selector: `${base}:has-text(${quote(text)})`, strategy: 'text'};
    }
    // Refuse unstable positional guesses: review must explain missing targets.
    return null;
  };
  const editable = el => el instanceof Element && el.matches(
    'textarea,input:not([type]),input[type=text],input[type=search],input[type=email],input[type=url],input[type=tel],input[type=number]'
  );
  const capture = (kind, el, extra = {}) => {
    if (!guard(el)) return;
    if (el?.getRootNode() !== document) return diagnostic('shadow_dom');
    const info = target(el);
    if (!info) return diagnostic('ambiguous_target');
    send({kind, target: info, ...extra});
  };
  document.addEventListener('focusin', e => guard(e.target), true);
  document.addEventListener('input', e => {
    if (!guard(e.target)) return;
    if (!editable(e.target)) return diagnostic('unsupported_input');
    capture('fill', e.target, {value: e.target.value});
  }, true);
  document.addEventListener('blur', () => send({kind: 'boundary'}), true);
  document.addEventListener('click', e => {
    const el = e.composedPath()[0];
    if (e.detail === 0 && keyboardActivation) return;
    if (!guard(el)) return;
    if (el instanceof Element && el.closest('input[type=file],a[download]')) {
      e.preventDefault();
      return diagnostic('file_transfer');
    }
    if (e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey)
      return diagnostic('modified_click');
    const actionable = el instanceof Element ? el.closest('button,a,input,textarea,[role=button]') : null;
    if (actionable?.matches('input[type=checkbox],input[type=radio],input[type=range],select'))
      return diagnostic('unsupported_input');
    capture('click', actionable || el);
  }, true);
  document.addEventListener('keydown', e => {
    const el = e.composedPath()[0];
    if (!guard(el) || e.isComposing) return;
    if (el instanceof Element && el.closest('input[type=file],a[download]')) {
      e.preventDefault();
      return diagnostic('file_transfer');
    }
    if (['Shift', 'Control', 'Alt', 'Meta', 'CapsLock'].includes(e.key)) return;
    if (editable(el) && !e.ctrlKey && !e.metaKey && !e.altKey &&
        (e.key.length === 1 || ['Backspace', 'Delete'].includes(e.key))) return;
    if ((e.ctrlKey || e.metaKey) && ['a', 'c', 'v', 'x'].includes(e.key.toLowerCase())) {
      // Selection and clipboard operations are represented by the resulting fill.
      if (editable(el)) return;
    }
    if (!['Enter', 'Tab', 'Escape', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight',
          'Home', 'End', 'PageUp', 'PageDown', ' '].includes(e.key))
      return diagnostic('unsupported_key');
    const modifiers = [e.ctrlKey && 'Control', e.altKey && 'Alt', e.shiftKey && 'Shift',
      e.metaKey && 'Meta'].filter(Boolean);
    const key = [...modifiers, e.key === ' ' ? 'Space' : e.key].join('+');
    keyboardActivation = ['Enter', ' '].includes(e.key);
    capture('press', el, {key});
  }, true);
  document.addEventListener('keyup', () => { setTimeout(() => { keyboardActivation = false; }, 0); }, true);
  for (const event of ['contextmenu', 'dblclick', 'dragstart', 'drop', 'touchstart', 'wheel', 'auxclick'])
    document.addEventListener(event, () => diagnostic('unsupported_gesture'), true);
  document.addEventListener('change', e => {
    if (e.target.matches('select,input[type=file],input[type=checkbox],input[type=radio]'))
      diagnostic('unsupported_input');
  }, true);
  window.__btmPause = async () => { stopped = true; await Promise.all([...pending]); };
})();
