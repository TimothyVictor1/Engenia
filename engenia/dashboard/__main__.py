"""CLI: uv run python -m engenia.dashboard --project harbour-listener --port 8080."""

import argparse

import uvicorn

from engenia.dashboard import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve one project's read-only dashboard")
    parser.add_argument("--project", required=True)
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    try:
        app = create_app(args.project)
    except ValueError as exc:
        parser.error(str(exc))
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
