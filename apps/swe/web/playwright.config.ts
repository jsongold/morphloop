import { defineConfig, devices } from "@playwright/test";

// E2E for the /v2 GUI against a real dev-stack (run `bash scripts/dev-stack.sh
// up --fake-llm` first; it prints the web port). The test navigates to
// E2E_BASE_URL, so the config never guesses a port.
//
// The stack is stateful (one Postgres, one lab runtime) and the spec walks one
// learner through it in order, so this suite is deliberately single-worker.

const baseURL = process.env.E2E_BASE_URL;
if (!baseURL) {
  throw new Error(
    "E2E_BASE_URL is not set: pass the dev-stack web URL, e.g. E2E_BASE_URL=http://localhost:13402 pnpm test:e2e",
  );
}

export default defineConfig({
  testDir: "./e2e",
  outputDir: "./e2e/artifacts",
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: 0,
  timeout: 180_000,
  expect: { timeout: 15_000 },
  reporter: [["list"]],
  use: {
    ...devices["Desktop Chrome"],
    baseURL,
    headless: true,
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
    video: "off",
  },
});
