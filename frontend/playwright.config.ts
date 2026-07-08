import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  webServer: {
    command: "npm run dev",
    url: "http://localhost:5173",
    reuseExistingServer: !process.env.CI,
  },
  use: { baseURL: "http://localhost:5173" },
  projects: [
    { name: "mocked", testMatch: /.*\.mocked\.spec\.ts/ },   // default CI lane, /api/* intercepted
    { name: "smoke", testMatch: /.*\.smoke\.spec\.ts/ },      // real backend, run manually/nightly
  ],
});
