// Design view: 3D viewer for the mechanical stage's STL files, plus mechanical and electronics facts.

import { fetchFile } from '../api.js';
import { h, pill, kvTable, staleNotice, errorState, loadingState, clear } from './ui.js';

const FILES = ['hull.stl', 'lid.stl'];
const FACT_STAGES = [
  ['mechanical', 'Mechanical'],
  ['electronics', 'Electronics'],
];

export function mount(host, ctx) {
  let destroyed = false;
  let viewer = null;
  let lidOn = true;
  let available = { hull: false, lid: false };
  let hadModels = false;
  let modelToken = 0;
  const facts = Object.fromEntries(FACT_STAGES.map(([name]) => [name, { status: 'loading', data: null, error: null }]));
  const factToken = Object.fromEntries(FACT_STAGES.map(([name]) => [name, 0]));

  // ---- static layout (built once so the canvas survives data refreshes)
  const lidBtn = h('button', { class: 'btn', type: 'button', 'aria-pressed': 'true', onclick: () => setLid(!lidOn) }, 'Lid');
  const resetBtn = h('button', { class: 'btn', type: 'button', onclick: () => viewer && viewer.reset() }, 'Reset view');
  const filesLabel = h('span', { class: 'files' });
  const viewport = h('div', { class: 'viewport' });
  const factsCol = h('div', { class: 'facts-col' });

  host.append(
    h('header', { class: 'page-head' }, h('h1', null, 'Design'), h('p', { class: 'lede' }, 'The hull and lid from the mechanical stage, with the facts the stages reported.')),
    h(
      'div',
      { class: 'design-layout' },
      h('div', null, h('div', { class: 'viewer-toolbar' }, lidBtn, resetBtn, filesLabel), viewport),
      factsCol,
    ),
  );

  function setLid(on) {
    lidOn = on;
    lidBtn.setAttribute('aria-pressed', String(on));
    if (viewer) viewer.setLidVisible(on);
  }

  function setOverlay(kind, error) {
    viewport.querySelectorAll('.overlay, .hint').forEach((el) => el.remove());
    let node = null;
    if (kind === 'loading') {
      node = h('div', { class: 'overlay', role: 'status' }, 'Loading models…');
    } else if (kind === 'missing') {
      node = h('div', { class: 'overlay' }, h('div', null, h('strong', null, 'Not built yet'), h('span', null, 'The mechanical stage has not produced hull.stl or lid.stl.')));
    } else if (kind === 'error') {
      node = h(
        'div',
        { class: 'overlay', role: 'alert' },
        h('div', null, h('strong', null, 'Could not show the 3D model'), h('span', null, error?.message || 'Unknown error.'), h('div', null, h('button', { class: 'btn', type: 'button', style: 'margin-top:12px', onclick: loadModels }, 'Try again'))),
      );
    } else if (kind === 'ready') {
      node = h('div', { class: 'hint' }, 'Drag to rotate · scroll to zoom · right-drag to pan');
    }
    if (node) viewport.append(node);
  }

  function syncToolbar() {
    lidBtn.disabled = !available.lid;
    resetBtn.disabled = !viewer;
    const names = FILES.filter((f) => available[f.replace('.stl', '')]);
    filesLabel.textContent = names.join(' · ');
  }

  async function loadModels() {
    const mine = ++modelToken;
    if (!hadModels) setOverlay('loading');
    const version = ctx.designVersion();
    const settled = await Promise.allSettled(FILES.map((f) => fetchFile(ctx.api.fileUrl('mechanical', f, version))));
    if (destroyed || mine !== modelToken) return;

    const failed = settled.find((r) => r.status === 'rejected');
    if (failed) {
      available = { hull: false, lid: false };
      syncToolbar();
      setOverlay('error', failed.reason);
      return;
    }
    const [hullRes, lidRes] = settled.map((r) => r.value);
    if (hullRes.status === 'missing' && lidRes.status === 'missing') {
      if (viewer) viewer.setModels({});
      hadModels = false;
      available = { hull: false, lid: false };
      syncToolbar();
      setOverlay('missing');
      return;
    }

    try {
      const { createViewer, parseSTL } = await import('./viewer.js');
      if (destroyed || mine !== modelToken) return;
      const geometries = {};
      if (hullRes.status === 'ok') geometries.hull = parseSTL(hullRes.buffer);
      if (lidRes.status === 'ok') geometries.lid = parseSTL(lidRes.buffer);
      if (!viewer) viewer = createViewer(viewport);
      viewer.setModels(geometries, { keepCamera: hadModels });
      viewer.setLidVisible(lidOn);
      hadModels = true;
      available = { hull: Boolean(geometries.hull), lid: Boolean(geometries.lid) };
      syncToolbar();
      setOverlay('ready');
    } catch (error) {
      if (destroyed || mine !== modelToken) return;
      setOverlay('error', error);
    }
  }

  async function loadFacts(name) {
    const mine = ++factToken[name];
    try {
      const data = await ctx.api.stage(name);
      if (destroyed || mine !== factToken[name]) return;
      facts[name] = { status: 'ready', data, error: null };
    } catch (error) {
      if (destroyed || mine !== factToken[name]) return;
      facts[name] = { ...facts[name], status: 'error', error };
    }
    paintFacts();
  }

  function paintFacts() {
    clear(factsCol);
    FACT_STAGES.forEach(([name, title]) => {
      const entry = facts[name];
      let body;
      if (entry.status === 'loading') body = loadingState();
      else if (entry.status === 'error' && !entry.data) body = errorState(entry.error, () => loadFacts(name));
      else {
        const { facts: values } = entry.data;
        body = [entry.error ? staleNotice(entry.error) : null, kvTable(values, 'No facts reported yet.')];
      }
      factsCol.append(h('section', null, h('h2', null, title, entry.data ? pill(entry.data.status) : null), body));
    });
  }

  function refresh() {
    FACT_STAGES.forEach(([name]) => loadFacts(name));
  }

  ctx.watch(['design'], () => {
    loadModels();
    refresh();
  });
  ctx.watch(['pipeline'], refresh);

  syncToolbar();
  paintFacts();
  loadModels();
  refresh();

  return {
    destroy() {
      destroyed = true;
      modelToken += 1;
      if (viewer) viewer.dispose();
      viewer = null;
    },
  };
}
