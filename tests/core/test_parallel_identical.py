"""A parallel run writes exactly the files a serial run writes."""

import shutil

import pytest

from engenia.core.engine import execute

PROJECT = "harbour-listener"

def snapshot(project_dir):
    """Every file under src/ and build/, by relative path, with its bytes."""
    files = {}
    for part in ("src", "build"):
        for path in sorted((project_dir / part).rglob("*")):
            if path.is_file():
                files[path.relative_to(project_dir).as_posix()] = path.read_bytes()
    return files


def run_in(tmp_path, name, fake_registry, workers, overrides=None):
    root = tmp_path / name
    shutil.copytree(
        pytest.importorskip("tests.core.conftest").FIXTURE_SPEC,
        root / "projects" / PROJECT / "spec",
    )
    report = execute(
        PROJECT, root, registry=fake_registry(overrides), store=root / "store", workers=workers
    )
    assert report.ok
    return report, snapshot(root / "projects" / PROJECT)


def test_parallel_and_serial_runs_give_byte_identical_outputs(tmp_path, fake_registry):
    serial_report, serial = run_in(tmp_path, "serial", fake_registry, 1)
    # Different delays make the stages finish in a different order from the serial one.
    slow = {"mechanical": {"delay": 0.15}, "firmware": {"delay": 0.05}, "redteam": {"delay": 0.1}}
    parallel_report, parallel = run_in(tmp_path, "parallel", fake_registry, 6, slow)

    assert serial.keys() == parallel.keys()
    assert any(p.endswith("interface.json") for p in serial) and "build/mismatches.json" in serial
    for path in serial:
        assert serial[path] == parallel[path], path
    assert [r.stage for r in serial_report.results] == [r.stage for r in parallel_report.results]
    assert [r.key for r in serial_report.results] == [r.key for r in parallel_report.results]


def test_a_cached_parallel_run_matches_too(tmp_path, fake_registry):
    _, serial = run_in(tmp_path, "serial", fake_registry, 1)
    root = tmp_path / "parallel"
    shutil.copytree(
        pytest.importorskip("tests.core.conftest").FIXTURE_SPEC,
        root / "projects" / PROJECT / "spec",
    )
    for _ in range(2):  # the second run restores everything from the cache
        execute(PROJECT, root, registry=fake_registry(), store=root / "store", workers=4)
    assert snapshot(root / "projects" / PROJECT) == serial
