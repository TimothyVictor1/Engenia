# Engenia

Engenia takes a written product request and produces a verified design. See `PROJECT.md`.

## Run on Codespaces

1. On GitHub choose **Code → Codespaces → Create codespace on main**, and pick the **4-core** machine.
2. Wait for the first build. It installs Python 3.12, uv, the CAD system libraries, `uv sync`, and the PlatformIO `native` platform, so the firmware stage downloads nothing later.
3. Run the pipeline and the checks:

   ```bash
   uv run engenia run harbour-listener
   uv run engenia gate harbour-listener
   ```

4. Start the dashboard. Bind to `0.0.0.0` so Codespaces can forward port 8080:

   ```bash
   uv run python -m engenia.dashboard --project harbour-listener --host 0.0.0.0 --port 8080
   ```

   Open the forwarded port 8080 from the **Ports** tab.

   The dashboard is a React app in `dashboard-ui/`. Its built files are committed in `engenia/dashboard/static/`, so no Node is needed to run it. To change the UI, edit `dashboard-ui/` and rebuild: `cd dashboard-ui && npm install && npm run build` (use `npm run dev` for live reload against a running dashboard on port 8080, and `npm test` for its tests).
