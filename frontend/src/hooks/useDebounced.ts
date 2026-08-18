import { useEffect, useState } from 'react'

/**
 * Return `value`, but only after it has stopped changing for `delayMs`.
 *
 * Used by the search box: without this, typing "programmatic" would fire twelve
 * requests. With it, the request happens once you pause.
 */
export function useDebounced<T>(value: T, delayMs = 300): T {
  const [debounced, setDebounced] = useState(value)

  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delayMs)
    // Cleanup cancels the pending timer whenever `value` changes again, which is
    // what makes the delay restart on every keystroke.
    return () => clearTimeout(timer)
  }, [value, delayMs])

  return debounced
}
