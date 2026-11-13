// The only module that talks to the network. In sample mode every /api/* path is answered from
// sample/*.json instead, with the same shapes.

export class ApiError extends Error {
  constructor(message, { status = null, network = false } = {}) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.network = network;
  }
}

export const mode = { sample: false };

const enc = encodeURIComponent;

function resolve(path) {
  if (!mode.sample) return path;
  const rest = path.replace(/^\/api\//, '');
  return rest.startsWith('files/') ? `sample/${rest}` : `sample/${rest}.json`;
}

async function request(path) {
  let res;
  try {
    res = await fetch(resolve(path), { headers: { Accept: 'application/json' }, cache: 'no-store' });
  } catch (err) {
    throw new ApiError('Cannot reach the server.', { network: true });
  }
  if (!res.ok) throw new ApiError(`Server answered ${res.status} ${res.statusText}`.trim(), { status: res.status });
  try {
    return await res.json();
  } catch (err) {
    throw new ApiError('The server did not return valid JSON.', { status: res.status });
  }
}

export const api = {
  pipeline: () => request('/api/pipeline'),
  requirements: () => request('/api/requirements'),
  stage: (name) => request(`/api/stages/${enc(name)}`),
  contract: () => request('/api/contract'),
  scenarios: () => request('/api/scenarios'),
  /** URL of a raw build file. `version` busts the cache after a design change. */
  fileUrl: (stage, file, version = 0) => {
    const base = resolve(`/api/files/${enc(stage)}/${enc(file)}`);
    return version && !mode.sample ? `${base}?v=${version}` : base;
  },
};

/** Fetch a raw file. Resolves to { status: 'ok', buffer } | { status: 'missing' }; rejects on other failures. */
export async function fetchFile(url) {
  let res;
  try {
    res = await fetch(url, { cache: 'no-store' });
  } catch (err) {
    throw new ApiError('Cannot reach the server.', { network: true });
  }
  if (res.status === 404) return { status: 'missing' };
  if (!res.ok) throw new ApiError(`Server answered ${res.status} ${res.statusText}`.trim(), { status: res.status });
  return { status: 'ok', buffer: await res.arrayBuffer() };
}

/**
 * Choose live or sample mode once, at start. ?sample=1 forces sample mode; otherwise the
 * pipeline endpoint is tried and, if it cannot be reached, sample data is used.
 * Resolves to the first pipeline response.
 */
export async function detectMode() {
  const params = new URLSearchParams(location.search);
  if (params.get('sample') === '1') {
    mode.sample = true;
    return api.pipeline();
  }
  try {
    return await api.pipeline();
  } catch (err) {
    mode.sample = true;
    return api.pipeline();
  }
}

/**
 * Subscribe to /api/events. Calls onChange(areas[]) for each message, onState('live'|'offline').
 * EventSource reconnects by itself; if the browser gives up (readyState CLOSED) we retry.
 * Returns a function that closes the stream.
 */
export function openEvents({ onChange, onState }) {
  let es = null;
  let timer = null;
  let closed = false;

  const connect = () => {
    es = new EventSource('/api/events');
    es.onopen = () => onState('live');
    es.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data);
        if (data && Array.isArray(data.changed)) onChange(data.changed.filter((a) => typeof a === 'string'));
      } catch (err) {
        /* ignore malformed event */
      }
    };
    es.onerror = () => {
      onState('offline');
      if (es.readyState === EventSource.CLOSED && !closed) {
        es.close();
        timer = setTimeout(connect, 3000);
      }
    };
  };
  connect();

  return () => {
    closed = true;
    clearTimeout(timer);
    if (es) es.close();
  };
}
