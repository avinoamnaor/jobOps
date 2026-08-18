/**
 * Small pure formatting helpers.
 *
 * Pure functions with no React and no network, which makes them trivial to test
 * — see format.test.ts.
 */

/** Words that should not be title-cased the ordinary way. */
const SPECIAL_CASES: Record<string, string> = {
  hr: 'HR',
  cv: 'CV',
  url: 'URL',
  linkedin: 'LinkedIn',
}

/**
 * Turn a backend token into something readable.
 *
 * `hr_interview` -> `HR Interview`, `take_home` -> `Take Home`.
 */
export function humanize(token: string | null | undefined): string {
  if (!token) return ''
  return token
    .split('_')
    .map((word) => SPECIAL_CASES[word] ?? word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ')
}

/** Dates arrive from the API as UTC ISO strings and are shown in local time. */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return '—'
  return date.toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return '—'
  return date.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })
}

export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

/** A short label for a document: its human label, else its filename. */
export function documentName(document: {
  label: string | null
  original_filename: string | null
  id: number
}): string {
  return document.label || document.original_filename || `Document ${document.id}`
}

/**
 * Convert a form field to what the API should receive.
 *
 * An untouched optional text input holds "", but the backend columns are
 * nullable — so empty strings become null rather than being stored as blanks.
 */
export function emptyToNull(value: string): string | null {
  const trimmed = value.trim()
  return trimmed === '' ? null : trimmed
}
