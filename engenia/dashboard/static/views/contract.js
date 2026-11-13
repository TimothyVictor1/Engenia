// Contract view: budgets from the contract beside the facts each stage reported, and change
// requests. The page lines the two columns up; it does not judge whether a budget is met.

import {
  h, pill, kvTable, flatten, fmtValue, entryState, staleNotice, emptyState, rerender,
} from './ui.js';

const SOURCE_TEXT = {
  project: 'The contract defined by this project.',
  default: 'The default contract. The project has not defined its own.',
  none: 'No contract exists for this project yet.',
};

const UNIT_SUFFIXES = [
  ['_mwh_per_day', 'mWh/day'], ['_mah', 'mAh'], ['_mm', 'mm'], ['_kb', 'kB'], ['_mw', 'mW'], ['_ms', 'ms'], ['_g', 'g'],
];
const NOISE = new Set(['dry', 'used', 'total', 'outer', 'budget', 'actual', 'measured']);

/** "mass_g_max" -> { base: "mass", unit: "g" } */
function splitKey(key) {
  const bare = key.replace(/_(max|min)$/, '');
  for (const [suffix, unit] of UNIT_SUFFIXES) {
    if (bare.endsWith(suffix)) return { base: bare.slice(0, -suffix.length), unit };
  }
  return { base: bare, unit: null };
}

const tokens = (base) => base.split(/[_.]/).filter((t) => t && !NOISE.has(t));

/** Find the reported fact that corresponds to a budget key: same words, fewest extra words. */
function findFact(facts, budgetKey) {
  const want = tokens(splitKey(budgetKey).base);
  if (!want.length) return null;
  let best = null;
  for (const [key, value] of flatten(facts)) {
    const have = tokens(splitKey(key).base);
    if (!want.every((t) => have.includes(t))) continue;
    const extra = have.length - want.length;
    if (!best || extra < best.extra) best = { key, value, extra };
  }
  return best;
}

function quantity(value, unit) {
  const text = Array.isArray(value) && value.every((x) => typeof x === 'number') ? value.join(' × ') : fmtValue(value);
  return h('span', { class: 'num' }, text, unit ? h('span', { class: 'unit' }, unit) : null);
}

const humanize = (base) => base.replace(/_/g, ' ');

function budgetRows(contract, facts) {
  const rows = [];
  const budgets = contract.budgets || {};
  Object.entries(budgets).forEach(([stage, items]) => {
    rows.push(h('tr', { class: 'group' }, h('th', { colspan: 3, scope: 'colgroup' }, stage)));
    Object.entries(items || {}).forEach(([key, value]) => {
      const { base, unit } = splitKey(key);
      const found = findFact(facts[stage], key);
      rows.push(
        h(
          'tr',
          null,
          h('th', { scope: 'row' }, humanize(base)),
          h('td', null, quantity(value, unit)),
          h('td', null, found ? h('span', null, quantity(found.value, unit), h('span', { class: 'src' }, found.key)) : h('span', { class: 'missing' }, 'not reported')),
        ),
      );
    });
  });
  const totals = contract.totals || {};
  if (Object.keys(totals).length) {
    rows.push(h('tr', { class: 'group' }, h('th', { colspan: 3, scope: 'colgroup' }, 'totals')));
    Object.entries(totals).forEach(([key, value]) => {
      const { base, unit } = splitKey(key);
      const found = findFact(facts.integration, key);
      rows.push(
        h(
          'tr',
          null,
          h('th', { scope: 'row' }, `${humanize(base)} (${/_min$/.test(key) ? 'min' : 'max'})`),
          h('td', null, quantity(value, unit)),
          h('td', null, found ? h('span', null, quantity(found.value, unit), h('span', { class: 'src' }, found.key)) : h('span', { class: 'missing' }, 'not reported')),
        ),
      );
    });
  }
  return rows;
}

function renderChangeRequests(list) {
  if (!list.length) return emptyState('No change requests', 'No stage has asked for a budget change.');
  return h(
    'ul',
    { class: 'cr-list' },
    list.map((cr) =>
      h(
        'li',
        { class: 'cr' },
        h('span', { class: 'id' }, cr.id),
        h(
          'div',
          null,
          h('div', { class: 'what' }, `${cr.from_stage} → ${cr.path}`),
          h('div', { class: 'what muted' }, `requested ${fmtValue(cr.requested)}`),
        ),
        pill(cr.status),
        cr.reason ? h('div', { class: 'why' }, cr.reason) : null,
      ),
    ),
  );
}

export function mount(host, ctx) {
  ctx.ensure('contract');

  function paint() {
    rerender(host, () => {
      const wrapper = h('div', null, h('header', { class: 'page-head' }, h('h1', null, 'Contract'), h('p', { class: 'lede' }, 'Budgets every stage must respect, next to what the stages reported.')));
      const entry = ctx.get('contract');
      const state = entryState(entry, () => ctx.reload('contract'));
      if (state) {
        wrapper.append(state);
        return wrapper;
      }
      const { source, contract, facts = {}, change_requests: changeRequests = [] } = entry.data;
      if (entry.error) wrapper.append(staleNotice(entry.error));

      wrapper.append(
        h(
          'p',
          { class: 'source-line' },
          h('span', null, 'Contract source'),
          h('span', { class: 'source-tag' }, source),
          h('span', null, SOURCE_TEXT[source] || ''),
          contract && contract.version !== undefined ? h('span', { class: 'muted' }, `· version ${contract.version}`) : null,
        ),
      );

      if (!contract) {
        wrapper.append(emptyState('No contract', 'There are no budgets to compare yet.'));
      } else {
        wrapper.append(
          h('h2', { class: 'section' }, 'Budgets and reported facts'),
          h(
            'div',
            { class: 'table-wrap' },
            h(
              'table',
              { class: 'compare' },
              h('thead', null, h('tr', null, h('th', { scope: 'col' }, 'Item'), h('th', { scope: 'col', class: 'col' }, 'Budget (contract)'), h('th', { scope: 'col', class: 'col' }, 'Reported (stage facts)'))),
              h('tbody', null, budgetRows(contract, facts)),
            ),
          ),
        );
        const interfaces = contract.interfaces || {};
        if (Object.keys(interfaces).length) {
          wrapper.append(
            h('h2', { class: 'section' }, 'Interfaces'),
            h('div', { class: 'two-col' }, Object.entries(interfaces).map(([name, values]) => h('div', { class: 'box' }, h('h3', { class: 'mono' }, name), kvTable(values)))),
          );
        }
      }

      wrapper.append(h('h2', { class: 'section' }, 'Change requests'), renderChangeRequests(changeRequests));
      return wrapper;
    });
  }

  ctx.watch(['contract'], paint);
  paint();
  return {};
}
