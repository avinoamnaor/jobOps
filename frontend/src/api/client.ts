/**
 * The single place in the frontend that calls `fetch`.
 *
 * Everything else imports typed functions from `api/applications.ts` and
 * `api/documents.ts`. Keeping network code in one module means URL building,
 * headers, and — most importantly — error translation are written once and
 * behave identically everywhere.
 */

// `import.meta.env` is Vite's build-time environment. Only VITE_-prefixed
// variables are exposed to browser code.
const BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000').replace(/\/+$/, '')

export interface FieldError {
  field: string
  message: string
}

/**
 * One error type for every failure, so components never have to guess whether
 * they are holding a network error, an HTTP error, or a validation error.
 */
export class ApiError extends Error {
  readonly status: number
  readonly fieldErrors: FieldError[]

  constructor(status: number, message: string, fieldErrors: FieldError[] = []) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.fieldErrors = fieldErrors
  }
}

function defaultMessageFor(status: number): string {
  switch (status) {
    case 400:
      return 'The request was rejected as invalid.'
    case 404:
      return 'Not found. It may have been deleted.'
    case 409:
      return 'That conflicts with the current state.'
    case 413:
      return 'That file is too large.'
    case 422:
      return 'Some of the values sent were not valid.'
    case 500:
      return 'The server ran into a problem completing that.'
    default:
      return `Request failed (HTTP ${status}).`
  }
}

interface RawValidationItem {
  loc?: unknown
  msg?: unknown
}

/**
 * Turn a FastAPI error body into an ApiError.
 *
 * The backend produces two different shapes and both need handling:
 *   1. our own handlers:  {"detail": "Application 5 does not exist"}
 *   2. Pydantic:          {"detail": [{"loc": ["body", "company_name"], "msg": "..."}]}
 *
 * Shape 2 becomes per-field messages so the form can show them next to the
 * offending input instead of dumping a JSON blob on the user.
 */
export async function toApiError(response: Response): Promise<ApiError> {
  let payload: unknown = null
  try {
    payload = await response.json()
  } catch {
    // A non-JSON error body (a proxy error page, say). Fall through.
  }

  const detail = (payload as { detail?: unknown } | null)?.detail

  if (typeof detail === 'string' && detail.trim()) {
    return new ApiError(response.status, detail)
  }

  if (Array.isArray(detail)) {
    const fieldErrors: FieldError[] = detail.map((item: RawValidationItem) => {
      const location = Array.isArray(item.loc) ? item.loc.map(String) : []
      // Drop the leading "body"/"query" segment; what remains is the field name.
      const field = location.slice(1).join('.') || location.join('.') || 'request'
      return { field, message: String(item.msg ?? 'Invalid value') }
    })

    const summary =
      fieldErrors.length === 1
        ? `${fieldErrors[0].field}: ${fieldErrors[0].message}`
        : `${fieldErrors.length} fields need attention.`

    return new ApiError(response.status, summary, fieldErrors)
  }

  return new ApiError(response.status, defaultMessageFor(response.status))
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)

  // FormData must NOT get an explicit Content-Type: the browser needs to set it
  // itself so it can include the multipart boundary. Setting it by hand here is
  // a classic cause of mysteriously failing uploads.
  if (init.body && !(init.body instanceof FormData)) {
    headers.set('Content-Type', 'application/json')
  }

  let response: Response
  try {
    response = await fetch(`${BASE_URL}${path}`, { ...init, headers })
  } catch {
    // fetch only rejects when the request never completed: server down, DNS
    // failure, CORS block. Worth its own message, because "failed to fetch"
    // tells the user nothing actionable.
    throw new ApiError(
      0,
      `Cannot reach the JobOps API at ${BASE_URL}. Is the backend running?`,
    )
  }

  if (!response.ok) {
    throw await toApiError(response)
  }

  if (response.status === 204) {
    return undefined as T
  }

  return (await response.json()) as T
}

/** Build a query string, skipping empty values so we never send `?q=`. */
export function buildQuery(params: Record<string, string | number | undefined | null>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') {
      search.set(key, String(value))
    }
  }
  const query = search.toString()
  return query ? `?${query}` : ''
}

export function apiUrl(path: string): string {
  return `${BASE_URL}${path}`
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) }),
  postForm: <T>(path: string, form: FormData) =>
    request<T>(path, { method: 'POST', body: form }),
  patch: <T>(path: string, body: unknown) =>
    request<T>(path, { method: 'PATCH', body: JSON.stringify(body) }),
  put: <T>(path: string, body: unknown) =>
    request<T>(path, { method: 'PUT', body: JSON.stringify(body) }),
  delete: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
}
