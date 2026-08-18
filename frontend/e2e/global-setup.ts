import { execFileSync } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { E2E_DATABASE_URL } from '../playwright.config'

const HERE = path.dirname(fileURLToPath(import.meta.url))
export const BACKEND_ROOT = path.resolve(HERE, '../../backend')
export const PYTHON = path.join(BACKEND_ROOT, '.venv', 'Scripts', 'python.exe')

/**
 * Create the E2E database if needed, run migrations, and empty it.
 *
 * Runs once before the whole suite. Building the schema with the real Alembic
 * migrations (rather than creating tables directly) means the browser tests also
 * confirm the migrations produce a working database.
 */
export default function globalSetup() {
  execFileSync(PYTHON, ['scripts/reset_e2e_db.py'], {
    cwd: BACKEND_ROOT,
    env: { ...process.env, E2E_DATABASE_URL },
    stdio: 'inherit',
  })
}
