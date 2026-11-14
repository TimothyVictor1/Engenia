# dashboard-ui

React + Vite + Tailwind front end for the Engenia dashboard. Design follows the team's Lovable
reference; the data comes from our FastAPI routes (`/api/pipeline`, `/api/requirements`,
`/api/stages/{name}`, `/api/contract`, `/api/scenarios`, `/api/files/...`) and live updates from
`/api/events` (SSE).

- `npm run build` writes into `../engenia/dashboard/static/`. Commit the result.
- `npm run dev` serves with hot reload and proxies `/api` to `127.0.0.1:8080`.
- `npm test` runs the unit and view tests (jsdom).
- Sample mode (`?sample=1`, the header switch, or automatic when no server answers) reads
  `public/sample/*.json`.

## Live studio events

The Live studio shows events shaped `{ts, run_id, stage, attempt, kind, data}`, `kind` being one
of `message | tool_call | tool_result | code | decision | preview | verdict | repair`. It reads them
from `/api/events`: a message whose JSON has a `kind` is a live event; one with `changed` still
refreshes the other views. Field names inside `data` that the UI uses:

| kind | data |
|---|---|
| decision | `title`, `choice`, `reason`, `rejected: [{option, why}]`, `requirements: [id]` |
| code | `path`, `code`, optional `diff` (list of `+`/`-`/` ` lines; computed if absent) |
| preview | `stage`, `files: ["hull.stl", ...]` (fetched from `/api/files/<stage>/<file>`) |
| verdict | a verdict: `check`, `requirement`, `measured`, `limit`, `result`, `evidence_level`, `message` |
| repair | `summary` (or `reason`) |

`public/sample/live-events.json` is a sample run used for replay.
