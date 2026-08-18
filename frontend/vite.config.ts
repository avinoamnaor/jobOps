/// <reference types="vitest" />
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react()],
  server: {
    // Must match CORS_ORIGINS in the backend's .env.
    port: 5173,
    strictPort: true,
  },
  test: {
    // Our tests cover pure functions and the API client (with a stubbed fetch),
    // so no browser environment is needed.
    environment: 'node',
    globals: true,
    include: ['src/**/*.test.ts'],
  },
})
