// pm2 process graph for the ops stack.
//
// Usage:
//   pm2 start ecosystem.config.js --only dashboard-api
//   pm2 start ecosystem.config.js                  # start everything
//   pm2 scale worker-x 10                          # ramp X workers up/down on the fly
//   pm2 logs dashboard-api
//   pm2 stop all / pm2 restart all / pm2 delete all
//
// On Windows, run in a terminal that has pm2 on PATH (npm i -g pm2).
// Windows venv layout is Scripts\; POSIX is bin/. We detect via platform.

const path = require("path");
const OPS = path.resolve(__dirname, "ops");
const VENV_BIN = process.platform === "win32"
  ? path.join(OPS, ".venv", "Scripts")
  : path.join(OPS, ".venv", "bin");
const PY = path.join(VENV_BIN, process.platform === "win32" ? "python.exe" : "python");
const UVICORN = path.join(VENV_BIN, process.platform === "win32" ? "uvicorn.exe" : "uvicorn");

const commonEnv = {
  PYTHONPATH: OPS,
  OPS_LOG_LEVEL: "INFO",
  // SQLite file at ops/ops.db (pm2's cwd is OPS). Override with an absolute
  // path in production: sqlite+aiosqlite:////abs/path/ops.db (four slashes).
  OPS_DATABASE_URL: "sqlite+aiosqlite:///./ops.db",
  OPS_REDIS_URL: "redis://localhost:6379/0",
  // Raise when scaling worker count beyond ~20.
  OPS_REDIS_MAX_CONNECTIONS: "100",
};

module.exports = {
  apps: [
    // ---- Control plane ---- //
    {
      name: "dashboard-api",
      script: UVICORN,
      args: [
        "dashboard.api.main:app",
        "--host", "0.0.0.0",
        "--port", "8000",
        "--workers", "1",          // single worker until M1 WS fan-out lands
        "--proxy-headers",
      ],
      cwd: OPS,
      env: { ...commonEnv, OPS_SERVICE_NAME: "dashboard-api" },
      autorestart: true,
      max_memory_restart: "512M",
      out_file: path.join(__dirname, "logs", "dashboard-api.out.log"),
      error_file: path.join(__dirname, "logs", "dashboard-api.err.log"),
      merge_logs: true,
      time: true,
    },

    // ---- Fleet services (stubs today; real code lands M3/M4/M13/M10) ---- //
    //
    // Each block is kept here but disabled (script=":" or commented) until
    // the corresponding milestone ships the module. Uncomment + `pm2 reload`
    // as we go.
    //
    // proxy-pool currently runs in-process inside dashboard-api (M3).
    // Uncomment + update script/args if you want to split it out on its
    // own host; see docs/ops_proxies.md §Scaling beyond one process.
    // {
    //   name: "proxy-pool",
    //   script: PY, args: ["-m", "proxy_pool.service"],
    //   cwd: OPS, env: { ...commonEnv, OPS_SERVICE_NAME: "proxy-pool" },
    //   autorestart: true, max_memory_restart: "256M",
    // },
    //
    // {
    //   name: "account-pool",
    //   script: PY, args: ["-m", "account_pool.service"],
    //   cwd: OPS, env: { ...commonEnv, OPS_SERVICE_NAME: "account-pool" },
    //   autorestart: true, max_memory_restart: "256M",
    // },
    //
    // Scraper workers (M5). One Chromium per process, new context per task.
    // Scale with:  pm2 scale worker 20
    // Prerequisite: `playwright install chromium` (ops/.venv).
    // Each worker picks tasks from Redis stream `scrape:tasks` via the
    // consumer group `workers`, so N processes coordinate naturally.
    // Filter by source at the task level (strategist decides); the worker
    // itself is source-agnostic and runs whichever scraper plugin is
    // registered for the task's source.
    {
      name: "worker",
      script: PY, args: ["-m", "worker"],
      cwd: OPS,
      env: { ...commonEnv, OPS_SERVICE_NAME: "worker" },
      instances: 1,              // `pm2 scale worker N` to horizontally scale
      exec_mode: "fork",
      autorestart: true,
      max_memory_restart: "1G",  // Playwright creep → recycle
      kill_timeout: 15000,       // graceful shutdown window
      out_file: path.join(__dirname, "logs", "worker.out.log"),
      error_file: path.join(__dirname, "logs", "worker.err.log"),
      merge_logs: true,
      time: true,
    },
    //
    // {
    //   name: "strategist",
    //   script: PY, args: ["-m", "strategist.service"],
    //   cwd: OPS, env: { ...commonEnv, OPS_SERVICE_NAME: "strategist" },
    //   autorestart: true, max_memory_restart: "256M",
    // },
    //
    // {
    //   name: "self-validator",
    //   script: PY, args: ["-m", "self_validator.service"],
    //   cwd: OPS, env: { ...commonEnv, OPS_SERVICE_NAME: "self-validator" },
    //   autorestart: true, max_memory_restart: "512M",
    // },
    //
    // // Pipeline orchestrator (M2.5) — runs as part of dashboard-api today,
    // // but if you outgrow that you can split it out:
    // {
    //   name: "pipeline-orchestrator",
    //   script: PY, args: ["-m", "pipeline.orchestrator_service"],
    //   cwd: OPS, env: { ...commonEnv, OPS_SERVICE_NAME: "pipeline-orchestrator" },
    //   autorestart: true, max_memory_restart: "512M",
    // },
  ],
};
