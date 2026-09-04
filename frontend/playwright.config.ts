import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { defineConfig, devices } from "@playwright/test";
import { ensureFixture } from "./tests/e2e/fixture";

// Must run before global setup opens this database.
ensureFixture();

/**
 * End-to-end runs use an ISOLATED database and staging tree, served by a
 * backend this config starts itself on a build-matched isolated API port.
 *
 * Next resolves rewrites at BUILD time from the routes manifest, so a different
 * origin cannot be selected at start time without rebuilding. The configurable
 * port supports an isolated build when the development API is already running.
 * `reuseExistingServer: false` still prevents writing to an existing process.
 */
const BACKEND_PORT = Number(process.env.ARGUS_E2E_BACKEND_PORT ?? "8000");
const FRONTEND_PORT = Number(process.env.ARGUS_E2E_FRONTEND_PORT ?? "3211");
const BACKEND_ORIGIN = `http://127.0.0.1:${BACKEND_PORT}`;
process.env.ARGUS_E2E_BACKEND_ORIGIN = BACKEND_ORIGIN;

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
  throw new Error(`E2E requires a build targeting the isolated ${BACKEND_ORIGIN} API.`);
}

export default defineConfig({
  testDir: "tests/e2e",
  globalSetup: "./tests/e2e/global-setup.ts",
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
});
