import { defineConfig } from '@playwright/test';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
export default defineConfig({
  testDir: './e2e',
  timeout: 240000,
  workers: 1,
  projects: [
    { name: 'development', use: { baseURL: 'http://127.0.0.1:5174' } },
    { name: 'production', use: { baseURL: 'http://127.0.0.1:8001' } },
  ],
  use: {
    baseURL: 'http://127.0.0.1:5174',
    viewport: { width: 1440, height: 1100 },
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    launchOptions: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE
      ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE }
      : {},
  },
  webServer: [
    {
      command: 'uv run --frozen --project .. openfoundry --host 127.0.0.1 --port 8001',
      url: 'http://127.0.0.1:8001/health',
      env: {
        OPENFOUNDRY_HOME: join(tmpdir(), `openfoundry-browser-${process.pid}`),
        OPENFOUNDRY_API_TOKEN: 'e2e-operator',
        OPENFOUNDRY_SANDBOX_MODE: 'guarded',
        OPENFOUNDRY_CORS_ORIGINS: 'http://127.0.0.1:5174',
      },
      timeout: 120000,
    },
    {
      command: 'npm run dev -- --port 5174',
      url: 'http://127.0.0.1:5174',
      env: { OPENFOUNDRY_BACKEND_URL: 'http://127.0.0.1:8001' },
    },
  ],
});
