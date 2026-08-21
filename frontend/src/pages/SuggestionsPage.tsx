import { useState } from 'react'
import { Link } from 'react-router-dom'
import { acceptSuggestion, listSuggestions, rejectSuggestion } from '../api/suggestions'
import type { ApiError } from '../api/client'
import { EmptyState, ErrorBanner, Loading } from '../components/Feedback'
import { StatusBadge } from '../components/StatusBadge'
import { asApiError, useAsync } from '../hooks/useAsync'
import { formatDateTime, humanize } from '../lib/format'

const CONFIDENCE_LABEL: Record<string, string> = {
  high: 'High confidence',
  medium: 'Medium confidence',
  low: 'Low confidence',
}

/**
 * Suggestions awaiting review.
 *
 * Every suggestion here is advisory — nothing about an application has changed
 * yet. Accept routes through the backend's real status-change service (so the
 * normal timeline event is written and every existing rule, like the
 * submitted-CV requirement, still applies); reject only marks the row
 * rejected. Either way the application it refers to is one click away.
 */
export function SuggestionsPage() {
  const suggestions = useAsync(() => listSuggestions('pending'), [])
  const [busyId, setBusyId] = useState<number | null>(null)
  const [actionError, setActionError] = useState<ApiError | null>(null)

  async function handleAccept(id: number) {
    setBusyId(id)
    setActionError(null)
    try {
      await acceptSuggestion(id)
      // Refresh the list so an accepted suggestion drops off immediately.
      suggestions.reload()
    } catch (caught) {
      setActionError(asApiError(caught))
    } finally {
      setBusyId(null)
    }
  }

  async function handleReject(id: number) {
    setBusyId(id)
    setActionError(null)
    try {
      await rejectSuggestion(id)
      suggestions.reload()
    } catch (caught) {
      setActionError(asApiError(caught))
    } finally {
      setBusyId(null)
    }
  }

  return (
    <div>
      <div className="page-head">
        <div>
          <h1>Needs attention</h1>
          <p className="muted small">
            Proposed status changes awaiting your review. Nothing here has changed any application
            yet.
          </p>
        </div>
      </div>

      {actionError && <ErrorBanner error={actionError} />}
      {suggestions.error && (
        <ErrorBanner error={suggestions.error} onRetry={suggestions.reload} />
      )}

      {suggestions.loading && !suggestions.data && <Loading label="Loading suggestions…" />}

      {suggestions.data && suggestions.data.length === 0 && (
        <EmptyState title="No pending suggestions." />
      )}

      {suggestions.data && suggestions.data.length > 0 && (
        <ul className="suggestion-list">
          {suggestions.data.map((suggestion) => (
            <li key={suggestion.id} className="card suggestion-card">
              <div className="suggestion-head">
                <div>
                  <Link className="link-strong" to={`/applications/${suggestion.application_id}`}>
                    {suggestion.company_name}
                  </Link>
                  <span className="muted"> — {suggestion.role_title}</span>
                </div>
                <span className={`confidence-tag confidence-${suggestion.confidence}`}>
                  {CONFIDENCE_LABEL[suggestion.confidence] ?? suggestion.confidence}
                </span>
              </div>

              <div className="transition">
                <StatusBadge status={suggestion.current_status} />
                <span className="arrow" aria-label="proposed change to">
                  →
                </span>
                <StatusBadge status={suggestion.proposed_status} />
              </div>

              <p className="suggestion-rationale">{suggestion.rationale}</p>
              <p className="muted small">
                Source: {humanize(suggestion.source)} · Suggested {formatDateTime(suggestion.created_at)}
              </p>

              <div className="form-actions">
                <button
                  type="button"
                  className="btn btn-primary btn-sm"
                  disabled={busyId === suggestion.id}
                  onClick={() => handleAccept(suggestion.id)}
                >
                  {busyId === suggestion.id ? 'Working…' : 'Accept'}
                </button>
                <button
                  type="button"
                  className="btn btn-sm"
                  disabled={busyId === suggestion.id}
                  onClick={() => handleReject(suggestion.id)}
                >
                  Reject
                </button>
                <Link className="btn btn-sm" to={`/applications/${suggestion.application_id}`}>
                  Open application
                </Link>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
