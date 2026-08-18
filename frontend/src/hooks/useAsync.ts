import { useCallback, useEffect, useState } from 'react'
import { ApiError } from '../api/client'

export interface AsyncResult<T> {
  data: T | null
  loading: boolean
  error: ApiError | null
  /** Re-run the loader — call after a mutation so the screen shows fresh data. */
  reload: () => void
}

export function asApiError(error: unknown): ApiError {
  if (error instanceof ApiError) return error
  return new ApiError(0, error instanceof Error ? error.message : 'Something went wrong.')
}

/**
 * Load data from the API and track loading/error state.
 *
 * The three things this handles that a bare `useEffect` does not:
 *
 *  1. **Loading and error state** in one place, so every screen behaves alike.
 *  2. **Stale responses.** If the inputs change while a request is in flight,
 *     the old response must not overwrite the new one. The `cancelled` flag in
 *     the cleanup function is what prevents that — React runs the cleanup before
 *     re-running the effect.
 *  3. **Reloading on demand**, by bumping a counter that the effect depends on.
 *
 * `deps` works exactly like the dependency array of `useEffect`: list every
 * value the loader reads, and the data reloads whenever one changes.
 */
export function useAsync<T>(loader: () => Promise<T>, deps: unknown[]): AsyncResult<T> {
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<ApiError | null>(null)
  const [reloadCount, setReloadCount] = useState(0)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)

    loader()
      .then((result) => {
        if (cancelled) return
        setData(result)
        setLoading(false)
      })
      .catch((caught: unknown) => {
        if (cancelled) return
        setError(asApiError(caught))
        setLoading(false)
      })

    return () => {
      cancelled = true
    }
    // `loader` is recreated on every render, so including it would loop forever.
    // The caller's `deps` describe what the loader actually reads.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, reloadCount])

  const reload = useCallback(() => setReloadCount((count) => count + 1), [])

  return { data, loading, error, reload }
}
