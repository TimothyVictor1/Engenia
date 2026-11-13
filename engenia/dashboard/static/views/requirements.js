// Requirements view: one table with a result filter. Everything shown is a field from the API.

import {
  h, pill, statusLabel, fmtNumber, fmtLimit, withUnit, entryState, staleNotice, emptyState, rerender,
} from './ui.js';

const FILTERS = [
  ['all', 'All'],
  ['pass', 'Pass'],
  ['fail', 'Fail'],
  ['error', 'Error'],
  ['unverified', 'Unverified'],
];

function measuredCell(req) {
  const v = req.verdict;
  if (!v || v.measured === null || v.measured === undefined) return h('span', { class: 'muted' }, '—');
  return withUnit(fmtNumber(v.measured), req.unit);
}

function limitCell(req) {
  const text = fmtLimit(req.limit);
  return text === '—' ? h('span', { class: 'muted' }, '—') : withUnit(text, req.unit);
}

function detailRow(req) {
  const v = req.verdict;
  const message = v && v.message
    ? h('p', { class: 'msg' }, v.message)
    : h('p', { class: 'msg none' }, v ? 'No message was recorded with this verdict.' : 'No verdict has been recorded for this requirement.');
  return h(
    'tr',
    { class: 'detail' },
    h(
      'td',
      { colspan: 6 },
      h(
        'div',
        { class: 'detail-grid' },
        h('div', null, h('p', { class: 'muted' }, 'Verdict message'), message),
        h(
          'dl',
          null,
          h('dt', null, 'check'), h('dd', null, v ? v.check : '—'),
          h('dt', null, 'stage'), h('dd', null, v ? v.stage : '—'),
          h('dt', null, 'method'), h('dd', null, req.method || '—'),
          h('dt', null, 'affects'),
          h('dd', null, req.affects && req.affects.length ? h('span', { class: 'chips' }, req.affects.map((a) => h('span', { class: 'chip' }, a))) : '—'),
        ),
      ),
    ),
  );
}

export function mount(host, ctx) {
  let filter = 'all';
  let openId = ctx.param || null;
  let scrollTo = openId;
  ctx.ensure('requirements');

  const setOpen = (id) => {
    openId = openId === id ? null : id;
    history.replaceState(null, '', openId ? `#/requirements/${encodeURIComponent(openId)}` : '#/requirements');
    paint();
  };

  function paint() {
    rerender(host, () => {
      const wrapper = h('div', null, h('header', { class: 'page-head' }, h('h1', null, 'Requirements'), h('p', { class: 'lede' }, 'Each requirement with the latest verdict the gate produced. Select a row for its message.')));
      const entry = ctx.get('requirements');
      const state = entryState(entry, () => ctx.reload('requirements'));
      if (state) {
        wrapper.append(state);
        return wrapper;
      }
      const reqs = entry.data.requirements || [];
      if (entry.error) wrapper.append(staleNotice(entry.error));
      if (!reqs.length) {
        wrapper.append(emptyState('No requirements', 'This project has no approved requirements yet.'));
        return wrapper;
      }

      const counts = { all: reqs.length };
      reqs.forEach((r) => { counts[r.status] = (counts[r.status] || 0) + 1; });

      wrapper.append(
        h(
          'div',
          { class: 'stats', role: 'group', 'aria-label': 'Filter by result' },
          FILTERS.map(([key, label]) =>
            h(
              'button',
              {
                class: 'stat-btn', type: 'button', 'data-filter': key, 'data-status': key,
                'aria-pressed': String(filter === key), 'data-focus': `filter:${key}`,
                onclick: () => { filter = key; paint(); },
              },
              h('span', { class: 'n' }, String(counts[key] || 0)),
              h('span', { class: 'l' }, h('span', { class: 'dot' }), label),
            ),
          ),
        ),
      );

      const shown = filter === 'all' ? reqs : reqs.filter((r) => r.status === filter);
      const rows = [];
      shown.forEach((req) => {
        const isOpen = openId === req.id;
        rows.push(
          h(
            'tr',
            { class: isOpen ? 'row open' : 'row', 'data-id': req.id, onclick: (ev) => { if (!ev.target.closest('button')) setOpen(req.id); } },
            h('td', null, h('button', { class: 'id-btn', type: 'button', 'aria-expanded': String(isOpen), 'data-focus': `row:${req.id}`, onclick: () => setOpen(req.id) }, req.id)),
            h('td', null, req.statement),
            h('td', { class: 'right nowrap' }, measuredCell(req)),
            h('td', { class: 'nowrap' }, limitCell(req)),
            h('td', null, pill(req.status)),
            h('td', { class: 'mono nowrap' }, req.verdict && req.verdict.evidence_level ? req.verdict.evidence_level : h('span', { class: 'muted' }, '—')),
          ),
        );
        if (isOpen) rows.push(detailRow(req));
      });
      if (!shown.length) rows.push(h('tr', { class: 'empty-row' }, h('td', { colspan: 6 }, `No requirements with result “${statusLabel(filter)}”.`)));

      wrapper.append(
        h(
          'div',
          { class: 'table-wrap' },
          h(
            'table',
            { class: 'reqs' },
            h('caption', { class: 'sr-only' }, 'Requirements and their verdicts'),
            h('thead', null, h('tr', null,
              h('th', { scope: 'col' }, 'ID'),
              h('th', { scope: 'col' }, 'Requirement'),
              h('th', { scope: 'col', class: 'right' }, 'Measured'),
              h('th', { scope: 'col' }, 'Limit'),
              h('th', { scope: 'col' }, 'Result'),
              h('th', { scope: 'col' }, 'Evidence'),
            )),
            h('tbody', null, rows),
          ),
        ),
      );
      return wrapper;
    });

    if (scrollTo) {
      const row = host.querySelector(`tr[data-id="${CSS.escape(scrollTo)}"]`);
      if (row) row.scrollIntoView({ block: 'center' });
      scrollTo = null;
    }
  }

  ctx.watch(['requirements'], paint);
  paint();

  return {
    setParam(param) {
      openId = param || null;
      scrollTo = openId;
      if (openId) filter = 'all';
      paint();
    },
  };
}
