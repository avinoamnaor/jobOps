import { Link } from 'react-router-dom'
import type { DuplicateMatch } from '../api/types'
import { formatDate } from '../lib/format'
import { StatusBadge } from './StatusBadge'

/**
 * Non-blocking advisory: existing applications that may be the same posting.
 *
 * This is informational only — it never disables Create Application. A
 * legitimate repeat posting or a second real application to the same company
 * and role is a normal thing to record, so the decision stays with the user.
 */
export function DuplicateWarning({ matches }: { matches: DuplicateMatch[] }) {
  if (matches.length === 0) return null

  return (
    <div className="banner banner-warning" role="status">
      <strong>Possible existing application</strong>
      <ul className="duplicate-list">
        {matches.map((match) => (
          <li key={match.application_id} className="duplicate-item">
            <div>
              <div className="duplicate-main">
                <span
                  className={`confidence-tag confidence-${match.confidence}`}
                  title={match.confidence === 'strong' ? 'Strong match' : 'Possible match'}
                >
                  {match.confidence}
                </span>
                <strong>{match.company_name}</strong> — {match.role_title}
                {' · '}
                <StatusBadge status={match.status} />
                {match.applied_at && (
                  <span className="muted small"> · applied {formatDate(match.applied_at)}</span>
                )}
              </div>
              <div className="duplicate-reason small">{match.reason}</div>
            </div>
            <Link
              className="btn btn-sm"
              to={`/applications/${match.application_id}`}
              target="_blank"
              rel="noreferrer"
            >
              Open existing
            </Link>
          </li>
        ))}
      </ul>
    </div>
  )
}
