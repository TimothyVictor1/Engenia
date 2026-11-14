"""CLI: uv run python -m engenia.dashboard --project harbour-listener --port 8080
or, for every project with a switcher: uv run python -m engenia.dashboard --all"""

import argparse

import uvicorn

from engenia.dashboard import create_app, create_multi_app


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve a read-only project dashboard")
    parser.add_argument("--project", help="the project to serve (with --all, the one opened first)")
    parser.add_argument("--all", action="store_true",
                        help="serve every project at /p/<name>/ with a project switcher")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    if not args.project and not args.all:
        parser.error("give --project <name>, or --all to serve every project")
    try:
        app = create_multi_app(default=args.project) if args.all else create_app(args.project)
    except ValueError as exc:
        parser.error(str(exc))
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
