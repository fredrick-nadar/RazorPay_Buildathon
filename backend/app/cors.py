"""Validated browser-origin policy for the ARGUS HTTP boundary.

The deployed application must never reflect an arbitrary browser origin while
also allowing credentials: that combination lets any site drive an
authenticated ARGUS session. This module owns the whole origin contract so the
application factory stays thin and every rule is testable without a server.

Rules enforced here:

- An origin is ``scheme://host[:port]`` only. A path, query, fragment,
  userinfo, or trailing slash is a configuration error, not something to trim
  silently, because trimming can broaden what the operator wrote.
- Only ``http`` and ``https`` are accepted. ``http`` is accepted for loopback
  hosts always, and for other hosts only so an operator can front ARGUS with a
  local reverse proxy; it is never the default for a public origin.
- Normalization is deterministic and narrowing-safe: the scheme and host are
  lowercased and a redundant default port (``:80`` for http, ``:443`` for
  https) is dropped, because the browser never sends it in ``Origin``. Nothing
  else is rewritten.
- ``*`` is a valid configured value only when credentials are disabled. With
  credentials enabled it is rejected outright rather than downgraded.

With nothing configured, the defaults below cover the local Next.js dev server
and the isolated Playwright frontend port only. They contain no public origin.
"""

from __future__ import annotations

from urllib.parse import urlsplit

__all__ = [
    "DEFAULT_LOCAL_ORIGINS",
    "CorsPolicy",
    "CorsPolicyError",
    "WILDCARD_ORIGIN",
    "build_cors_policy",
    "normalize_origin",
    "parse_origin_list",
]

WILDCARD_ORIGIN = "*"

_ALLOWED_SCHEMES = frozenset({"http", "https"})
_DEFAULT_PORTS = {"http": "80", "https": "443"}
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "[::1]", "::1"})

# Local development (Next.js dev/start on 3000) and the isolated E2E frontend
# port used by frontend/playwright.config.ts. Both loopback spellings are
# listed because a browser sends whichever the operator typed.
DEFAULT_LOCAL_ORIGINS: tuple[str, ...] = (
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:3211",
    "http://127.0.0.1:3211",
)


class CorsPolicyError(ValueError):
    """A configured browser-origin policy that cannot be honoured safely."""


def _is_loopback(host: str) -> bool:
    return host.lower() in _LOOPBACK_HOSTS


def normalize_origin(raw: str) -> str:
    """Return the deterministic normal form of one browser origin.

    Raises :class:`CorsPolicyError` for anything that is not exactly an
    origin. The wildcard is not handled here; see :func:`build_cors_policy`.
    """
    value = raw.strip()
    if not value:
        raise CorsPolicyError("origin is empty")
    if value == WILDCARD_ORIGIN:
        raise CorsPolicyError("the wildcard origin is not a concrete origin")
    if any(ch.isspace() for ch in value):
        raise CorsPolicyError(f"origin contains whitespace: {value!r}")

    split = urlsplit(value)
    if not split.scheme:
        raise CorsPolicyError(f"origin has no scheme (expected http:// or https://): {value!r}")
    scheme = split.scheme.lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise CorsPolicyError(f"origin scheme {scheme!r} is not http or https: {value!r}")
    if split.path:
        raise CorsPolicyError(f"origin must not contain a path: {value!r}")
    if split.query:
        raise CorsPolicyError(f"origin must not contain a query string: {value!r}")
    if split.fragment:
        raise CorsPolicyError(f"origin must not contain a fragment: {value!r}")
    if split.username is not None or split.password is not None:
        raise CorsPolicyError(f"origin must not contain credentials: {value!r}")
    if not split.netloc:
        raise CorsPolicyError(f"origin has no host: {value!r}")

    try:
        hostname = split.hostname
        port = split.port
    except ValueError:  # malformed port, e.g. http://host:notaport
        raise CorsPolicyError(f"origin has an invalid port: {value!r}") from None
    if not hostname:
        raise CorsPolicyError(f"origin has no host: {value!r}")
    if "*" in hostname:
        raise CorsPolicyError(f"wildcard hosts are not supported: {value!r}")

    # urlsplit strips the brackets from an IPv6 literal; put them back so the
    # normal form is comparable to the browser's Origin header.
    host = f"[{hostname}]" if ":" in hostname else hostname.lower()

    if port is None:
        return f"{scheme}://{host}"
    if str(port) == _DEFAULT_PORTS[scheme]:
        return f"{scheme}://{host}"
    return f"{scheme}://{host}:{port}"


def parse_origin_list(raw: str | list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    """Split a comma-separated or already-sequenced origin setting.

    Order is preserved and duplicates removed, so the resulting policy is
    deterministic for a given configuration string.
    """
    if raw is None:
        return ()
    items: list[str] = []
    if isinstance(raw, str):
        items = [part for part in raw.split(",")]
    else:
        for entry in raw:
            items.extend(str(entry).split(","))
    seen: dict[str, None] = {}
    for item in items:
        candidate = item.strip()
        if candidate:
            seen.setdefault(candidate, None)
    return tuple(seen)


class CorsPolicy:
    """The resolved, validated origin policy handed to the CORS middleware."""

    __slots__ = ("allow_credentials", "allow_origins", "source")

    def __init__(
        self, *, allow_origins: tuple[str, ...], allow_credentials: bool, source: str
    ) -> None:
        self.allow_origins = allow_origins
        self.allow_credentials = allow_credentials
        self.source = source

    @property
    def is_wildcard(self) -> bool:
        return self.allow_origins == (WILDCARD_ORIGIN,)

    def safe_summary(self) -> dict[str, object]:
        """Non-secret snapshot. Origins are configuration, never credentials."""
        return {
            "cors_origin_source": self.source,
            "cors_allowed_origins": list(self.allow_origins),
            "cors_allowed_origin_count": len(self.allow_origins),
            "cors_allow_credentials": self.allow_credentials,
            "cors_wildcard": self.is_wildcard,
        }

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, CorsPolicy):
            return NotImplemented
        return (
            self.allow_origins == other.allow_origins
            and self.allow_credentials == other.allow_credentials
            and self.source == other.source
        )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"CorsPolicy(allow_origins={self.allow_origins!r}, "
            f"allow_credentials={self.allow_credentials!r}, source={self.source!r})"
        )


def build_cors_policy(
    configured: str | list[str] | tuple[str, ...] | None,
    *,
    allow_credentials: bool = True,
) -> CorsPolicy:
    """Resolve the effective policy, or raise :class:`CorsPolicyError`.

    ``configured`` empty means "use the safe localhost defaults"; it never
    means "allow everything".
    """
    entries = parse_origin_list(configured)
    if not entries:
        return CorsPolicy(
            allow_origins=DEFAULT_LOCAL_ORIGINS,
            allow_credentials=allow_credentials,
            source="default-localhost",
        )

    if WILDCARD_ORIGIN in entries:
        if len(entries) != 1:
            raise CorsPolicyError(
                "the wildcard origin cannot be combined with explicit origins; "
                "configure either '*' alone or an explicit list"
            )
        if allow_credentials:
            raise CorsPolicyError(
                "the wildcard origin cannot be combined with credentialed requests; "
                "set ARGUS_CORS_ALLOW_CREDENTIALS=false or list explicit origins"
            )
        return CorsPolicy(
            allow_origins=(WILDCARD_ORIGIN,),
            allow_credentials=False,
            source="explicit-wildcard",
        )

    normalized: list[str] = []
    problems: list[str] = []
    for entry in entries:
        try:
            candidate = normalize_origin(entry)
        except CorsPolicyError as exc:
            problems.append(str(exc))
            continue
        if candidate not in normalized:
            normalized.append(candidate)
    if problems:
        raise CorsPolicyError("; ".join(problems))
    if not normalized:  # pragma: no cover - unreachable: problems would be set
        raise CorsPolicyError("no usable origin was configured")

    non_loopback_http = [
        origin
        for origin in normalized
        if origin.startswith("http://") and not _is_loopback(urlsplit(origin).hostname or "")
    ]
    source = "explicit" if not non_loopback_http else "explicit-with-plaintext-origin"
    return CorsPolicy(
        allow_origins=tuple(normalized),
        allow_credentials=allow_credentials,
        source=source,
    )
