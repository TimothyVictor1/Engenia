"""Read-only live dashboard API. Start with python -m engenia.dashboard."""

from engenia.dashboard.app import create_app

__all__ = ["create_app"]
