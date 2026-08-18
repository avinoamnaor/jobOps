import { defineConfig, devices } from '@playwright/test'

/**
 * Browser end-to-end tests.
 *
 * These run against a REAL backend and a REAL PostgreSQL database — but a
 * dedicated one (`jobops_e2e`) on dedicated ports, so they can never touch the
 * development database or its documents. `backend/scripts/reset_e2e_db.py`
 * refuses to run against any other database name.
 *
 * Playwright starts both servers itself, so `npm run test:e2e` is the only
 * command needed (PostgreSQL must already be up via `docker compose up -d`).
 */

export const E2E_API_PORT = 8001
export const E2E_WEB_PORT = 5174
export const E2E_DATABASE_URL = 'postgresql+psycopg://jobops:jobops@localhost:5432/jobops_e2e'

export default defineConfig({
  testDir: './e2e',
  // One worker, no parallelism: every test shares one database, and a test that
  // asserts "the list is empty" cannot tolerate another test inserting rows.
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 30_000,
  expect: { timeout: 7_000 },
  reporter: [['list']],
  globalSetup: './e2e/global-setup.ts',

  use: {
    baseURL: `http://localhost:${E2E_WEB_PORT}`,
    trace: 'retain-on-failure',
  },

  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],

  webServer: [
    {
      // Environment variables win over the .env file in pydantic-settings, so
      // this instance uses the E2E database and its own document directory
      // without any change to the committed configuration.
      command: '.venv/Scripts/python.exe -m uvicorn app.main:app --port 8001 --log-level warning',
      cwd: '../backend',
      url: `http://localhost:${E2E_API_PORT}/health`,
      reuseExistingServer: false,
      timeout: 60_000,
      env: {
        DATABASE_URL: E2E_DATABASE_URL,
        DOCUMENTS_ROOT: 'e2e-data/documents',
        CORS_ORIGINS: `http://localhost:${E2E_WEB_PORT}`,
        APP_ENV: 'e2e',
      },
    },
    {
      command: `npm run dev -- --port ${E2E_WEB_PORT} --strictPort`,
      url: `http://localhost:${E2E_WEB_PORT}`,
      reuseExistingServer: false,
      timeout: 60_000,
      env: { VITE_API_BASE_URL: `http://localhost:${E2E_API_PORT}` },
    },
  ],
})
