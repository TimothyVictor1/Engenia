import json

import pytest

from engenia.core import cache
from engenia.core.cache import Cache, CacheError, default_store
from engenia.core.hashing import HashError

KEY = "sha256:" + "a" * 64
OTHER = "sha256:" + "b" * 64


@pytest.fixture
def store(tmp_path):
    return Cache(tmp_path / "store")


@pytest.fixture
def build(tmp_path):
    """A build directory like a stage leaves behind."""
    d = tmp_path / "build" / "mechanical"
    (d / "sub" / "deep").mkdir(parents=True)
    (d / "hull.step").write_bytes(b"\x00\x01binary\xff" * 100)
    (d / "sub" / "deep" / "notes.txt").write_text("hello\n")
    (d / "interface.json").write_text('{\n  "facts": {"mass": 1}\n}\n')
    (d / "verdict.json").write_text("[]\n")
    (d / "run.sh").write_text("#!/bin/sh\n")
    (d / "run.sh").chmod(0o755)
    (d / "empty.txt").write_bytes(b"")
    return d


def snapshot(directory):
    """Every file's relative path, bytes and executable bit."""
    return {
        p.relative_to(directory).as_posix(): (p.read_bytes(), bool(p.stat().st_mode & 0o100))
        for p in sorted(directory.rglob("*"))
        if p.is_file()
    }


def test_get_restores_exactly_what_put_stored(store, build, tmp_path):
    original = snapshot(build)
    store.put(KEY, build)
    target = tmp_path / "elsewhere" / "mechanical"
    assert store.get(KEY, target) is True
    assert snapshot(target) == original
    assert (target / "interface.json").read_text() == '{\n  "facts": {"mass": 1}\n}\n'
    assert (target / "verdict.json").read_text() == "[]\n"
    assert (target / "run.sh").stat().st_mode & 0o111
    assert not (target / "hull.step").stat().st_mode & 0o111


def test_get_replaces_what_was_in_the_build_directory(store, build, tmp_path):
    store.put(KEY, build)
    target = tmp_path / "t"
    target.mkdir()
    (target / "stale.txt").write_text("old")
    (target / "hull.step").write_text("different")
    assert store.get(KEY, target)
    assert not (target / "stale.txt").exists()
    assert snapshot(target) == snapshot(build)


def test_a_miss_returns_false_and_touches_nothing(store, build):
    before = snapshot(build)
    assert store.get(OTHER, build) is False
    assert snapshot(build) == before
    assert store.has(OTHER) is False


def test_keys_are_independent(store, build, tmp_path):
    store.put(KEY, build)
    (build / "hull.step").write_bytes(b"v2")
    store.put(OTHER, build)
    one, two = tmp_path / "one", tmp_path / "two"
    assert store.get(KEY, one) and store.get(OTHER, two)
    assert (one / "hull.step").read_bytes() != b"v2"
    assert (two / "hull.step").read_bytes() == b"v2"


def test_equal_files_share_one_blob(store, build, tmp_path):
    (build / "copy.step").write_bytes((build / "hull.step").read_bytes())
    store.put(KEY, build)
    store.put(OTHER, build)
    blobs = [p for p in (store.store / "blobs").rglob("*") if p.is_file()]
    assert len(blobs) == len({p.name for p in blobs}) == 6  # 7 files, 2 of them equal


def test_put_twice_is_fine(store, build, tmp_path):
    store.put(KEY, build)
    store.put(KEY, build)
    assert store.get(KEY, tmp_path / "t")


def test_put_takes_a_snapshot_not_a_reference(store, build, tmp_path):
    store.put(KEY, build)
    (build / "hull.step").write_bytes(b"changed afterwards")
    store.get(KEY, tmp_path / "t")
    assert (tmp_path / "t" / "hull.step").read_bytes().startswith(b"\x00\x01binary")


def test_an_empty_build_directory_round_trips(store, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    store.put(KEY, empty)
    assert store.get(KEY, tmp_path / "t") is True
    assert list((tmp_path / "t").iterdir()) == []


def test_no_temporary_files_are_left_behind(store, build, tmp_path):
    store.put(KEY, build)
    store.get(KEY, tmp_path / "t")
    leftovers = [p for p in tmp_path.rglob("*") if ".tmp-" in p.name or ".restore-" in p.name]
    assert leftovers == []


# --- bad input ------------------------------------------------------------------------------


@pytest.mark.parametrize("key", ["", "abc", "sha256:xyz", "sha256:" + "A" * 64, "../" + "a" * 64])
def test_bad_keys_are_rejected(store, build, tmp_path, key):
    with pytest.raises(CacheError, match="bad cache key"):
        store.put(key, build)
    with pytest.raises(CacheError, match="bad cache key"):
        store.get(key, tmp_path / "t")


def test_put_needs_a_directory(store, tmp_path):
    with pytest.raises(CacheError, match="not a directory"):
        store.put(KEY, tmp_path / "missing")
    assert not store.has(KEY)


def test_symlinks_in_a_build_are_refused_and_nothing_is_stored(store, build, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("do not store me")
    (build / "link").symlink_to(secret)
    with pytest.raises(HashError, match="symlink"):
        store.put(KEY, build)
    assert not store.has(KEY)


def test_a_damaged_blob_is_a_miss_and_heals_on_the_next_put(store, build, tmp_path):
    store.put(KEY, build)
    blobs = [p for p in (store.store / "blobs").rglob("*") if p.is_file()]
    blob = max(blobs, key=lambda p: p.stat().st_size)
    blob.write_bytes(b"bit rot")
    target = tmp_path / "t"
    target.mkdir()
    (target / "keep.txt").write_text("untouched")
    assert store.get(KEY, target) is False
    assert (target / "keep.txt").read_text() == "untouched"  # nothing half restored
    assert not blob.exists()
    store.put(KEY, build)
    assert store.get(KEY, target) is True
    assert snapshot(target) == snapshot(build)


def test_a_missing_blob_is_a_miss(store, build, tmp_path):
    store.put(KEY, build)
    next(p for p in (store.store / "blobs").rglob("*") if p.is_file()).unlink()
    assert store.get(KEY, tmp_path / "t") is False
    assert not (tmp_path / "t").exists()


@pytest.mark.parametrize(
    "bad_path", ["../escape.txt", "/etc/passwd", "a/../../b", "a\\b", "", ".", "a//b"]
)
def test_an_entry_that_points_outside_the_target_is_a_miss(store, build, tmp_path, bad_path):
    store.put(KEY, build)
    entry = store.store / "entries" / f"{KEY[7:]}.json"
    data = json.loads(entry.read_text())
    data["files"][0]["path"] = bad_path
    entry.write_text(json.dumps(data))
    target = tmp_path / "work" / "t"
    assert store.get(KEY, target) is False
    assert not (tmp_path / "work" / "escape.txt").exists()
    assert not target.exists()


@pytest.mark.parametrize("damage", ["not json", "[]", '{"version": 99}', '{"version": 1}'])
def test_a_damaged_entry_is_a_miss(store, build, tmp_path, damage):
    store.put(KEY, build)
    (store.store / "entries" / f"{KEY[7:]}.json").write_text(damage)
    assert store.get(KEY, tmp_path / "t") is False


def test_an_entry_for_another_key_is_not_used(store, build, tmp_path):
    store.put(KEY, build)
    entries = store.store / "entries"
    (entries / f"{KEY[7:]}.json").rename(entries / f"{OTHER[7:]}.json")
    assert store.get(OTHER, tmp_path / "t") is False


# --- where the store lives ------------------------------------------------------------------


def test_default_store_follows_the_environment(monkeypatch, tmp_path):
    monkeypatch.delenv("ENGENIA_STORE", raising=False)
    assert default_store(tmp_path) == tmp_path / ".engenia" / "store"
    monkeypatch.setenv("ENGENIA_STORE", "cache/here")
    assert default_store(tmp_path) == tmp_path / "cache" / "here"
    monkeypatch.setenv("ENGENIA_STORE", str(tmp_path / "abs"))
    assert default_store(tmp_path) == tmp_path / "abs"


def test_module_functions_use_the_environment_store(monkeypatch, build, tmp_path):
    monkeypatch.setenv("ENGENIA_STORE", str(tmp_path / "envstore"))
    cache.put(KEY, build)
    assert cache.has(KEY)
    assert cache.get(KEY, tmp_path / "t") is True
    assert (tmp_path / "envstore" / "entries").is_dir()
    assert cache.get(OTHER, tmp_path / "t2") is False
