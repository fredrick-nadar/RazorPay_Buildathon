"""Validation of owner-supplied release evidence: videos, screenshots, paths.

This module is the single boundary the Phase 8 gate uses to decide whether a
submission asset is real. It exists because the first version of the gate
accepted fabricated media: a zero-filled file with an ``.mp4`` name and an
eight-byte PNG header both passed, because only the extension, the size and a
magic prefix were checked.

Design rules:

- Standard library only. No ffmpeg, ffprobe or Pillow is added for this gate.
- Validation is *structural*, not perceptual. We parse the container/format
  headers far enough to prove the file is a real encoded artifact of the
  declared type and has realistic dimensions. We deliberately do not attempt
  to judge what the media depicts, and we never OCR a screenshot.
- Local video formats are narrowed to the ISO base media file format family
  (``.mp4``/``.m4v``/``.mov``) because that family can be validated reliably
  with the standard library. Narrowing the accepted set is honest; pretending
  to validate every container is not.
- A remote video URL is checked for syntax and safety only. Offline validation
  can never prove that a URL is reachable or that it serves the demo; that
  limitation is stated in the gate summary and in the release documentation.
- Every path is reported back as the repository-relative string the owner
  wrote. Absolute machine paths never enter a summary or an artifact.
"""

from __future__ import annotations

import hashlib
import re
import zlib
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

__all__ = [
    "ALLOWED_TRACEABLE_ARTIFACTS",
    "MIN_IMAGE_BYTES",
    "MIN_IMAGE_HEIGHT",
    "MIN_IMAGE_WIDTH",
    "MIN_VIDEO_BYTES",
    "RELEASE_MANIFEST_PATH",
    "RELEASE_MANIFEST_VERSION",
    "VIDEO_SUFFIXES",
    "AssetIdentity",
    "ReleasePathError",
    "file_sha256",
    "manifest_local_files",
    "read_image_dimensions",
    "resolve_within_repo",
    "safe_repo_relative",
    "validate_iso_bmff_file",
    "validate_jpeg_dimensions",
    "validate_manifest",
    "validate_png_dimensions",
    "validate_video_url",
]

RELEASE_MANIFEST_PATH = "artifacts/release/submission-manifest.json"
RELEASE_MANIFEST_VERSION = "argus-release-manifest-v1"

# Only the ISO base media file format family is accepted for a committed video,
# because it is the family this module can genuinely validate with the stdlib.
VIDEO_SUFFIXES = frozenset({".mp4", ".m4v", ".mov"})

# A 4:30-5:00 screen recording is megabytes even at a very low bitrate. One
# mebibyte is far below any real recording and far above any placeholder.
MIN_VIDEO_BYTES = 1024 * 1024

# A screenshot of the control room taken from the running application. Below
# these dimensions nothing in the dashboard would be legible, and a valid image
# at this size cannot be an eight-byte header.
MIN_IMAGE_WIDTH = 640
MIN_IMAGE_HEIGHT = 360
MIN_IMAGE_BYTES = 16 * 1024

# A screenshot may only claim traceability to a measured release artifact.
ALLOWED_TRACEABLE_ARTIFACTS = (
    "artifacts/benchmark/final.json",
    "artifacts/benchmark/final-rules-only.json",
    "artifacts/benchmark/final_summary.md",
    "artifacts/benchmark/public-summary.json",
)

_PLACEHOLDER_TOKENS = (
    "example.com",
    "example.org",
    "example.net",
    "localhost",
    "todo",
    "tbd",
    "replace",
    "placeholder",
    "your-",
    "yourname",
    "xxx",
    "changeme",
    "<",
    ">",
)

_PRIVATE_HOST_PREFIXES = ("127.", "10.", "192.168.", "169.254.", "0.")
_HOSTNAME_LABEL = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_JPEG_SOI = b"\xff\xd8"

# SOF markers carrying frame dimensions. C4 (DHT), C8 (JPG) and CC (DAC) are
# excluded because they are not start-of-frame segments.
_JPEG_SOF_MARKERS = frozenset(
    {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
)
_JPEG_STANDALONE = frozenset({0x01, *range(0xD0, 0xDA)})

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_TOP_LEVEL_BOXES = 512


class ReleasePathError(ValueError):
    """A manifest path that is not a safe repository-relative location."""


class AssetIdentity:
    """Where an asset actually lives, for distinctness comparison.

    Two manifest entries that differ only in decorative extra fields must not
    count as two separate recordings, so identity is derived from the location
    (and, for a committed file, its content hash) rather than the entry dict.
    """

    __slots__ = ("content_hash", "kind", "location")

    def __init__(self, kind: str, location: str, content_hash: str | None = None) -> None:
        self.kind = kind
        self.location = location
        self.content_hash = content_hash

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, AssetIdentity):
            return NotImplemented
        return self.kind == other.kind and self.location == other.location

    def __hash__(self) -> int:
        return hash((self.kind, self.location))

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"AssetIdentity({self.kind!r}, {self.location!r})"


# ---------------------------------------------------------------------------
# Path confinement.
# ---------------------------------------------------------------------------


def safe_repo_relative(repo_root: Path, raw: str) -> str:
    """Return a normalized repository-relative POSIX path, or raise.

    Rejects absolute paths, drive letters, ``..`` traversal, and anything whose
    resolved location escapes the repository (which also catches a symlink that
    points outside). The returned value is what callers must display: no
    absolute machine path ever leaves this function.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise ReleasePathError("path is empty")
    text = raw.strip().replace("\\", "/")
    if any(ord(ch) < 0x20 for ch in text):
        raise ReleasePathError("path contains control characters")
    candidate = Path(text)
    if candidate.is_absolute() or candidate.drive or text.startswith("/"):
        raise ReleasePathError(f"path must be repository-relative: {raw!r}")
    if ".." in candidate.parts:
        raise ReleasePathError(f"path must not traverse upwards: {raw!r}")

    root = repo_root.resolve()
    resolved = (root / candidate).resolve()
    if not resolved.is_relative_to(root):
        raise ReleasePathError(f"path escapes the repository: {raw!r}")
    return resolved.relative_to(root).as_posix()


def resolve_within_repo(repo_root: Path, base_dir: str, target: str) -> str:
    """Resolve a document-relative link and require it to stay in the repository.

    Unlike :func:`safe_repo_relative`, ``..`` is legal here: a link from
    ``docs/architecture.md`` to ``../ARGUS_CONTROL_PRD.md`` is a normal
    repository-internal reference. What is rejected is a target that resolves
    OUTSIDE the repository, which would otherwise "work" merely because an
    unrelated file happens to exist on the machine running the gate.
    """
    if not isinstance(target, str) or not target.strip():
        raise ReleasePathError("link target is empty")
    text = target.strip().replace("\\", "/")
    if any(ord(ch) < 0x20 for ch in text):
        raise ReleasePathError("link target contains control characters")
    candidate = Path(text)
    if candidate.is_absolute() or candidate.drive or text.startswith("/"):
        raise ReleasePathError(f"link target must be repository-relative: {target!r}")

    root = repo_root.resolve()
    resolved = (root / base_dir / candidate).resolve()
    if not resolved.is_relative_to(root):
        raise ReleasePathError(f"link target escapes the repository: {target!r}")
    return resolved.relative_to(root).as_posix()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# Image structure.
# ---------------------------------------------------------------------------


def validate_png_dimensions(data: bytes) -> tuple[int, int]:
    """Parse and validate the PNG signature and IHDR chunk; return (w, h)."""
    if len(data) < 8 or not data.startswith(_PNG_SIGNATURE):
        raise ValueError("not a PNG signature")
    if len(data) < 8 + 25:
        raise ValueError("truncated before the IHDR chunk")
    length = int.from_bytes(data[8:12], "big")
    chunk_type = data[12:16]
    if chunk_type != b"IHDR":
        raise ValueError(f"first chunk is {chunk_type!r}, not IHDR")
    if length != 13:
        raise ValueError(f"IHDR length is {length}, not 13")
    body = data[16:29]
    stored_crc = int.from_bytes(data[29:33], "big")
    if zlib.crc32(chunk_type + body) & 0xFFFFFFFF != stored_crc:
        raise ValueError("IHDR CRC does not match its contents")

    width = int.from_bytes(body[0:4], "big")
    height = int.from_bytes(body[4:8], "big")
    bit_depth = body[8]
    color_type = body[9]
    compression = body[10]
    filter_method = body[11]
    interlace = body[12]
    if width <= 0 or height <= 0 or width > 100_000 or height > 100_000:
        raise ValueError(f"implausible IHDR dimensions {width}x{height}")
    if bit_depth not in (1, 2, 4, 8, 16):
        raise ValueError(f"invalid IHDR bit depth {bit_depth}")
    if color_type not in (0, 2, 3, 4, 6):
        raise ValueError(f"invalid IHDR colour type {color_type}")
    if compression != 0 or filter_method != 0 or interlace not in (0, 1):
        raise ValueError("invalid IHDR compression/filter/interlace fields")
    return width, height


def validate_jpeg_dimensions(data: bytes) -> tuple[int, int]:
    """Walk JPEG markers to a start-of-frame segment; return (w, h)."""
    if len(data) < 4 or not data.startswith(_JPEG_SOI):
        raise ValueError("not a JPEG SOI marker")
    index = 2
    size = len(data)
    while index < size - 1:
        if data[index] != 0xFF:
            raise ValueError("expected a marker prefix byte")
        while index < size and data[index] == 0xFF:
            index += 1  # fill bytes are legal before a marker
        if index >= size:
            break
        marker = data[index]
        index += 1
        if marker in _JPEG_STANDALONE:
            continue
        if marker == 0xD9:  # EOI
            break
        if index + 2 > size:
            raise ValueError("truncated segment length")
        segment_length = int.from_bytes(data[index : index + 2], "big")
        if segment_length < 2:
            raise ValueError(f"invalid segment length {segment_length}")
        if marker in _JPEG_SOF_MARKERS:
            if index + 7 > size:
                raise ValueError("truncated start-of-frame segment")
            height = int.from_bytes(data[index + 3 : index + 5], "big")
            width = int.from_bytes(data[index + 5 : index + 7], "big")
            if width <= 0 or height <= 0:
                raise ValueError(f"invalid start-of-frame dimensions {width}x{height}")
            return width, height
        index += segment_length
        if marker == 0xDA:  # start of scan: entropy-coded data follows
            break
    raise ValueError("no start-of-frame dimensions record found")


def read_image_dimensions(data: bytes) -> tuple[str, int, int]:
    """Return (format, width, height) for a structurally valid PNG or JPEG."""
    if data.startswith(_PNG_SIGNATURE):
        width, height = validate_png_dimensions(data)
        return "png", width, height
    if data.startswith(_JPEG_SOI):
        width, height = validate_jpeg_dimensions(data)
        return "jpeg", width, height
    raise ValueError("not a PNG or JPEG image")


# ---------------------------------------------------------------------------
# Video container structure (ISO base media file format).
# ---------------------------------------------------------------------------


def validate_iso_bmff_file(path: Path) -> list[str]:
    """Walk the top-level boxes of an MP4/MOV file; return the box types.

    Raises ``ValueError`` unless the file starts with a well-formed ``ftyp``
    box, its box sizes tile the file exactly, and it contains both a ``moov``
    (metadata) and a ``mdat`` (media data) box. A zero-filled file fails on the
    very first box.
    """
    size = path.stat().st_size
    if size < 16:
        raise ValueError("file is too small to contain any box")
    types: list[str] = []
    with path.open("rb") as handle:
        offset = 0
        for _ in range(_MAX_TOP_LEVEL_BOXES):
            if offset >= size:
                break
            header = handle.read(8)
            if len(header) < 8:
                raise ValueError(f"truncated box header at offset {offset}")
            box_size = int.from_bytes(header[0:4], "big")
            box_type = header[4:8]
            header_length = 8
            if box_size == 1:
                extended = handle.read(8)
                if len(extended) < 8:
                    raise ValueError(f"truncated 64-bit box size at offset {offset}")
                box_size = int.from_bytes(extended, "big")
                header_length = 16
            elif box_size == 0:
                box_size = size - offset  # box extends to end of file
            if box_size < header_length or offset + box_size > size:
                raise ValueError(f"invalid box size {box_size} at offset {offset}")
            try:
                name = box_type.decode("ascii")
            except UnicodeDecodeError:
                raise ValueError(f"non-ASCII box type at offset {offset}") from None
            if not name.strip() or not all(0x20 <= byte <= 0x7E for byte in box_type):
                raise ValueError(f"invalid box type {box_type!r} at offset {offset}")
            if not types and name != "ftyp":
                raise ValueError(f"first box is {name!r}, not 'ftyp'")
            if name == "ftyp":
                brand = handle.read(4)
                if len(brand) < 4 or not all(0x20 <= byte <= 0x7E for byte in brand):
                    raise ValueError("ftyp box has no readable major brand")
            types.append(name)
            offset += box_size
            handle.seek(offset)
        if offset != size:
            raise ValueError("top-level boxes do not tile the file exactly")
    if "moov" not in types:
        raise ValueError("no 'moov' metadata box")
    if "mdat" not in types:
        raise ValueError("no 'mdat' media data box")
    return types


# ---------------------------------------------------------------------------
# Remote video URLs.
# ---------------------------------------------------------------------------


def validate_video_url(url: object) -> tuple[str, list[str]]:
    """Validate a hosted-video URL offline; return (identity, problems).

    Syntax and safety only. A passing URL is *not* proof that the recording
    exists at the other end; only the owner can confirm that.
    """
    problems: list[str] = []
    if not isinstance(url, str) or not url.strip():
        return "", ["url is missing or empty"]
    # Checked BEFORE stripping: a trailing newline is a control character in
    # the owner's manifest, not whitespace to quietly discard.
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in url):
        return "", ["url contains control characters"]
    raw = url.strip()

    lowered = raw.lower()
    for token in _PLACEHOLDER_TOKENS:
        if token in lowered:
            problems.append(f"url contains the placeholder token {token!r}")
            break

    split = urlsplit(raw)
    if split.scheme.lower() != "https":
        problems.append(f"url scheme must be https, got {split.scheme!r}")
    if split.username or split.password:
        problems.append("url must not embed credentials")
    if split.fragment:
        problems.append("url must not carry a fragment")
    try:
        hostname = split.hostname
        port = split.port
    except ValueError:
        return "", [*problems, "url has an invalid port"]
    if not hostname:
        problems.append("url has no hostname")
    else:
        host = hostname.lower()
        if host.startswith(_PRIVATE_HOST_PREFIXES) or host in ("::1", "[::1]"):
            problems.append(f"url points at a private or loopback host: {host}")
        elif not host.replace(".", "").isdigit():
            labels = host.split(".")
            if len(labels) < 2 or not all(_HOSTNAME_LABEL.match(label) for label in labels):
                problems.append(f"url has an invalid hostname: {host}")
    if len(split.path.strip("/")) < 1:
        problems.append("url has no path identifying the recording")

    identity = ""
    if not problems and hostname:
        suffix = f":{port}" if port else ""
        identity = f"https://{hostname.lower()}{suffix}{split.path}?{split.query}"
    return identity, problems


# ---------------------------------------------------------------------------
# Manifest entries.
# ---------------------------------------------------------------------------


def _validate_video_entry(
    repo_root: Path, label: str, entry: object
) -> tuple[AssetIdentity | None, list[str]]:
    problems: list[str] = []
    if not isinstance(entry, dict):
        return None, [f"{label} video entry is missing or not an object"]
    kind = entry.get("kind")

    if kind == "url":
        identity, url_problems = validate_video_url(entry.get("url"))
        problems.extend(f"{label} video {problem}" for problem in url_problems)
        if problems:
            return None, problems
        return AssetIdentity("url", identity), problems

    if kind != "file":
        return None, [f"{label} video kind must be 'file' or 'url', got {kind!r}"]

    try:
        relative = safe_repo_relative(repo_root, str(entry.get("path") or ""))
    except ReleasePathError as exc:
        return None, [f"{label} video {exc}"]

    path = repo_root.resolve() / relative
    if not path.is_file():
        return None, [f"{label} video file does not exist: {relative}"]
    if path.suffix.lower() not in VIDEO_SUFFIXES:
        problems.append(
            f"{label} video {relative} must be one of "
            f"{sorted(VIDEO_SUFFIXES)} (formats this gate can validate offline)"
        )
        return None, problems

    size = path.stat().st_size
    if size < MIN_VIDEO_BYTES:
        problems.append(
            f"{label} video {relative} is {size} bytes, below the "
            f"{MIN_VIDEO_BYTES}-byte minimum for a real recording"
        )
    try:
        validate_iso_bmff_file(path)
    except (ValueError, OSError) as exc:
        problems.append(f"{label} video {relative} is not a valid MP4/MOV container: {exc}")

    declared = entry.get("sha256")
    if not isinstance(declared, str) or not _SHA256_RE.match(declared.strip().lower()):
        problems.append(
            f"{label} video {relative} has no owner-recorded 64-character sha256 in the manifest"
        )
        return None, problems
    actual = file_sha256(path)
    if actual != declared.strip().lower():
        problems.append(
            f"{label} video {relative} sha256 does not match the manifest "
            f"(file {actual[:12]}..., manifest {declared.strip().lower()[:12]}...)"
        )
    if problems:
        return None, problems
    return AssetIdentity("file", relative, actual), problems


def _validate_screenshot_entry(
    repo_root: Path, index: int, entry: object
) -> tuple[str | None, list[str]]:
    label = f"screenshot[{index}]"
    if not isinstance(entry, dict):
        return None, [f"{label} is not an object"]
    try:
        relative = safe_repo_relative(repo_root, str(entry.get("path") or ""))
    except ReleasePathError as exc:
        return None, [f"{label} {exc}"]

    problems: list[str] = []
    path = repo_root.resolve() / relative
    if not path.is_file():
        return None, [f"{label} does not exist: {relative}"]

    size = path.stat().st_size
    if size < MIN_IMAGE_BYTES:
        problems.append(
            f"{label} {relative} is {size} bytes, below the {MIN_IMAGE_BYTES}-byte "
            "minimum for a real application screenshot"
        )
    try:
        image_format, width, height = read_image_dimensions(path.read_bytes())
    except ValueError as exc:
        problems.append(f"{label} {relative} is not a structurally valid image: {exc}")
        return None, problems
    if width < MIN_IMAGE_WIDTH or height < MIN_IMAGE_HEIGHT:
        problems.append(
            f"{label} {relative} is {width}x{height} {image_format}, below the "
            f"{MIN_IMAGE_WIDTH}x{MIN_IMAGE_HEIGHT} minimum for a legible screenshot"
        )

    artifact_raw = entry.get("traceable_artifact")
    values = entry.get("traceable_values")
    if not isinstance(artifact_raw, str) or not artifact_raw.strip():
        problems.append(f"{label} does not name a traceable_artifact")
        return relative, problems
    try:
        artifact_rel = safe_repo_relative(repo_root, artifact_raw)
    except ReleasePathError as exc:
        problems.append(f"{label} traceable_artifact {exc}")
        return relative, problems
    if artifact_rel not in ALLOWED_TRACEABLE_ARTIFACTS:
        problems.append(
            f"{label} traceable_artifact {artifact_rel} is not one of the "
            f"measured release artifacts {list(ALLOWED_TRACEABLE_ARTIFACTS)}"
        )
        return relative, problems
    artifact_path = repo_root.resolve() / artifact_rel
    if not artifact_path.is_file():
        problems.append(f"{label} traceable_artifact does not exist: {artifact_rel}")
        return relative, problems
    if not isinstance(values, list) or not values:
        problems.append(f"{label} lists no traceable_values")
        return relative, problems
    try:
        artifact_text = artifact_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        problems.append(f"{label} traceable_artifact unreadable: {exc}")
        return relative, problems
    for value in values:
        if str(value) not in artifact_text:
            problems.append(
                f"{label} displays {value!r}, which does not appear in {artifact_rel}"
            )
    return relative, problems


def validate_manifest(repo_root: Path, manifest: object) -> tuple[list[str], dict[str, Any]]:
    """Validate a release manifest; return (problems, non-secret details)."""
    problems: list[str] = []
    details: dict[str, Any] = {
        "screenshot_paths": [],
        "local_video_paths": [],
        "remote_video_count": 0,
    }
    if not isinstance(manifest, dict):
        return ["manifest root is not an object"], details

    if manifest.get("manifest_version") != RELEASE_MANIFEST_VERSION:
        problems.append(
            f"manifest_version must be {RELEASE_MANIFEST_VERSION!r}, "
            f"got {manifest.get('manifest_version')!r}"
        )

    videos = manifest.get("videos")
    identities: dict[str, AssetIdentity] = {}
    if not isinstance(videos, dict):
        problems.append("manifest has no videos object")
    else:
        for label in ("primary", "backup"):
            identity, entry_problems = _validate_video_entry(repo_root, label, videos.get(label))
            problems.extend(entry_problems)
            if identity is not None:
                identities[label] = identity
                if identity.kind == "file":
                    details["local_video_paths"].append(identity.location)
                else:
                    details["remote_video_count"] = int(details["remote_video_count"]) + 1
        primary = identities.get("primary")
        backup = identities.get("backup")
        if primary is not None and backup is not None:
            if primary == backup:
                problems.append(
                    "primary and backup videos resolve to the same location "
                    f"({primary.kind} {primary.location})"
                )
            elif (
                primary.content_hash is not None
                and primary.content_hash == backup.content_hash
            ):
                problems.append(
                    "primary and backup videos are different paths with identical content"
                )

    screenshots = manifest.get("screenshots")
    if not isinstance(screenshots, list) or not screenshots:
        problems.append("manifest lists no screenshots")
    else:
        for index, entry in enumerate(screenshots):
            relative, entry_problems = _validate_screenshot_entry(repo_root, index, entry)
            problems.extend(entry_problems)
            if relative is not None:
                details["screenshot_paths"].append(relative)

    return problems, details


def manifest_local_files(details: dict[str, Any]) -> list[str]:
    """Repository-relative paths the manifest depends on, for tracking checks."""
    paths = [RELEASE_MANIFEST_PATH]
    paths.extend(str(item) for item in details.get("screenshot_paths", []))
    paths.extend(str(item) for item in details.get("local_video_paths", []))
    return paths
