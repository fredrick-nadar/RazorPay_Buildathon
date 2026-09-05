"""Release-asset validation: fabricated media must not pass as evidence.

Reproduces and pins the defect that a zero-filled ``.mp4`` and an eight-byte
PNG header were accepted as a demo recording and an application screenshot.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import struct
import sys
from pathlib import Path
from types import ModuleType

import pytest

from tests.unit.release_fixtures import make_iso_bmff, make_jpeg, make_png

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = REPO_ROOT / "scripts"


@pytest.fixture(scope="module")
def assets() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "argus_release_assets", SCRIPTS_DIR / "release_assets.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# The reproduction: the fabricated fixtures the previous gate accepted.
# ---------------------------------------------------------------------------


def test_zero_filled_video_is_rejected(assets: ModuleType, tmp_path: Path) -> None:
    fake = tmp_path / "primary.mp4"
    fake.write_bytes(bytes(128 * 1024))
    with pytest.raises(ValueError, match="invalid box type"):
        assets.validate_iso_bmff_file(fake)


def test_header_only_png_is_rejected(assets: ModuleType) -> None:
    with pytest.raises(ValueError, match="truncated before the IHDR chunk"):
        assets.read_image_dimensions(b"\x89PNG\r\n\x1a\n" + b"synthetic")


# ---------------------------------------------------------------------------
# PNG structure.
# ---------------------------------------------------------------------------


def test_real_png_reports_its_dimensions(assets: ModuleType) -> None:
    assert assets.read_image_dimensions(make_png(800, 480)) == ("png", 800, 480)


def test_png_with_a_corrupt_ihdr_crc_is_rejected(assets: ModuleType) -> None:
    data = bytearray(make_png(800, 480))
    data[29] ^= 0xFF  # flip a CRC byte
    with pytest.raises(ValueError, match="IHDR CRC"):
        assets.validate_png_dimensions(bytes(data))


def test_png_with_zero_dimensions_is_rejected(assets: ModuleType) -> None:
    data = bytearray(make_png(800, 480))
    data[16:24] = struct.pack(">II", 0, 0)
    data[29:33] = struct.pack(">I", zlib_crc(bytes(data[12:29])))
    with pytest.raises(ValueError, match="implausible IHDR dimensions"):
        assets.validate_png_dimensions(bytes(data))


def test_png_whose_first_chunk_is_not_ihdr_is_rejected(assets: ModuleType) -> None:
    data = bytearray(make_png(800, 480))
    data[12:16] = b"IDAT"
    with pytest.raises(ValueError, match="not IHDR"):
        assets.validate_png_dimensions(bytes(data))


def zlib_crc(payload: bytes) -> int:
    import zlib

    return zlib.crc32(payload) & 0xFFFFFFFF


# ---------------------------------------------------------------------------
# JPEG structure.
# ---------------------------------------------------------------------------


def test_real_jpeg_reports_its_dimensions(assets: ModuleType) -> None:
    assert assets.read_image_dimensions(make_jpeg(1024, 640)) == ("jpeg", 1024, 640)


def test_jpeg_without_a_start_of_frame_is_rejected(assets: ModuleType) -> None:
    without_sof = b"\xff\xd8" + b"\xff\xfe" + struct.pack(">H", 4) + b"ab" + b"\xff\xd9"
    with pytest.raises(ValueError, match="no start-of-frame"):
        assets.validate_jpeg_dimensions(without_sof)


def test_jpeg_with_a_malformed_sof_segment_is_rejected(assets: ModuleType) -> None:
    truncated = b"\xff\xd8" + b"\xff\xc0" + struct.pack(">H", 17) + b"\x08\x00"
    with pytest.raises(ValueError, match="truncated start-of-frame"):
        assets.validate_jpeg_dimensions(truncated)


def test_jpeg_with_zero_sof_dimensions_is_rejected(assets: ModuleType) -> None:
    data = bytearray(make_jpeg(1024, 640))
    index = data.index(b"\xff\xc0")
    data[index + 5 : index + 9] = struct.pack(">HH", 0, 0)
    with pytest.raises(ValueError, match="invalid start-of-frame dimensions"):
        assets.validate_jpeg_dimensions(bytes(data))


# ---------------------------------------------------------------------------
# ISO base media container structure.
# ---------------------------------------------------------------------------


def test_real_container_lists_its_top_level_boxes(assets: ModuleType, tmp_path: Path) -> None:
    path = tmp_path / "demo.mp4"
    path.write_bytes(make_iso_bmff(1024 * 1024 + 4096))
    assert assets.validate_iso_bmff_file(path) == ["ftyp", "moov", "mdat"]


def test_container_without_moov_is_rejected(assets: ModuleType, tmp_path: Path) -> None:
    ftyp = struct.pack(">I", 24) + b"ftyp" + b"isom" + b"\x00" * 12
    mdat = struct.pack(">I", 4108) + b"mdat" + b"\x00" * 4100
    path = tmp_path / "no-moov.mp4"
    path.write_bytes(ftyp + mdat)
    with pytest.raises(ValueError, match="no 'moov'"):
        assets.validate_iso_bmff_file(path)


def test_container_whose_boxes_do_not_tile_the_file_is_rejected(
    assets: ModuleType, tmp_path: Path
) -> None:
    path = tmp_path / "ragged.mp4"
    path.write_bytes(make_iso_bmff(200_000) + b"trailing garbage")
    with pytest.raises(ValueError, match="invalid box size|do not tile|truncated box"):
        assets.validate_iso_bmff_file(path)


def test_container_not_starting_with_ftyp_is_rejected(assets: ModuleType, tmp_path: Path) -> None:
    body = struct.pack(">I", 4108) + b"mdat" + b"\x00" * 4100
    path = tmp_path / "no-ftyp.mp4"
    path.write_bytes(body)
    with pytest.raises(ValueError, match="not 'ftyp'"):
        assets.validate_iso_bmff_file(path)


# ---------------------------------------------------------------------------
# Path confinement.
# ---------------------------------------------------------------------------


def test_repository_relative_paths_are_normalized(assets: ModuleType, tmp_path: Path) -> None:
    (tmp_path / "artifacts" / "release").mkdir(parents=True)
    (tmp_path / "artifacts" / "release" / "x.png").write_bytes(b"x")
    assert (
        assets.safe_repo_relative(tmp_path, "artifacts\\release\\x.png")
        == "artifacts/release/x.png"
    )


@pytest.mark.parametrize(
    "bad",
    [
        "/etc/passwd",
        "C:/Windows/System32/config",
        "../outside.png",
        "artifacts/../../outside.png",
        "",
        "   ",
    ],
)
def test_escaping_and_absolute_paths_are_rejected(
    assets: ModuleType, tmp_path: Path, bad: str
) -> None:
    with pytest.raises(assets.ReleasePathError):
        assets.safe_repo_relative(tmp_path, bad)


def test_an_in_repository_parent_link_resolves(assets: ModuleType, tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "ARGUS_CONTROL_PRD.md").write_text("prd", encoding="utf-8")
    assert (
        assets.resolve_within_repo(tmp_path, "docs", "../ARGUS_CONTROL_PRD.md")
        == "ARGUS_CONTROL_PRD.md"
    )


def test_a_link_escaping_the_repository_is_rejected(assets: ModuleType, tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    outside = tmp_path.parent / "outside-secrets.md"
    outside.write_text("secret", encoding="utf-8")
    with pytest.raises(assets.ReleasePathError, match="escapes the repository"):
        assets.resolve_within_repo(tmp_path, "docs", "../../outside-secrets.md")


@pytest.mark.parametrize("bad", ["/etc/passwd", "C:/Windows/win.ini", "", chr(10)])
def test_absolute_or_malformed_link_targets_are_rejected(
    assets: ModuleType, tmp_path: Path, bad: str
) -> None:
    with pytest.raises(assets.ReleasePathError):
        assets.resolve_within_repo(tmp_path, "docs", bad)


# ---------------------------------------------------------------------------
# Hosted-video URLs.
# ---------------------------------------------------------------------------


def test_a_real_looking_https_url_is_accepted(assets: ModuleType) -> None:
    identity, problems = assets.validate_video_url("https://cdn.argus-demo.in/releases/v1.mp4")
    assert problems == []
    assert identity.startswith("https://cdn.argus-demo.in/releases/v1.mp4")


@pytest.mark.parametrize(
    "url",
    [
        "http://cdn.argus-demo.in/v1.mp4",  # not https
        "https://example.com/demo.mp4",  # placeholder host
        "https://localhost/demo.mp4",  # loopback
        "https://127.0.0.1/demo.mp4",  # loopback literal
        "https://10.0.0.4/demo.mp4",  # private range
        "https://user:pw@cdn.argus-demo.in/v1.mp4",  # credentials
        "https://cdn.argus-demo.in/v1.mp4#t=30",  # fragment
        "https://cdn.argus-demo.in",  # no path
        "https://<replace-me>/demo.mp4",  # placeholder token
        "https://cdn.argus-demo.in/TODO",  # placeholder token
        "https://cdn.argus-demo.in/v1.mp4\n",  # control character
        "https://-bad-.host/v1.mp4",  # invalid label
        "",
    ],
)
def test_malformed_or_placeholder_urls_are_rejected(assets: ModuleType, url: str) -> None:
    _identity, problems = assets.validate_video_url(url)
    assert problems, f"expected {url!r} to be rejected"


# ---------------------------------------------------------------------------
# Manifest-level rules.
# ---------------------------------------------------------------------------


def _write_video(root: Path, name: str, *, size: int = 2 * 1024 * 1024, seed: int = 0) -> str:
    path = root / "artifacts" / "release" / "video" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    body = bytearray(make_iso_bmff(size))
    if seed:
        body[-1] = seed & 0xFF  # distinct content, same structure
    path.write_bytes(bytes(body))
    return f"artifacts/release/video/{name}"


def _write_screenshot(root: Path, name: str) -> str:
    path = root / "artifacts" / "release" / "screenshots" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(make_png(1280, 720))
    return f"artifacts/release/screenshots/{name}"


def _write_benchmark(root: Path) -> str:
    path = root / "artifacts" / "benchmark" / "final.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"throughput": 9656.1, "eligible": 1880}), encoding="utf-8")
    return "artifacts/benchmark/final.json"


def _sha(root: Path, relative: str) -> str:
    return hashlib.sha256((root / relative).read_bytes()).hexdigest()


def _manifest(root: Path, **overrides: object) -> dict[str, object]:
    artifact = _write_benchmark(root)
    primary = _write_video(root, "primary.mp4", seed=1)
    backup = _write_video(root, "backup.mp4", seed=2)
    shot = _write_screenshot(root, "dashboard.png")
    manifest: dict[str, object] = {
        "manifest_version": "argus-release-manifest-v1",
        "videos": {
            "primary": {"kind": "file", "path": primary, "sha256": _sha(root, primary)},
            "backup": {"kind": "file", "path": backup, "sha256": _sha(root, backup)},
        },
        "screenshots": [
            {
                "path": shot,
                "source": "dashboard",
                "traceable_artifact": artifact,
                "traceable_values": ["1880"],
            }
        ],
    }
    manifest.update(overrides)
    return manifest


def test_a_structurally_real_manifest_passes(assets: ModuleType, tmp_path: Path) -> None:
    problems, details = assets.validate_manifest(tmp_path, _manifest(tmp_path))
    assert problems == [], problems
    assert len(details["local_video_paths"]) == 2
    assert details["screenshot_paths"] == ["artifacts/release/screenshots/dashboard.png"]


def test_a_video_without_an_owner_recorded_hash_fails(assets: ModuleType, tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    del manifest["videos"]["primary"]["sha256"]  # type: ignore[index]
    problems, _details = assets.validate_manifest(tmp_path, manifest)
    assert any("sha256" in problem for problem in problems)


def test_a_video_whose_hash_does_not_match_fails(assets: ModuleType, tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    manifest["videos"]["primary"]["sha256"] = "0" * 64  # type: ignore[index]
    problems, _details = assets.validate_manifest(tmp_path, manifest)
    assert any("does not match the manifest" in problem for problem in problems)


def test_a_zero_filled_video_file_fails_the_manifest(assets: ModuleType, tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    path = tmp_path / "artifacts" / "release" / "video" / "primary.mp4"
    path.write_bytes(bytes(2 * 1024 * 1024))
    manifest["videos"]["primary"]["sha256"] = _sha(  # type: ignore[index]
        tmp_path, "artifacts/release/video/primary.mp4"
    )
    problems, _details = assets.validate_manifest(tmp_path, manifest)
    assert any("not a valid MP4/MOV container" in problem for problem in problems)


def test_an_undersized_video_fails(assets: ModuleType, tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    relative = _write_video(tmp_path, "primary.mp4", size=200_000)
    manifest["videos"]["primary"] = {  # type: ignore[index]
        "kind": "file",
        "path": relative,
        "sha256": _sha(tmp_path, relative),
    }
    problems, _details = assets.validate_manifest(tmp_path, manifest)
    assert any(
        "below the" in problem and "minimum for a real recording" in problem for problem in problems
    )


def test_identical_locations_with_decorative_extra_fields_still_collide(
    assets: ModuleType, tmp_path: Path
) -> None:
    manifest = _manifest(tmp_path)
    primary = dict(manifest["videos"]["primary"])  # type: ignore[index,arg-type]
    manifest["videos"]["backup"] = {**primary, "note": "same file, extra key"}  # type: ignore[index]
    problems, _details = assets.validate_manifest(tmp_path, manifest)
    assert any("resolve to the same location" in problem for problem in problems)


def test_two_paths_with_identical_content_are_rejected(assets: ModuleType, tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    duplicate = _write_video(tmp_path, "copy.mp4", seed=1)  # same bytes as primary
    manifest["videos"]["backup"] = {  # type: ignore[index]
        "kind": "file",
        "path": duplicate,
        "sha256": _sha(tmp_path, duplicate),
    }
    problems, _details = assets.validate_manifest(tmp_path, manifest)
    assert any("identical content" in problem for problem in problems)


def test_duplicate_hosted_urls_are_rejected(assets: ModuleType, tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    url = {"kind": "url", "url": "https://cdn.argus-demo.in/releases/v1.mp4"}
    manifest["videos"] = {"primary": dict(url), "backup": {**url, "label": "mirror"}}
    problems, _details = assets.validate_manifest(tmp_path, manifest)
    assert any("resolve to the same location" in problem for problem in problems)


def test_a_screenshot_outside_the_repository_is_rejected(
    assets: ModuleType, tmp_path: Path
) -> None:
    manifest = _manifest(tmp_path)
    manifest["screenshots"] = [{"path": "../elsewhere/shot.png"}]  # type: ignore[index]
    problems, _details = assets.validate_manifest(tmp_path, manifest)
    assert any("traverse upwards" in problem for problem in problems)


def test_a_traceable_artifact_outside_the_allowlist_is_rejected(
    assets: ModuleType, tmp_path: Path
) -> None:
    manifest = _manifest(tmp_path)
    other = tmp_path / "notes.md"
    other.write_text("1880", encoding="utf-8")
    manifest["screenshots"][0]["traceable_artifact"] = "notes.md"  # type: ignore[index]
    problems, _details = assets.validate_manifest(tmp_path, manifest)
    assert any("not one of the" in problem for problem in problems)


def test_a_screenshot_value_absent_from_the_artifact_is_rejected(
    assets: ModuleType, tmp_path: Path
) -> None:
    manifest = _manifest(tmp_path)
    manifest["screenshots"][0]["traceable_values"] = ["99999"]  # type: ignore[index]
    problems, _details = assets.validate_manifest(tmp_path, manifest)
    assert any("does not appear in" in problem for problem in problems)


def test_a_tiny_screenshot_is_rejected(assets: ModuleType, tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    path = tmp_path / "artifacts" / "release" / "screenshots" / "dashboard.png"
    path.write_bytes(make_png(64, 48))
    problems, _details = assets.validate_manifest(tmp_path, manifest)
    assert any("below the" in problem for problem in problems)


def test_manifest_local_files_lists_everything_a_clone_needs(
    assets: ModuleType, tmp_path: Path
) -> None:
    _problems, details = assets.validate_manifest(tmp_path, _manifest(tmp_path))
    required = assets.manifest_local_files(details)
    assert assets.RELEASE_MANIFEST_PATH in required
    assert "artifacts/release/screenshots/dashboard.png" in required
    assert "artifacts/release/video/primary.mp4" in required
