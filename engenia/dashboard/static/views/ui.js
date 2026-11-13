// Small DOM and formatting helpers shared by the views. No innerHTML anywhere: API text is
// always inserted as text nodes.

const SVG_NS = 'http://www.w3.org/2000/svg';

function apply(node, attrs) {
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === 'class') node.setAttribute('class', value);
    else if (key.startsWith('on') && typeof value === 'function') node.addEventListener(key.slice(2), value);
    else if (key === 'dataset') Object.assign(node.dataset, value);
    else node.setAttribute(key, value === true ? '' : String(value));
  }
}

function append(node, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
}

/** h('div', {class: 'x'}, 'text', childNode) */
export function h(tag, attrs, ...children) {
  const node = document.createElement(tag);
  apply(node, attrs);
  append(node, children);
  return node;
}

export function svg(tag, attrs, ...children) {
  const node = document.createElementNS(SVG_NS, tag);
  apply(node, attrs);
  append(node, children);
  return node;
}

export const clear = (node) => node.replaceChildren();

// ---------------------------------------------------------------- status

/** "not_run" -> "not run". The status string itself always comes from the API. */
export const statusLabel = (status) => String(status ?? 'unknown').replace(/_/g, ' ');

export function pill(status, label) {
  return h('span', { class: 'pill', 'data-status': status ?? 'unknown' }, label ?? statusLabel(status));
}

// ---------------------------------------------------------------- formatting

export function fmtNumber(n) {
  if (typeof n !== 'number' || !Number.isFinite(n)) return String(n);
  return Number.isInteger(n) ? String(n) : String(Number(n.toPrecision(6)));
}

/** Value for a key/value table: numbers, strings, booleans, arrays and nested objects. */
export function fmtValue(v) {
  if (v === null || v === undefined) return '—';
  if (typeof v === 'number') return fmtNumber(v);
  if (typeof v === 'boolean') return v ? 'true' : 'false';
  if (typeof v === 'string') return v;
  if (Array.isArray(v)) return `[${v.map(fmtValue).join(', ')}]`;
  return JSON.stringify(v);
}

/** Flatten nested plain objects to dotted keys; arrays and scalars stay as leaves. */
export function flatten(obj, prefix = '', out = []) {
  for (const [key, value] of Object.entries(obj || {})) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (value && typeof value === 'object' && !Array.isArray(value) && Object.keys(value).length) {
      flatten(value, path, out);
    } else {
      out.push([path, value]);
    }
  }
  return out;
}

export function kvTable(obj, emptyText = 'Nothing reported.') {
  const rows = flatten(obj);
  if (!rows.length) return h('p', { class: 'muted' }, emptyText);
  return h(
    'table',
    { class: 'kv' },
    h('tbody', null, rows.map(([k, v]) => h('tr', null, h('th', { scope: 'row' }, k), h('td', null, fmtValue(v))))),
  );
}

/** Limit object -> "≥ 0.2 g", "≤ 400 g", "= 1 fits", or "—". Display only. */
export function fmtLimit(limit, unit) {
  if (!limit) return '—';
  const parts = [];
  if (limit.min !== null && limit.min !== undefined) parts.push(`≥ ${fmtNumber(limit.min)}`);
  if (limit.max !== null && limit.max !== undefined) parts.push(`≤ ${fmtNumber(limit.max)}`);
  if (limit.equals !== null && limit.equals !== undefined) parts.push(`= ${fmtNumber(limit.equals)}`);
  return parts.length ? parts.join(', ') : '—';
}

/** Number with a trailing muted unit, as a node. */
export function withUnit(text, unit) {
  return h('span', { class: 'num' }, text, unit ? h('span', { class: 'unit' }, unit) : null);
}

export function fmtTime(date) {
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
}

// ---------------------------------------------------------------- states

export const loadingState = (text = 'Loading…') =>
  h('div', { class: 'state loading', role: 'status', 'aria-busy': 'true' }, text);

export function errorState(error, onRetry) {
  return h(
    'div',
    { class: 'state error', role: 'alert' },
    h('strong', null, 'Could not load this view'),
    h('span', null, error?.message || 'Unknown error.'),
    onRetry ? h('div', null, h('button', { class: 'btn', type: 'button', onclick: onRetry }, 'Try again')) : null,
  );
}

export const emptyState = (title, text) =>
  h('div', { class: 'state' }, h('strong', null, title), text ? h('span', null, text) : null);

/** Shown above stale data when a refresh failed. */
export const staleNotice = (error) =>
  h('div', { class: 'notice', role: 'status' }, `Could not refresh: ${error?.message || 'unknown error'} Showing the last data received.`);

/**
 * Standard handling of a store entry. Returns a node for loading/error/empty, or null when
 * the entry has data and the caller should render it.
 */
export function entryState(entry, retry) {
  if (!entry || entry.status === 'idle' || (entry.status === 'loading' && !entry.data)) return loadingState();
  if (entry.status === 'error' && !entry.data) return errorState(entry.error, retry);
  return null;
}

/** Re-render while keeping keyboard focus on the element with the same data-focus id. */
export function rerender(host, build) {
  const active = document.activeElement;
  const focusId = active && host.contains(active) ? active.getAttribute('data-focus') : null;
  const scroller = document.scrollingElement;
  const top = scroller ? scroller.scrollTop : 0;
  host.replaceChildren(build());
  if (scroller) scroller.scrollTop = top;
  if (focusId) {
    const next = host.querySelector(`[data-focus="${CSS.escape(focusId)}"]`);
    if (next) next.focus({ preventScroll: true });
  }
}
