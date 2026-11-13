// Pipeline view: the stage graph (SVG, laid out by dependency depth) and a stage side panel.

import {
  h, svg, pill, statusLabel, kvTable, fmtNumber, fmtLimit, entryState, staleNotice, rerender,
  loadingState, errorState,
} from './ui.js';

const BOX_W = 108;
const BOX_H = 52;
const COL_GAP = 30;
const ROW_GAP = 22;
const PAD = 10;

const STATUS_ORDER = ['done', 'running', 'failed', 'blocked', 'not_run', 'stub'];

/**
 * Column = dependency depth (longest chain of needs). A stage with no needs but with
 * consumers is placed one column before its first consumer so its arrow stays short.
 * Rows follow API order, then each stage is pulled towards the average of its neighbours.
 */
export function layout(stages) {
  const byName = new Map(stages.map((s) => [s.name, s]));
  const needsOf = (s) => (s.needs || []).filter((n) => byName.has(n));
  const consumers = new Map(stages.map((s) => [s.name, []]));
  stages.forEach((s) => needsOf(s).forEach((n) => consumers.get(n).push(s.name)));

  const depth = new Map();
  const visit = (name, trail) => {
    if (depth.has(name)) return depth.get(name);
    if (trail.has(name)) return 0; // cycle guard: the API should never send one
    trail.add(name);
    const needs = needsOf(byName.get(name));
    const d = needs.length ? 1 + Math.max(...needs.map((n) => visit(n, trail))) : 0;
    trail.delete(name);
    depth.set(name, d);
    return d;
  };
  stages.forEach((s) => visit(s.name, new Set()));
  stages.forEach((s) => {
    const next = consumers.get(s.name);
    if (!needsOf(s).length && next.length) depth.set(s.name, Math.min(...next.map((c) => depth.get(c))) - 1);
  });

  const columnCount = Math.max(0, ...depth.values()) + 1;
  const columns = Array.from({ length: columnCount }, () => []);
  stages.forEach((s) => columns[depth.get(s.name)].push(s.name));

  const top = new Map();
  const mean = (names) => names.reduce((sum, n) => sum + top.get(n), 0) / names.length;

  columns.forEach((names) => {
    const withNeeds = names.filter((n) => needsOf(byName.get(n)).length);
    const sources = names.filter((n) => !needsOf(byName.get(n)).length);
    const wanted = withNeeds
      .map((n, i) => ({ n, i, y: mean(needsOf(byName.get(n))) }))
      .sort((a, b) => a.y - b.y || a.i - b.i);
    let bottom = -ROW_GAP;
    wanted.forEach(({ n, y }) => {
      top.set(n, Math.max(y, bottom + ROW_GAP));
      bottom = top.get(n) + BOX_H;
    });
    sources.forEach((n) => {
      top.set(n, bottom + ROW_GAP);
      bottom = top.get(n) + BOX_H;
    });
  });

  // A column with a single stage is centred on the stages that consume it.
  for (let c = columns.length - 1; c >= 0; c -= 1) {
    if (columns[c].length !== 1) continue;
    const next = consumers.get(columns[c][0]).filter((n) => top.has(n));
    if (next.length) top.set(columns[c][0], mean(next));
  }

  const minTop = Math.min(0, ...top.values());
  const nodes = stages.map((s) => ({
    stage: s,
    x: PAD + depth.get(s.name) * (BOX_W + COL_GAP),
    y: PAD + top.get(s.name) - minTop,
  }));
  const width = PAD * 2 + columnCount * BOX_W + (columnCount - 1) * COL_GAP;
  const height = PAD * 2 + Math.max(...nodes.map((n) => n.y - PAD + BOX_H), BOX_H);
  const edges = [];
  stages.forEach((s) => needsOf(s).forEach((n) => edges.push([n, s.name])));
  return { nodes, edges, width, height };
}

function renderGraph(stages, selected, select) {
  const { nodes, edges, width, height } = layout(stages);
  const at = new Map(nodes.map((n) => [n.stage.name, n]));

  const edgeEls = edges.map(([from, to]) => {
    const a = at.get(from);
    const b = at.get(to);
    const x1 = a.x + BOX_W;
    const y1 = a.y + BOX_H / 2;
    const x2 = b.x;
    const y2 = b.y + BOX_H / 2;
    const dx = Math.max(12, (x2 - x1) / 2);
    const active = selected === from || selected === to;
    return svg('path', {
      class: active ? 'edge active' : 'edge',
      d: `M${x1},${y1} C${x1 + dx},${y1} ${x2 - dx},${y2} ${x2 - 1},${y2}`,
      'marker-end': active ? 'url(#arrow-active)' : 'url(#arrow)',
    });
  });

  const nodeEls = nodes.map(({ stage, x, y }) => {
    const isSelected = stage.name === selected;
    const activate = () => select(isSelected ? null : stage.name);
    return svg(
      'g',
      {
        class: isSelected ? 'node selected' : 'node',
        transform: `translate(${x},${y})`,
        tabindex: 0,
        role: 'button',
        'aria-pressed': String(isSelected),
        'aria-label': `${stage.name}, status ${statusLabel(stage.status)}`,
        'data-status': stage.status,
        'data-focus': `node:${stage.name}`,
        onclick: activate,
        onkeydown: (ev) => {
          if (ev.key === 'Enter' || ev.key === ' ') {
            ev.preventDefault();
            activate();
          }
        },
      },
      svg('title', null, `${stage.name}: ${statusLabel(stage.status)}`),
      svg('rect', { class: 'box', width: BOX_W, height: BOX_H, rx: 4 }),
      svg('rect', { class: 'bar', x: 1, y: 1, width: 4, height: BOX_H - 2 }),
      svg('text', { class: 'name', x: 15, y: 23 }, stage.name),
      svg('text', { class: 'stat', x: 15, y: 40 }, statusLabel(stage.status)),
    );
  });

  const arrow = (id) =>
    svg(
      'marker',
      { id, viewBox: '0 0 8 8', refX: 7, refY: 4, markerWidth: 7, markerHeight: 7, orient: 'auto' },
      svg('path', { d: 'M0,0 L8,4 L0,8 z' }),
    );

  return svg(
    'svg',
    { class: 'graph', viewBox: `0 0 ${width} ${height}`, role: 'group', 'aria-label': 'Pipeline stages and their dependencies' },
    svg('defs', null, arrow('arrow'), arrow('arrow-active')),
    edgeEls,
    nodeEls,
  );
}

function renderSummary(stages) {
  const counts = new Map();
  stages.forEach((s) => counts.set(s.status, (counts.get(s.status) || 0) + 1));
  const known = STATUS_ORDER.filter((s) => counts.has(s));
  const other = [...counts.keys()].filter((s) => !STATUS_ORDER.includes(s));
  return h(
    'p',
    { class: 'summary' },
    h('span', null, `${stages.length} stages`),
    [...known, ...other].map((status) =>
      h('span', { class: 'count' }, pill(status), h('b', null, String(counts.get(status)))),
    ),
  );
}

function renderVerdict(v) {
  return h(
    'div',
    { class: 'verdict' },
    h('div', { class: 'verdict-top' }, h('span', { class: 'check' }, v.check), pill(v.result)),
    h(
      'div',
      { class: 'line' },
      v.requirement ? h('a', { class: 'mono', href: `#/requirements/${encodeURIComponent(v.requirement)}` }, v.requirement) : 'no requirement',
      v.evidence_level ? ` · ${v.evidence_level}` : '',
    ),
    h(
      'div',
      { class: 'line num' },
      `measured ${v.measured === null || v.measured === undefined ? '—' : fmtNumber(v.measured)}`,
      ` · limit ${fmtLimit(v.limit)}`,
    ),
    v.message ? h('div', { class: 'msg' }, v.message) : null,
  );
}

function renderPanel(name, stage, panel, close) {
  const head = h(
    'div',
    { class: 'panel-head' },
    h(
      'div',
      null,
      h('h2', null, name),
      h('div', { class: 'sub' }, pill(stage ? stage.status : panel.data?.status), stage && stage.key ? h('span', { class: 'mono muted', title: stage.key }, stage.key.slice(0, 19)) : null),
    ),
    h('button', { class: 'btn btn-quiet', type: 'button', onclick: close, 'aria-label': 'Close stage details' }, 'Close'),
  );

  let body;
  if (panel.status === 'error' && !panel.data) {
    body = errorState(panel.error);
  } else if (!panel.data) {
    body = loadingState();
  } else {
    const { facts, verdicts } = panel.data;
    body = [
      panel.error ? staleNotice(panel.error) : null,
      stage && stage.needs && stage.needs.length
        ? h(
            'dl',
            { class: 'meta' },
            h('dt', null, 'needs'),
            h('dd', null, h('span', { class: 'chips' }, stage.needs.map((n) => h('a', { class: 'chip', href: `#/pipeline/${encodeURIComponent(n)}` }, n)))),
          )
        : null,
      h('h3', null, 'Facts'),
      kvTable(facts, 'No facts reported.'),
      h('h3', null, 'Verdicts'),
      verdicts && verdicts.length ? verdicts.map(renderVerdict) : h('p', { class: 'muted' }, 'No verdicts for this stage.'),
    ];
  }
  return h('aside', { class: 'panel', 'aria-label': `Stage ${name}` }, head, body);
}

export function mount(host, ctx) {
  let selected = ctx.param || null;
  let panel = { name: null, status: 'idle', data: null, error: null };
  let token = 0;

  async function loadPanel() {
    if (!selected) return;
    const name = selected;
    const mine = ++token;
    if (panel.name !== name) panel = { name, status: 'loading', data: null, error: null };
    paint();
    try {
      const data = await ctx.api.stage(name);
      if (mine !== token) return;
      panel = { name, status: 'ready', data, error: null };
    } catch (error) {
      if (mine !== token) return;
      panel = { ...panel, status: 'error', error };
    }
    paint();
  }

  function select(name) {
    selected = name;
    const next = name ? `#/pipeline/${encodeURIComponent(name)}` : '#/pipeline';
    if (location.hash !== next) history.replaceState(null, '', next);
    if (name) loadPanel();
    else {
      token += 1;
      panel = { name: null, status: 'idle', data: null, error: null };
      paint();
    }
  }

  function paint() {
    rerender(host, () => {
      const entry = ctx.get('pipeline');
      const wrapper = h('div', null, h('header', { class: 'page-head' }, h('h1', null, 'Pipeline'), h('p', { class: 'lede' }, 'Twelve stages, left to right by dependency. Select a stage for its facts and verdicts.')));
      const state = entryState(entry, () => ctx.reload('pipeline'));
      if (state) {
        wrapper.append(state);
        return wrapper;
      }
      const { stages } = entry.data;
      if (!stages || !stages.length) {
        wrapper.append(h('div', { class: 'state' }, h('strong', null, 'No stages'), h('span', null, 'The pipeline has not been planned yet.')));
        return wrapper;
      }
      if (entry.error) wrapper.append(staleNotice(entry.error));
      wrapper.append(renderSummary(stages));
      const stage = selected ? stages.find((s) => s.name === selected) : null;
      const showPanel = selected && stage;
      const layoutEl = h(
        'div',
        { class: showPanel ? 'pipeline-layout has-panel' : 'pipeline-layout' },
        h('div', { class: 'graph-wrap' }, renderGraph(stages, showPanel ? selected : null, select)),
        showPanel ? renderPanel(selected, stage, panel.name === selected ? panel : { status: 'loading' }, () => closePanel()) : null,
      );
      wrapper.append(layoutEl);
      if (selected && !stage) wrapper.append(h('p', { class: 'muted' }, `There is no stage named “${selected}”.`));
      return wrapper;
    });
  }

  function closePanel() {
    const name = selected;
    select(null);
    const node = host.querySelector(`[data-focus="node:${CSS.escape(name)}"]`);
    if (node) node.focus();
  }

  const onKey = (ev) => {
    if (ev.key === 'Escape' && selected) closePanel();
  };
  document.addEventListener('keydown', onKey);
  ctx.onCleanup(() => document.removeEventListener('keydown', onKey));

  ctx.watch(['pipeline'], () => {
    paint();
    if (selected) loadPanel(); // a pipeline change may have changed this stage's facts
  });

  paint();
  if (selected) loadPanel();

  return {
    setParam(param) {
      const next = param || null;
      if (next === selected) return;
      selected = next;
      if (selected) loadPanel();
      else paint();
    },
    destroy() {
      token += 1;
    },
  };
}
