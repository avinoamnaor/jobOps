import { execFileSync } from 'node:child_process'
import type { APIRequestContext, Page } from '@playwright/test'
import { expect } from '@playwright/test'
import { E2E_API_PORT, E2E_DATABASE_URL } from '../playwright.config'
import { BACKEND_ROOT, PYTHON } from './global-setup'

export const API_BASE = `http://localhost:${E2E_API_PORT}`

/** Empty the E2E database and its document directory between tests. */
export function resetDatabase() {
  execFileSync(PYTHON, ['scripts/reset_e2e_db.py', '--fast'], {
    cwd: BACKEND_ROOT,
    env: { ...process.env, E2E_DATABASE_URL },
    stdio: 'pipe',
  })
}

/** A small but valid-looking PDF, with distinct bytes per call. */
export function fakePdf(marker: string): Buffer {
  return Buffer.from(
    `%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n${marker}`,
  )
}

/** Create an application straight through the API (for list/pagination setup). */
export async function createApplicationViaApi(
  request: APIRequestContext,
  body: Record<string, unknown>,
) {
  const response = await request.post(`${API_BASE}/applications`, { data: body })
  expect(response.status()).toBe(201)
  return response.json()
}

/** Upload a document straight through the API. */
export async function uploadDocumentViaApi(
  request: APIRequestContext,
  options: { name: string; kind: string; content: Buffer; label?: string },
) {
  const response = await request.post(`${API_BASE}/documents`, {
    multipart: {
      file: { name: options.name, mimeType: 'application/pdf', buffer: options.content },
      kind: options.kind,
      ...(options.label ? { label: options.label } : {}),
    },
  })
  expect([200, 201]).toContain(response.status())
  return response.json()
}

/**
 * Collect console errors and page exceptions for the lifetime of a test.
 *
 * Used to assert that normal flows produce a clean console — React key warnings
 * and unhandled promise rejections show up here.
 */
export function collectConsoleProblems(page: Page): string[] {
  const problems: string[] = []
  page.on('console', (message) => {
    if (message.type() === 'error' || message.type() === 'warning') {
      problems.push(`${message.type()}: ${message.text()}`)
    }
  })
  page.on('pageerror', (error) => problems.push(`pageerror: ${error.message}`))
  return problems
}

/** Console noise that is expected and not a defect. */
export function ignorableConsoleNoise(entry: string): boolean {
  return (
    // Vite's dev-server chatter.
    entry.includes('[vite]') ||
    entry.includes('Download the React DevTools') ||
    // Deliberate failed-request tests log a browser-level network error we
    // cannot suppress; the assertions check the UI handled it.
    entry.includes('Failed to load resource')
  )
}
