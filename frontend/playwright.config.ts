import { existsSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { defineConfig, devices } from "@playwright/test";
import { E2E_DB, E2E_STAGING, ensureFixture } from "./tests/e2e/fixture";

const REPO_ROOT = resolve(__dirname, "..");

// Must run before the backend web server below, which opens this database at
// startup. globalSetup would run too late.
ensureFixture();

/**
 * End-to-end runs use an ISOLATED database and staging tree, served by a
 * backend this config starts itself on the default API port.
 *
 * The port is the default one because Next resolves rewrites at BUILD time from
 * the routes manifest, so a different origin cannot be selected at start time
 * without rebuilding. Instead, `reuseExistingServer: false` means a backend
 * already listening here fails the run loudly rather than being written to: an
 * existing development API must be stopped before running this suite.
 */
const BACKEND_PORT = 8000;
const FRONTEND_PORT = 3211;
const BACKEND_ORIGIN = `http://127.0.0.1:${BACKEND_PORT}`;

// next start uses baked rewrites. Refuse a build that could route fixture
// actions to a different (possibly development/remote) API.
const routesPath = join(__dirname, ".next", "routes-manifest.json");
if (!existsSync(routesPath)) throw new Error("Build the frontend before running E2E.");
const routes = JSON.parse(readFileSync(routesPath, "utf8"));
const rewrites = Array.isArray(routes.rewrites) ? routes.rewrites : [
  ...(routes.rewrites?.beforeFiles ?? []), ...(routes.rewrites?.afterFiles ?? []),
  ...(routes.rewrites?.fallback ?? []),
];
const apiRewrite = rewrites.find((rule: {source: string}) => rule.source === "/api/:path*");
if (apiRewrite?.destination !== `${BACKEND_ORIGIN}/api/:path*`) {
  throw new Error("E2E requires a build targeting the isolated localhost:8000 API.");
}

const venvPython = existsSync(join(REPO_ROOT, ".venv", "Scripts", "python.exe"))
  ? join(REPO_ROOT, ".venv", "Scripts", "python.exe")
  : join(REPO_ROOT, ".venv", "bin", "python");

export default defineConfig({
  testDir: "tests/e2e",
  timeout: 60_000,
  fullyParallel: false,
  retries: 0,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: `http://127.0.0.1:${FRONTEND_PORT}`,
    trace: "off",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: [
    {
      // Isolated database and staging tree. reuseExistingServer stays false so
      // a development API on this port fails the run instead of being used.
      command: [
        `"${venvPython}"`,
        "-m uvicorn app.main:app --app-dir backend",
        `--host 127.0.0.1 --port ${BACKEND_PORT}`,
      ].join(" "),
      cwd: REPO_ROOT,
      url: `${BACKEND_ORIGIN}/api/v1/health`,
      reuseExistingServer: false,
      timeout: 120_000,
      env: {
        ARGUS_DB_PATH: E2E_DB,
        ARGUS_IMPORT_STAGING_ROOT: E2E_STAGING,
      },
    },
    {
      command: `npm run start -- --port ${FRONTEND_PORT} --hostname 127.0.0.1`,
      url: `http://127.0.0.1:${FRONTEND_PORT}`,
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
});
