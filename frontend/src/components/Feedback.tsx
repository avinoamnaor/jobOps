import type { ReactNode } from 'react'
import type { ApiError } from '../api/client'

export function Loading({ label = 'Loading…' }: { label?: string }) {
  return <p className="muted pad">{label}</p>
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <p className="empty-title">{title}</p>
      {children}
    </div>
  )
}

/**
 * Show an API failure in human terms.
 *
 * Validation errors (422) arrive as a list of field/message pairs, so they are
 * listed individually. Everything else has a single sentence. In no case is a
 * raw exception or JSON blob shown to the user.
 */
export function ErrorBanner({ error, onRetry }: { error: ApiError; onRetry?: () => void }) {
  return (
    <div className="banner banner-error" role="alert">
      <div>
        <strong>{titleFor(error.status)}</strong>
        {error.fieldErrors.length > 0 ? (
          <ul className="error-list">
            {error.fieldErrors.map((fieldError, index) => (
              <li key={`${fieldError.field}-${index}`}>
                <code>{fieldError.field}</code> — {fieldError.message}
              </li>
            ))}
          </ul>
        ) : (
          <p>{error.message}</p>
        )}
      </div>
      {onRetry && (
        <button type="button" className="btn btn-sm" onClick={onRetry}>
          Try again
        </button>
      )}
    </div>
  )
}

function titleFor(status: number): string {
  if (status === 0) return 'Cannot reach the server'
  if (status === 404) return 'Not found'
  if (status === 409) return 'Conflict'
  if (status === 422) return 'Please check the form'
  if (status >= 500) return 'Server error'
  return 'Something went wrong'
}

export function SuccessBanner({ message }: { message: string }) {
  return (
    <div className="banner banner-success" role="status">
      <p>{message}</p>
    </div>
  )
}
