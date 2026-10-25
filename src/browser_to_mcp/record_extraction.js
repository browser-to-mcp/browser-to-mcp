// Internal fixed extractor. Workflows supply CSS and declared fields, never page code.
({area, rows, fields, empty, limit, cellLimit, byteLimit, readyOnly, paging}) => {
  const visible = el => !!el.getClientRects().length &&
    getComputedStyle(el).visibility !== 'hidden';
  const failure = (error, row, field) => ({error, row, field});
  try {
    const roots = document.querySelectorAll(area);
    if (roots.length !== 1) return readyOnly ? roots.length > 1 : failure('results_area');
    const root = roots[0];
    if (!visible(root)) return readyOnly ? false : failure('hidden_area');
    const foundRows = rows === ':scope' ? [root] : root.querySelectorAll(rows);
    const markers = [];
    if (empty) for (const marker of root.querySelectorAll(empty)) {
      if (visible(marker)) markers.push(marker);
      if (markers.length > 1) break;
    }
    let navigation;
    if (paging) {
      const next = document.querySelectorAll(paging.next_selector);
      const end = [];
      for (const marker of document.querySelectorAll(paging.end_selector)) {
        if (visible(marker)) end.push(marker);
        if (end.length > 1) break;
      }
      if (readyOnly && !next.length && !end.length) return false;
      if (!readyOnly) {
        if (next.length > 1 || end.length > 1 || (next.length && end.length))
          return failure('ambiguous_paging');
        if (end.length === 1) navigation = {end: true};
        else {
          const link = next[0];
          if (!link || link.tagName !== 'A' || !visible(link) ||
              !link.getAttribute('href') || link.hasAttribute('download') ||
              link.getAttribute('aria-disabled') === 'true' || link.hasAttribute('disabled') ||
              (link.target && link.target !== '_self')) return failure('invalid_next_link');
          const target = new URL(link.getAttribute('href'), document.baseURI);
          if (target.href.length > 4096) return failure('next_link_limit');
          navigation = {end: false, url: target.href};
        }
      }
    }
    if (readyOnly) return foundRows.length > 0 || markers.length > 0;
    if (!foundRows.length)
      return markers.length === 1 ? {records: [], navigation} : failure('unconfirmed_empty');
    if (markers.length) return failure('mixed_empty_state');
    if (foundRows.length > limit) return failure('row_limit');
    const matches = [...foundRows];
    const selected = new Set(matches);
    for (const row of matches) {
      for (let parent = row.parentElement; parent && parent !== root; parent = parent.parentElement)
        if (selected.has(parent)) return failure('overlapping_rows');
    }
    let bytes = 0;
    const records = [];
    for (let i = 0; i < matches.length; i++) {
      const row = matches[i];
      if (!visible(row)) return failure('hidden_row', i);
      const record = {};
      for (const [name, spec] of Object.entries(fields)) {
        const cells = spec.selector === ':scope' ? [row] : row.querySelectorAll(spec.selector);
        if (cells.length > 1) return failure('ambiguous_cell', i, name);
        if (!cells.length) { record[name] = null; continue; }
        const cell = cells[0];
        if (!row.contains(cell) || !visible(cell) ||
            cell.closest('input,textarea,select,[contenteditable],iframe'))
          return failure('unsupported_cell', i, name);
        let value = spec.source === 'text' ? cell.innerText : cell.getAttribute(spec.source);
        if (value === null || value === undefined || value === '') { record[name] = null; continue; }
        if (value.length > cellLimit * 2 || [...value].length > cellLimit) return failure('cell_limit', i, name);
        if (spec.source !== 'href' && spec.normalize_whitespace) value = value.replace(/\s+/g, ' ').trim();
        if (spec.source === 'href') {
          const url = new URL(value, document.baseURI);
          url.hash = '';
          value = url.href;
        }
        bytes += new TextEncoder().encode(value).length;
        if (bytes > byteLimit) return failure('byte_limit');
        record[name] = value;
      }
      records.push(record);
    }
    return {records, navigation};
  } catch (_) { return readyOnly ? true : failure('invalid_selector_or_link'); }
}
