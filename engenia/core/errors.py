"""One base class for every error the core raises on purpose, so the CLI can catch them all."""


class CoreError(Exception):
    """A problem with the user's spec, the stage graph, the hash inputs or the cache."""
