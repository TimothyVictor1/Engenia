"""File listing, zip downloads and the release download."""

import io
import os
import zipfile

import pytest

from engenia.dashboard.schemas import FilesResponse, ReleaseResponse
from tests.dashboard.conftest import PROJECT

PROJECT_HASH = "abcdef012345"


@pytest.fixture
def built(project):
    mechanical = project / "build" / "mechanical"
    (mechanical / "cad").mkdir(parents=True, exist_ok=True)
    (mechanical / "cad" / "hull.stl").write_bytes(b"solid hull\nendsolid hull\n")
    (mechanical / "hull.step").write_bytes(b"ISO-10303-21;")
    (mechanical / "notes.txt").write_text("hello")
    (mechanical / "data.csv").write_text("a,b\n1,2\n")
    (project / "src" / "mechanical").mkdir(parents=True)
    (project / "src" / "mechanical" / "model.py").write_text("WALL = 3\n")
    return project


def names(archive: bytes) -> set[str]:
    return set(zipfile.ZipFile(io.BytesIO(archive)).namelist())


# ---------------------------------------------------------------- listing


def test_lists_every_file_with_size_type_and_time(built, client):
    response = client.get("/api/files/mechanical")
    assert response.status_code == 200
    body = FilesResponse.model_validate(response.json()).model_dump()
    assert body["stage"] == "mechanical"
    by_path = {f["path"]: f for f in body["files"]}
    assert {"cad/hull.stl", "hull.step", "notes.txt", "data.csv", "interface.json",
            "verdict.json"} <= set(by_path)
    assert by_path["cad/hull.stl"] | {} == {
        "path": "cad/hull.stl", "name": "hull.stl", "size": 25, "type": "stl",
        "modified": by_path["cad/hull.stl"]["modified"],
    }
    assert by_path["hull.step"]["type"] == "step"
    assert by_path["data.csv"]["type"] == "csv"
    assert by_path["interface.json"]["type"] == "json"
    assert by_path["notes.txt"]["type"] == "text"
    assert by_path["notes.txt"]["modified"].endswith("+00:00")
    assert [f["path"] for f in body["files"]] == sorted(f["path"] for f in body["files"])


def test_unbuilt_stage_lists_nothing(built, client):
    assert client.get("/api/files/firmware").json() == {"stage": "firmware", "files": []}


def test_unknown_stage_is_refused(client):
    assert client.get("/api/files/nope").status_code == 404


def test_listing_never_follows_symlinks(built, client):
    secret = built / "build" / "electronics" / "private.json"
    secret.parent.mkdir(parents=True, exist_ok=True)
    secret.write_text("{}")
    outside = built.parent / "outside"
    outside.mkdir()
    (outside / "x.txt").write_text("secret")
    mechanical = built / "build" / "mechanical"
    (mechanical / "link.json").symlink_to(secret)
    (mechanical / "linkdir").symlink_to(outside, target_is_directory=True)
    listed = {f["path"] for f in client.get("/api/files/mechanical").json()["files"]}
    assert "link.json" not in listed and not any(p.startswith("linkdir") for p in listed)


def test_symlinked_build_directory_lists_nothing(built, client):
    build = built / "build"
    elsewhere = built.parent / "elsewhere"
    build.rename(elsewhere)
    build.symlink_to(elsewhere, target_is_directory=True)
    assert client.get("/api/files/mechanical").json()["files"] == []
    assert client.get("/api/download/mechanical.zip").status_code == 404
    leaked = names(client.get("/api/download/all.zip").content)
    assert not any(n.startswith("build/") for n in leaked)


# ---------------------------------------------------------------- zips


def test_stage_zip_holds_the_stage_files(built, client):
    response = client.get("/api/download/mechanical.zip")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    disposition = f'attachment; filename="{PROJECT}-mechanical.zip"'
    assert response.headers["content-disposition"] == disposition
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    assert archive.testzip() is None
    assert {"mechanical/cad/hull.stl", "mechanical/hull.step", "mechanical/notes.txt"} <= set(
        archive.namelist()
    )
    assert archive.read("mechanical/cad/hull.stl") == b"solid hull\nendsolid hull\n"
    assert not any(n.startswith("electronics/") for n in archive.namelist())


def test_all_zip_holds_build_and_src(built, client):
    response = client.get("/api/download/all.zip")
    assert response.status_code == 200
    found = names(response.content)
    assert "build/mechanical/hull.step" in found
    assert "build/redteam/scenarios/storm.yaml" in found
    assert "src/mechanical/model.py" in found


def test_zip_leaves_out_symlinks(built, client):
    secret = built.parent / "secret.txt"
    secret.write_text("private")
    (built / "build" / "mechanical" / "escape.txt").symlink_to(secret)
    for url in ("/api/download/mechanical.zip", "/api/download/all.zip"):
        content = client.get(url).content
        assert not any("escape" in n for n in names(content))
        assert b"private" not in content


def test_zip_of_an_unbuilt_stage_is_not_found(built, client):
    assert client.get("/api/download/firmware.zip").status_code == 404


@pytest.mark.parametrize(
    "url",
    [
        "/api/download/nope.zip",
        "/api/download/mechanical",
        "/api/download/mechanical.tar",
        "/api/download/..%2f..%2fetc.zip",
        "/api/download/%2e%2e.zip",
        "/api/download/..%5c..%5cetc.zip",
        "/api/download/mechanical%00.zip",
        "/api/files/..",
        "/api/files/%2e%2e",
        "/api/files/..%2f..",
        "/api/files/mechanical%00",
    ],
)
def test_traversal_and_unknown_names_are_refused(built, client, url):
    assert client.get(url).status_code in {404, 422}


def test_downloading_does_not_write_into_the_project(built, client):
    def tree():
        return sorted(
            (str(p), p.stat().st_mtime_ns) for p in built.parents[1].rglob("*") if p.is_file()
        )
    before = tree()
    client.get("/api/download/all.zip")
    client.get("/api/files/mechanical")
    assert tree() == before
    assert client.post("/api/download/all.zip").status_code == 405


# ---------------------------------------------------------------- release


def make_release(root, name, mtime=None):
    path = root / "release" / name
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(b"PK-release-" + name.encode())
    if mtime:
        os.utime(path, (mtime, mtime))
    return path


def test_no_release_yet(client):
    body = ReleaseResponse.model_validate(client.get("/api/release").json())
    assert body.available is False
    assert client.get("/api/download/release").status_code == 404


def test_release_is_offered_when_the_zip_exists(project, client):
    root = project.parents[1]
    make_release(root, f"{PROJECT}-{PROJECT_HASH}.zip")
    body = ReleaseResponse.model_validate(client.get("/api/release").json())
    assert body.available and body.name == f"{PROJECT}-{PROJECT_HASH}.zip" and body.size
    response = client.get("/api/download/release")
    assert response.status_code == 200
    assert response.content == b"PK-release-" + body.name.encode()
    assert "attachment" in response.headers["content-disposition"]


def test_release_picks_the_newest_and_ignores_other_names(project, client):
    root = project.parents[1]
    make_release(root, f"{PROJECT}-aaaaaaaaaaaa.zip", mtime=1_000_000_000)
    make_release(root, f"{PROJECT}-bbbbbbbbbbbb.zip", mtime=1_900_000_000)
    make_release(root, "other-cccccccccccc.zip", mtime=2_000_000_000)
    make_release(root, f"{PROJECT}-notahexvalue.zip", mtime=2_100_000_000)
    make_release(root, f"{PROJECT}-dddddddddddd.zip.part", mtime=2_200_000_000)
    assert client.get("/api/release").json()["name"] == f"{PROJECT}-bbbbbbbbbbbb.zip"


def test_release_symlink_is_not_served(project, client):
    root = project.parents[1]
    outside = root.parent / "outside.zip"
    outside.write_bytes(b"x")
    (root / "release").mkdir()
    (root / "release" / f"{PROJECT}-{PROJECT_HASH}.zip").symlink_to(outside)
    assert client.get("/api/release").json() == {
        "available": False, "name": None, "size": None, "modified": None,
    }
