// Scenarios view: one card per red-team scenario.

import { h, fmtNumber, kvTable, entryState, staleNotice, emptyState, rerender } from './ui.js';

const FIELDS = [
  ['wave_height_m', 'Wave height', 'm'],
  ['wave_period_s', 'Wave period', 's'],
  ['depth_m', 'Water depth', 'm'],
  ['water_density', 'Water density', 'kg/m³'],
  ['water_temp_c', 'Water temperature', '°C'],
];

function card(s) {
  const rows = FIELDS.filter(([key]) => s[key] !== null && s[key] !== undefined).flatMap(([key, label, unit]) => [
    h('dt', null, label),
    h('dd', null, fmtNumber(s[key]), h('span', { class: 'unit' }, unit)),
  ]);
  const extra = s.extra && Object.keys(s.extra).length ? h('div', { class: 'extra' }, kvTable(s.extra)) : null;
  return h(
    'article',
    { class: 'card' },
    h('h2', null, s.name),
    h('dl', null, rows),
    extra,
    s.rationale ? h('p', { class: 'why' }, s.rationale) : null,
    h(
      'div',
      { class: 'targets' },
      h('span', { class: 'lbl' }, 'Targets'),
      h('span', { class: 'chips' }, (s.targets || []).map((id) => h('a', { class: 'chip', href: `#/requirements/${encodeURIComponent(id)}` }, id))),
    ),
  );
}

export function mount(host, ctx) {
  ctx.ensure('scenarios');

  function paint() {
    rerender(host, () => {
      const wrapper = h('div', null, h('header', { class: 'page-head' }, h('h1', null, 'Scenarios'), h('p', { class: 'lede' }, 'Environmental conditions the design is stressed against, and the requirements each one targets.')));
      const entry = ctx.get('scenarios');
      const state = entryState(entry, () => ctx.reload('scenarios'));
      if (state) {
        wrapper.append(state);
        return wrapper;
      }
      const list = entry.data.scenarios || [];
      if (entry.error) wrapper.append(staleNotice(entry.error));
      wrapper.append(list.length ? h('div', { class: 'cards' }, list.map(card)) : emptyState('No scenarios', 'The red-team stage has not proposed any scenarios yet.'));
      return wrapper;
    });
  }

  ctx.watch(['scenarios'], paint);
  paint();
  return {};
}
