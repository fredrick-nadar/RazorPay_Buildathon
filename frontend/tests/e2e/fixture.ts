import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";

/**
 * Isolated end-to-end fixture.
 *
 * The fixture lives under the gitignored `tmp/` tree and is rebuilt once per
 * run, so a test can never read or mutate a development or demo database. The
 * seeder makes no network calls of any kind.
 *
 * Seeding happens while the Playwright config is being loaded, because the
 * config starts the backend web server before `globalSetup` would run and the
 * backend opens its database at startup. Only the main process seeds; worker
 * processes re-read the same config module and just load the JSON.
 */

const REPO_ROOT = resolve(__dirname, "../../..");

// Inherit the run directory in workers. Never delete another run's evidence.
mkdirSync(join(REPO_ROOT, "tmp"), { recursive: true });
export const E2E_ROOT = process.env.ARGUS_E2E_RUN_ROOT ?? mkdtempSync(join(REPO_ROOT, "tmp", "e2e-"));
process.env.ARGUS_E2E_RUN_ROOT = E2E_ROOT;
export const E2E_DB = join(E2E_ROOT, "e2e.sqlite3");
export const E2E_STAGING = join(E2E_ROOT, "imports");
export const E2E_FIXTURE = join(E2E_ROOT, "fixture.json");

export interface FixtureEntry {
  session_id: string;
  import_id: string | null;
  evidence_id?: string;
}

/** The two persisted runs the seeder executes before the servers start. */
export interface FixtureRuns {
  /** A fully linked run that raises no exceptions at all. */
  clean_run_id: string;
  /** The dev dataset run, which carries the four mandatory exception classes. */
  exception_run_id: string;
}

function venvPython(): string {
  const candidates = [
    join(REPO_ROOT, ".venv", "Scripts", "python.exe"),
    join(REPO_ROOT, ".venv", "bin", "python"),
  ];
  const found = candidates.find((candidate) => existsSync(candidate));
  if (!found) {
    throw new Error(
      `No project virtualenv interpreter found. Looked for:\n  ${candidates.join("\n  ")}`,
    );
  }
  return found;
}

/** True in a Playwright worker rather than the run-owning main process. */
function isWorker(): boolean {
  return process.env.TEST_WORKER_INDEX !== undefined;
}

/** Rebuild the fixture once per run; workers reuse what the main process wrote. */
export function ensureFixture(): void {
  if (isWorker()) return;
  if (existsSync(E2E_FIXTURE)) return;
  mkdirSync(E2E_ROOT, { recursive: true });
  const seeded = execFileSync(
    venvPython(),
    [
      join(REPO_ROOT, "scripts", "seed_local_e2e_fixture.py"),
      "--db",
      E2E_DB,
      "--staging",
      E2E_STAGING,
    ],
    { cwd: REPO_ROOT, encoding: "utf-8" },
  );
  writeFileSync(E2E_FIXTURE, seeded, "utf-8");
}

let cached: Record<string, unknown> | null = null;

function all(): Record<string, unknown> {
  if (cached === null) {
    cached = JSON.parse(readFileSync(E2E_FIXTURE, "utf-8")) as Record<string, unknown>;
  }
  return cached;
}

/** Read one seeded scenario, failing loudly if the fixture is incomplete. */
export function scenario(name: string): FixtureEntry {
  const entry = all()[name] as FixtureEntry | undefined;
  if (!entry) {
    throw new Error(`Fixture is missing the ${name} scenario. Rerun the end-to-end suite.`);
  }
  return entry;
}

/** Read the seeded run identities, failing loudly if the seeder is stale. */
export function runs(): FixtureRuns {
  const entry = all().runs as FixtureRuns | undefined;
  if (!entry?.clean_run_id || !entry.exception_run_id) {
    throw new Error("Fixture is missing seeded run ids. Delete tmp/e2e-* and rerun the suite.");
  }
  return entry;
}

/** Read a seeded scenario that must have a linked import. */
export function withImport(name: string): FixtureEntry & { import_id: string } {
  const entry = scenario(name);
  if (!entry.import_id) throw new Error(`Fixture scenario ${name} has no import id.`);
  return { ...entry, import_id: entry.import_id };
}

/** Read a seeded scenario that must have a linked import and demo record. */
export function linked(name: string): FixtureEntry & { import_id: string; evidence_id: string } {
  const entry = withImport(name);
  if (!entry.evidence_id) throw new Error(`Fixture scenario ${name} has no evidence id.`);
  return { ...entry, evidence_id: entry.evidence_id };
}
