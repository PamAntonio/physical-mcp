// `npm run dev:mock`: the mock backend server + the Next dev server, together.
// The app is pointed at the mock server (not the in-browser mock), so the real
// HTTP + WebSocket client is what's running. Ctrl+C stops both.

import { spawn } from "node:child_process";
import path from "node:path";
import { MOCK_PORT } from "./server"; // importing it starts the mock server

const nextBin = path.resolve("node_modules/next/dist/bin/next");

const app = spawn(process.execPath, [nextBin, "dev", "--turbopack"], {
  stdio: "inherit",
  env: {
    ...process.env,
    NEXT_PUBLIC_USE_MOCK: "false",
    NEXT_PUBLIC_BACKEND_URL: `http://localhost:${MOCK_PORT}`,
    NEXT_PUBLIC_AUTO_START: "true", // open on a live run, same as the in-browser mock
  },
});

// if Next dies, take the mock server down with it, and the other way round
app.on("exit", (code) => process.exit(code ?? 0));

function shutdown() {
  app.kill();
  process.exit(0);
}
process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);
