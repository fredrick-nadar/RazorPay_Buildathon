# ARGUS CONTROL — Security and Deployment

The deployment contract this repository actually enforces. Every rule below is
implemented in code and covered by a test; nothing here is aspirational.

Related: [`architecture.md`](architecture.md), [`data-flow.md`](data-flow.md).

## 1. Supported deployment topology

ARGUS is a Buildathon prototype with a deliberately small footprint:

- **one** backend process (`uvicorn app.main:app`);
- **one** frontend process (`next start`, or `next dev` locally);
- **one** SQLite database file and **one** import-staging directory.

There is no queue, cache, object store, container orchestrator or second
database, and none should be added to make the demo work. Scaling the backend
to multiple workers is **not supported** while Telegram is enabled (see §4).

### Local single-process commands

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -r backend/requirements.lock.txt
```

```bash
cd frontend && npm ci
```

```bash
.venv\Scripts\python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

```bash
npm --prefix frontend run dev
```

## 2. Persistent state contract

Exactly two paths hold durable state, and both must survive a restart.

The zero-configuration defaults (`argus.local.sqlite3` and
`artifacts/raw/imports`) sit inside the working directory. That is deliberate
and stays as it is: local development and the demo must work with no
configuration, and both defaults are gitignored. For a **deployed,
production-like instance**, configure both variables explicitly onto one
persistent location that is not ephemeral application or build output
(`.next/`, `__pycache__/`, `dist/`, `build/`, `tmp/`, or any directory recreated
by a deploy). Keep them together: they are one backup and restore unit.

| Setting | Environment variable | Default | Holds |
| --- | --- | --- | --- |
| `db_path` | `ARGUS_DB_PATH` | `argus.local.sqlite3` | Runs, normalized rows, matches, cases, proofs, previews, approvals, audit events, Telegram pairings/offsets. |
| `import_staging_root` | `ARGUS_IMPORT_STAGING_ROOT` | `artifacts/raw/imports` | Immutable source revisions, activation manifests and receipts, run snapshots. |

Enforced behaviour:

- Both settings are validated at construction. An empty value is rejected; a
  `db_path` that is a directory is rejected; an `import_staging_root` that is a
  file is rejected (`Settings._validate_persistence_paths`).
- Startup creates **only** the database's parent directory and the staging root
  (`app.persistence.database.ensure_persistent_parents`). No other directory is
  created and an existing tree is never reshaped, so a mounted volume is safe.
- Schema migration is automatic, ordered and idempotent: every boot creates the
  v1 baseline if needed and then walks the migration chain to the latest
  version inside per-migration `BEGIN IMMEDIATE` transactions. A failed
  migration rolls back completely and raises before any run row can exist.
- The two paths are one unit. The database references immutable revisions in
  the staging tree, so backing up or moving one without the other is a
  configuration error.

The full contract — empty location → migration → synthetic session and run →
clean shutdown → restart on the same paths → state restored with no duplicate
economic effect and no second source activation — is proven offline by
`backend/tests/integration/test_persistent_state_restart.py`, in temporary
directories only.

### Restart procedure

1. Stop the backend process (the lifespan closes the SQLite connection).
2. Leave `ARGUS_DB_PATH` and `ARGUS_IMPORT_STAGING_ROOT` unchanged.
3. Start the backend again. Migration re-runs idempotently; sessions, imports,
   runs and audit history are restored as they were.

### What is immutable and what is not

The staging tree is **not** uniformly append-only, and this matters for backup
correctness:

- **Immutable, append-only**: source revision bytes. A staged raw or canonical
  revision file is written once under a content-addressed name and never
  rewritten; a colliding write with different bytes raises
  `SourceRevisionError` (`session_staging._write_immutable`).
- **Mutable through atomic replacement**: the session manifest
  (`_write_manifest`) and the materialized active canonical files
  (`materialize_active_sources`). Both are replaced in place via
  `os.replace`, so a reader never sees a half-written file — but the *content
  at that path changes* whenever a new revision is activated.

Because of the second group, and because SQLite and the staging tree cannot be
captured as one atomic online snapshot, a recursive copy taken while ARGUS is
running can pair a database row with a manifest from a different moment.

### Backup procedure

No backup framework is bundled, and no online snapshot service is added.

1. **Stop the backend process.** This is required, not advisory: it is the only
   point at which the database and the staging tree are consistent with each
   other.
2. Copy the database with SQLite's own backup API (or, with the process
   stopped, a plain file copy of `argus.local.sqlite3` together with any
   `-wal`/`-shm` sidecar files):

   ```bash
   .venv\Scripts\python -c "import sqlite3,sys;s=sqlite3.connect(sys.argv[1]);d=sqlite3.connect(sys.argv[2]);s.backup(d);d.close();s.close()" argus.local.sqlite3 backup/2026-09-05/argus.sqlite3
   ```

3. Recursively copy the **complete** staging tree into the same backup
   directory.
4. Keep the two together as one labelled backup set (a dated directory holding
   both). A database without its staging tree references revisions that no
   longer exist; a staging tree without its database has no runs, proofs or
   audit history.

### Restore procedure

1. Restore into **new** paths — never over the only live copy. Overwriting the
   running instance's paths is not a supported restore.
2. Validate before starting ARGUS:
   - `PRAGMA integrity_check` returns `ok`;
   - `SELECT value FROM app_meta WHERE key='schema_version'` matches the
     schema version the current code expects (older is fine — migration is
     idempotent and will move it forward; newer means the backup came from a
     later build and must not be used);
   - every session directory in the restored staging tree has a parseable
     `manifest.json` whose active revisions exist on disk.
3. Point `ARGUS_DB_PATH` and `ARGUS_IMPORT_STAGING_ROOT` at the restored pair
   and start the backend. Migration runs idempotently on first boot.
4. Only after the restored instance is verified should the previous copy be
   retired, and it should be retained, not deleted, until then.

## 3. Browser origin policy (CORS)

The application previously reflected **any** `Origin` while allowing
credentials. That is fixed. `backend/app/cors.py` now owns the whole contract:

- With nothing configured, only the local development frontend and the isolated
  Playwright frontend port are allowed:
  `http://localhost:3000`, `http://127.0.0.1:3000`,
  `http://localhost:3211`, `http://127.0.0.1:3211`.
- Production origins are configured explicitly and comma-separated:

  ```bash
  set ARGUS_CORS_ALLOWED_ORIGINS=https://argus.example.com,https://ops.example.com:8443
  ```

- A wildcard origin can **never** be combined with credentials. `*` with
  `ARGUS_CORS_ALLOW_CREDENTIALS` left at its default `true` is rejected at
  startup rather than silently downgraded, and `*` cannot be mixed with
  explicit origins.
- An origin must be exactly `scheme://host[:port]`. A path, trailing slash,
  query, fragment, userinfo, wildcard host, non-http(s) scheme, invalid port or
  embedded whitespace is a configuration error, not something to trim.
- Normalization is deterministic and narrowing-safe: scheme and host are
  lowercased and a redundant default port (`:80` for http, `:443` for https) is
  dropped, because the browser never sends it. Nothing else is rewritten, so a
  policy is never silently broadened.
- `Settings.safe_summary()` exposes the resolved origins, their count, the
  credentials flag and whether the policy is a wildcard. Origins are
  configuration, not secrets; no key value is ever included.

The Next.js `/api/:path*` rewrite is unaffected: it is a server-to-server call
that carries no `Origin` header.

## 4. Telegram deployment boundary

The Telegram channel is **optional and disabled by default**
(`ARGUS_TELEGRAM_ENABLED` unset). It is a stdlib long-polling adapter — no SDK,
no webhook, no tunnel, no public URL, no second service.

**One backend process only.** The channel starts one poller thread inside the
backend process. Running the backend with multiple workers or replicas while
Telegram is enabled would start **competing pollers** against the same bot:
Telegram's `getUpdates` hands each update to whichever poller asked first, so
updates would be split unpredictably across processes and the durable offset in
SQLite would be advanced by racing writers. The supported demo deployment is
therefore a single backend process. A multi-process deployment must first move
this channel into one dedicated worker or add a leader election; ARGUS
deliberately does **not** ship a distributed lock for a prototype.

Failure isolation, all covered by tests:

- With Telegram disabled, no Telegram network call occurs at all — asserted in
  `test_release_rules_only.py` with the transport tripwired.
- An empty/blank `ARGUS_TELEGRAM_BOT_TOKEN` is normalized to "not configured"
  and does not prevent rules-only startup while Telegram is disabled.
- `ARGUS_TELEGRAM_ENABLED=true` without a token is rejected at configuration
  time with a named error, rather than starting a channel that cannot work.
- An unreachable Telegram API degrades **only** the channel: its state becomes
  `DEGRADED` with a failure code, while reconciliation, verification, approval
  and audit continue normally.
- A malformed update is isolated and cannot terminate the poller.

Telegram carries no financial authority: `/approve`, `/apply`, `/resolve` and
`/razorpay` are explicitly refused.

## 5. Rules-only fallback

ARGUS must remain inspectable with no model access. With every model credential
absent, `Settings.rules_only` is true and the deterministic pipeline —
normalization, matching, evidence graph, control totals, verifier, dry-run,
approval, audit — runs unchanged. Cases that would have been investigated stay
`UNRESOLVED` and the run honestly reports `provider_id = "none"` and
`investigation_status = "NOT_INVESTIGATED"`; no provider is ever claimed that
did not run.

`backend/tests/integration/test_release_rules_only.py` proves this offline: it
strips every credential environment variable, disables Telegram, arms tripwires
on the socket layer and on each module's outbound transport, asserts each
tripwire genuinely fires when called, then runs the deterministic synthetic
dataset and requires measured output with zero outbound attempts.

## 6. Secret management

- Secrets are read from environment variables or a gitignored `.env` /
  `.env.local`. `.env.example` contains **names only** and is checked by the
  verifier's secret scan for non-empty values.
- `.gitignore` covers `.env`, `.env.*` (except `.env.example`), `*.pem`,
  `*.key`, `*.sqlite3*`, `tmp/`, `artifacts/raw/` and `artifacts/traces/`.
- Secret-typed settings use `SecretStr`; `safe_summary()` reports only whether
  a key is *configured*, never its value. Secrets never enter logs, prompts,
  fixtures, screenshots, audit events or artifacts.
- `python scripts/verify_phase.py --phase N` runs a repository secret scan and
  a gitignore-coverage check on every phase.

Environment variables, **by name only** (values belong in a gitignored file):

`ARGUS_DB_PATH`, `ARGUS_IMPORT_STAGING_ROOT`, `ARGUS_CORS_ALLOWED_ORIGINS`,
`ARGUS_CORS_ALLOW_CREDENTIALS`, `ARGUS_HOST`, `ARGUS_PORT`, `ARGUS_LOG_LEVEL`,
`ARGUS_AI_PROVIDER`, `ARGUS_AI_TIMEOUT_S`, `ARGUS_AI_PROVIDER_MAX_ATTEMPTS`,
`ARGUS_GROQ_API_KEY`, `ARGUS_GROQ_SCHEMA_MODEL`,
`ARGUS_GROQ_INVESTIGATOR_MODEL`, `ARGUS_GEMINI_API_KEY`,
`ARGUS_OPENAI_API_KEY`, `ARGUS_SARVAM_API_KEY`, `ARGUS_OLLAMA_ENABLED`,
`ARGUS_OLLAMA_MODEL`, `ARGUS_MODEL_PROVIDER`, `ARGUS_MODEL_API_KEY`,
`LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL`,
`ARGUS_INVESTIGATOR_TIMEOUT_S`, `ARGUS_INVESTIGATOR_TURN_TIMEOUT_S`,
`ARGUS_INVESTIGATOR_SAFETY_RESERVE_S`, `ARGUS_INVESTIGATOR_MIN_ATTEMPT_S`,
`ARGUS_INVESTIGATOR_WATCHDOG_GRACE_S`,
`ARGUS_INVESTIGATOR_FALLBACK_RESERVE_S`,
`ARGUS_INVESTIGATOR_REQUIRE_TOOL_CALL`,
`ARGUS_RAZORPAY_KEY_ID`, `ARGUS_RAZORPAY_KEY_SECRET`,
`ARGUS_RAZORPAY_WEBHOOK_SECRET`,
`ARGUS_TELEGRAM_ENABLED`, `ARGUS_TELEGRAM_BOT_TOKEN`,
`ARGUS_TELEGRAM_POLL_TIMEOUT_S`, `ARGUS_TELEGRAM_PAIRING_TTL_S`,
`ARGUS_VOICE_STT_API_KEY`, `ARGUS_VOICE_TTS_API_KEY`,
`ARGUS_BACKEND_ORIGIN` (frontend build-time proxy target).

## 7. Release submission manifest

`python scripts/verify_phase.py --phase 8` requires an owner-supplied manifest
at `artifacts/release/submission-manifest.json`. It is **not** generated by any
tool: it records evidence the owner actually produced. The verifier fails Phase
8 while it is missing, incomplete, or filled with placeholders. Validation is
implemented in `scripts/release_assets.py` and is standard library only — no
ffmpeg, ffprobe or image library is added for this gate.

```json
{
  "manifest_version": "argus-release-manifest-v1",
  "recorded_at_utc": "2026-09-05T00:00:00Z",
  "videos": {
    "primary": {
      "kind": "file",
      "path": "artifacts/release/video/primary-demo.mp4",
      "sha256": "<64 hex characters of the file the owner recorded>"
    },
    "backup": { "kind": "url", "url": "https://<real host>/<real path>" }
  },
  "screenshots": [
    {
      "path": "artifacts/release/screenshots/dashboard-run-summary.png",
      "source": "dashboard",
      "traceable_artifact": "artifacts/benchmark/final.json",
      "traceable_values": ["1880", "9656.1"]
    }
  ]
}
```

**Videos.** `primary` and `backup` are both required and must resolve to
different underlying locations; identical entries decorated with extra fields
are still the same recording and are rejected.

- `kind: "file"` — the path must be repository-relative and confined to the
  repository (no absolute path, no `..`, no symlink escape); the extension must
  be `.mp4`, `.m4v` or `.mov`; the file must be at least 1 MiB; its ISO base
  media container is parsed, so its top-level boxes must begin with a
  well-formed `ftyp`, tile the file exactly, and include both `moov` and
  `mdat`; and the manifest must carry an owner-recorded `sha256` that exactly
  matches the file. Two committed videos must also differ in content hash.
  Only the ISO base media family is accepted, because that is the family this
  gate can genuinely validate offline; a different container should be hosted
  and declared as a URL rather than validated dishonestly.
- `kind: "url"` — parsed with `urlsplit`. It must be absolute `https`, carry a
  valid hostname, have a real path, and contain no credentials, no fragment,
  no control characters, no loopback or private host, and no placeholder token
  (`example.com`, `localhost`, `TODO`, `REPLACE`, `<…>`, …).
  **Offline syntax validation cannot prove that a URL is reachable or that it
  serves the demo.** The gate never fetches it; confirming the link works is an
  owner action.

**Screenshots.** At least one is required. Each must be repository-confined, at
least 16 KiB, and a structurally valid PNG (signature plus a CRC-correct IHDR)
or JPEG (marker walk to a valid start-of-frame record), with dimensions of at
least 640x360. Each must name a `traceable_artifact` from the measured release
set (`artifacts/benchmark/final.json`, `final-rules-only.json`,
`final_summary.md`, `public-summary.json`) and list `traceable_values` that
literally appear in it, so a screenshot cannot display a number no measured
artifact supports. The gate does **not** OCR screenshots; it verifies that the
values the owner declares are backed by a machine artifact.

**Tracking.** The manifest and any local screenshots or videos it references
must be committed. A release asset that exists only in the working tree fails
`release-fresh-checkout-readiness`, because a fresh clone would not receive it.

Until the owner records the primary and backup videos and captures screenshots
from the running application, **Phase 8 is expected to FAIL**. That is correct
behaviour, not a software defect. The gate must not be weakened to pass.

## 8. Known limitations

- Prototype scope: single-process, SQLite-backed, synthetic data only.
- Telegram is offline-verified but still awaits owner live acceptance with a
  real bot; multi-worker deployment is unsupported while it is enabled.
- Razorpay integration is Test Mode read-only. No real money moves and no
  production ERP is written; corrections are always simulated entries.
- Live model providers are optional and unmanaged: no retries beyond the
  configured budget, no cost controls, no provider SLA.
- Voice languages beyond `en-IN`/`hi-IN` are labelled by provider capability,
  not by an ARGUS-passed test pack.
- Frontend `/api` rewrite target is baked at build time; changing it requires a
  rebuild.
- Demo policy values (MDR/GST basis points) are a **synthetic merchant
  policy**, not Razorpay published pricing.
- Release-asset validation is **structural, not perceptual**. The gate proves a
  video is a real ISO base media container of a defensible size with a matching
  owner-recorded hash, and that a screenshot is a real image of legible size
  whose declared values are backed by a measured artifact. It cannot prove what
  the media depicts; that judgement stays with the owner and the reviewer.
- A hosted demo-video URL is validated for syntax and safety offline only. The
  gate never fetches it, so it cannot prove the recording is reachable.
- Backup consistency requires stopping the backend. There is no online snapshot
  facility, and none should be added for a prototype of this size.
