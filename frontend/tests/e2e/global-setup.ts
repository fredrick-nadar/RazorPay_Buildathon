import { execFile, spawn, type ChildProcess } from "node:child_process";
import { existsSync } from "node:fs";
import { createConnection } from "node:net";
import { join, resolve } from "node:path";
import { E2E_DB, E2E_STAGING } from "./fixture";

const REPO_ROOT = resolve(__dirname, "../../..");
const FRONTEND_ROOT = resolve(__dirname, "../..");
const BACKEND_PORT = Number(process.env.ARGUS_E2E_BACKEND_PORT ?? "8000");
const FRONTEND_PORT = Number(process.env.ARGUS_E2E_FRONTEND_PORT ?? "3211");

function assertPortFree(port: number): Promise<void> {
  return new Promise((resolvePromise, reject) => {
    const socket = createConnection({ host: "127.0.0.1", port });
    socket.setTimeout(750);
    socket.once("connect", () => {
      socket.destroy();
      reject(new Error(`E2E refuses to reuse an existing server on port ${port}.`));
    });
    const free = () => {
      socket.destroy();
      resolvePromise();
    };
    socket.once("error", free);
    socket.once("timeout", free);
  });
}

async function waitForUrl(child: ChildProcess, url: string, label: string): Promise<void> {
  const deadline = Date.now() + 120_000;
  while (Date.now() < deadline) {
    if (child.exitCode !== null) {
      throw new Error(`${label} exited with code ${child.exitCode} before becoming ready.`);
    }
    try {
      const response = await fetch(url, { signal: AbortSignal.timeout(1_500) });
      if (response.ok) return;
    } catch {
      // Startup races are expected; the absolute deadline remains authoritative.
    }
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 250));
  }
  throw new Error(`${label} did not become ready within 120 seconds.`);
}

function taskkill(pid: number): Promise<void> {
  return new Promise((resolvePromise) => {
    execFile(
      "taskkill.exe",
      ["/PID", String(pid), "/T", "/F"],
      { windowsHide: true },
      () => resolvePromise(),
    );
  });
}

async function stopOwnedProcess(child: ChildProcess | null): Promise<void> {
  if (!child?.pid || child.exitCode !== null) return;
  if (process.platform === "win32") {
    await taskkill(child.pid);
    return;
  }
  child.kill("SIGTERM");
  await Promise.race([
    new Promise<void>((resolvePromise) => child.once("exit", () => resolvePromise())),
    new Promise<void>((resolvePromise) => setTimeout(resolvePromise, 5_000)),
  ]);
  if (child.exitCode === null) child.kill("SIGKILL");
}

export default async function globalSetup(): Promise<() => Promise<void>> {
  await assertPortFree(BACKEND_PORT);
  await assertPortFree(FRONTEND_PORT);

  const venvPython = existsSync(join(REPO_ROOT, ".venv", "Scripts", "python.exe"))
    ? join(REPO_ROOT, ".venv", "Scripts", "python.exe")
    : join(REPO_ROOT, ".venv", "bin", "python");
  if (!existsSync(venvPython)) throw new Error("Backend virtual environment is missing.");

  let backend: ChildProcess | null = null;
  let frontend: ChildProcess | null = null;
  const stopAll = async () => {
    await stopOwnedProcess(frontend);
    await stopOwnedProcess(backend);
  };

  try {
    backend = spawn(
      venvPython,
      [
        "-m",
        "uvicorn",
        "app.main:app",
        "--app-dir",
        "backend",
        "--host",
        "127.0.0.1",
        "--port",
        String(BACKEND_PORT),
      ],
      {
        cwd: REPO_ROOT,
        env: {
          ...process.env,
          ARGUS_DB_PATH: E2E_DB,
          ARGUS_IMPORT_STAGING_ROOT: E2E_STAGING,
          ARGUS_AI_PROVIDER: "none",
          ARGUS_GROQ_API_KEY: "",
          GROQ_API_KEY: "",
          LLM_API_KEY: "",
          ARGUS_MODEL_API_KEY: "",
          ARGUS_OPENAI_API_KEY: "",
          OPENAI_API_KEY: "",
          ARGUS_GEMINI_API_KEY: "",
          ARGUS_SARVAM_API_KEY: "",
        },
        shell: false,
        stdio: "inherit",
        windowsHide: true,
      },
    );
    await waitForUrl(
      backend,
      `http://127.0.0.1:${BACKEND_PORT}/api/v1/health`,
      "isolated backend",
    );

    const nextBin = join(FRONTEND_ROOT, "node_modules", "next", "dist", "bin", "next");
    if (!existsSync(nextBin)) throw new Error("Next.js runtime is missing; run npm ci first.");
    frontend = spawn(
      process.execPath,
      [nextBin, "start", "--port", String(FRONTEND_PORT), "--hostname", "127.0.0.1"],
      {
        cwd: FRONTEND_ROOT,
        env: process.env,
        shell: false,
        stdio: "inherit",
        windowsHide: true,
      },
    );
    await waitForUrl(frontend, `http://127.0.0.1:${FRONTEND_PORT}`, "isolated frontend");
  } catch (error) {
    await stopAll();
    throw error;
  }

  return stopAll;
}
