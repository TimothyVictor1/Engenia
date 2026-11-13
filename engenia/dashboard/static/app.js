// Engenia dashboard shell: data store, hash router, top bar, live updates.
// The frontend never decides pass or fail; it stores and displays what the API returns.

import { api, mode, detectMode, openEvents } from './api.js';
import { errorState, loadingState, fmtTime } from './views/ui.js';

const VIEWS = {
  pipeline: () => import('./views/pipeline.js'),
  requirements: () => import('./views/requirements.js'),
  design: () => import('./views/design.js'),
  contract: () => import('./views/contract.js'),
  scenarios: () => import('./views/scenarios.js'),
};

// ---------------------------------------------------------------- store

const LOADERS = {
  pipeline: api.pipeline,
  requirements: api.requirements,
  contract: api.contract,
  scenarios: api.scenarios,
};

/** area -> { status: 'idle' | 'loading' | 'ready' | 'error', data, error } */
const entries = Object.fromEntries(
  Object.keys(LOADERS).map((area) => [area, { status: 'idle', data: null, error: null }]),
);
const sequence = Object.fromEntries(Object.keys(LOADERS).map((area) => [area, 0]));
const subscribers = new Set();
let designVersion = 0;
let lastUpdate = null;

function notify(areas) {
  for (const sub of [...subscribers]) {
    if (areas.some((area) => sub.areas.has(area))) sub.fn(areas);
  }
}

async function load(area) {
  const entry = entries[area];
  const token = ++sequence[area];
  if (!entry.data) {
    entry.status = 'loading';
    notify([area]);
  }
  try {
    const data = await LOADERS[area]();
    if (token !== sequence[area]) return;
    Object.assign(entry, { status: 'ready', data, error: null });
    markUpdated(data);
  } catch (error) {
    if (token !== sequence[area]) return;
    Object.assign(entry, { status: 'error', error });
  }
  if (area === 'pipeline') renderTopbar();
  notify([area]);
}

const ensure = (area) => {
  if (entries[area].status === 'idle') load(area);
};

function makeCtx(param) {
  const disposers = [];
  return {
    param,
    api,
    get: (area) => entries[area],
    reload: (area) => load(area),
    ensure: (...areas) => areas.forEach(ensure),
    designVersion: () => designVersion,
    watch(areas, fn) {
      const sub = { areas: new Set(areas), fn };
      subscribers.add(sub);
      disposers.push(() => subscribers.delete(sub));
    },
    onCleanup: (fn) => disposers.push(fn),
    dispose: () => disposers.splice(0).forEach((fn) => fn()),
  };
}

// ---------------------------------------------------------------- top bar

const $ = (id) => document.getElementById(id);

function markUpdated(data) {
  if (mode.sample) {
    const stamp = data && data.updated ? new Date(data.updated) : null;
    if (stamp && !Number.isNaN(stamp.getTime())) lastUpdate = stamp;
  } else {
    lastUpdate = new Date();
  }
  renderTopbar();
}

function renderTopbar() {
  const pipeline = entries.pipeline.data;
  $('project-name').textContent = pipeline && pipeline.project ? pipeline.project : 'No project';
  const updated = $('updated');
  updated.replaceChildren();
  if (lastUpdate) {
    const time = document.createElement('time');
    time.className = 'num';
    time.dateTime = lastUpdate.toISOString();
    time.textContent = fmtTime(lastUpdate);
    updated.append(mode.sample ? 'Data as of ' : 'Last updated ', time);
  }
}

function setConnection(state) {
  const conn = $('conn');
  conn.hidden = false;
  conn.dataset.state = state;
  conn.textContent = state === 'live' ? 'Live' : 'Offline';
}

// ---------------------------------------------------------------- live updates

const pendingAreas = new Set();
let flushTimer = null;
let wasOffline = false;

function queueChange(areas) {
  areas.forEach((area) => pendingAreas.add(area));
  clearTimeout(flushTimer);
  flushTimer = setTimeout(flush, 150); // coalesce bursts of events
}

function flush() {
  const areas = [...pendingAreas];
  pendingAreas.clear();
  refresh(areas);
}

/** Refetch only the named areas. Areas never opened yet stay idle and load on first visit. */
function refresh(areas) {
  for (const area of areas) {
    if (area === 'design') {
      designVersion += 1;
      notify(['design']);
    } else if (LOADERS[area] && entries[area].status !== 'idle') {
      load(area);
    }
  }
}

function startLive() {
  setConnection('offline');
  openEvents({
    onChange: queueChange,
    onState(state) {
      setConnection(state);
      if (state === 'offline') {
        wasOffline = true;
      } else if (wasOffline) {
        wasOffline = false; // we may have missed events while away: refetch everything loaded
        refresh([...Object.keys(LOADERS), 'design']);
      }
    },
  });
}

// ---------------------------------------------------------------- router

let current = null;
let routeToken = 0;
let userNavigated = false;

function parseHash() {
  const [, view = '', param = ''] = location.hash.replace(/^#/, '').split('/');
  return { view, param: param ? decodeURIComponent(param) : '' };
}

async function route() {
  const { view, param } = parseHash();
  if (!VIEWS[view]) {
    location.replace('#/pipeline');
    return;
  }
  document.querySelectorAll('.nav a').forEach((a) => {
    if (a.dataset.view === view) a.setAttribute('aria-current', 'page');
    else a.removeAttribute('aria-current');
  });

  if (current && current.name === view) {
    if (current.instance.setParam) current.instance.setParam(param);
    return;
  }

  const token = ++routeToken;
  if (current) {
    current.instance.destroy?.();
    current.ctx.dispose();
    current = null;
  }
  const main = $('main');
  main.replaceChildren(loadingState());
  let module;
  try {
    module = await VIEWS[view]();
  } catch (error) {
    if (token === routeToken) main.replaceChildren(errorState(error, route));
    return;
  }
  if (token !== routeToken) return;

  main.replaceChildren();
  const ctx = makeCtx(param);
  const instance = module.mount(main, ctx);
  current = { name: view, instance, ctx };
  if (userNavigated) main.focus({ preventScroll: true });
  document.title = `${view[0].toUpperCase()}${view.slice(1)} · Engenia`;
}

// ---------------------------------------------------------------- start

async function start() {
  const theme = new URLSearchParams(location.search).get('theme');
  if (theme === 'light' || theme === 'dark') document.documentElement.dataset.theme = theme;

  let first;
  try {
    first = await detectMode();
  } catch (error) {
    $('project-name').textContent = 'Unavailable';
    $('main').replaceChildren(errorState(new Error('No API and no sample data could be loaded.'), () => location.reload()));
    return;
  }
  Object.assign(entries.pipeline, { status: 'ready', data: first, error: null });
  markUpdated(first);

  if (mode.sample) $('sample-tag').hidden = false;
  else startLive();

  window.addEventListener('hashchange', () => {
    userNavigated = current !== null;
    route();
  });
  if (!location.hash) location.replace('#/pipeline');
  route();
}

start();
