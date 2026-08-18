import type { ApplicationEvent } from '../api/types'
import { documentDownloadUrl } from '../api/documents'
import { formatDateTime, humanize } from '../lib/format'
import { StatusBadge } from './StatusBadge'

/**
 * Render the application's history.
 *
 * The backend returns events newest-first and already decides what each event
 * means; this component only presents them. It invents no new semantics — a
 * status change is drawn as `previous → new` because those two columns exist on
 * the event, not because the UI inferred anything.
 */
export function Timeline({ events }: { events: ApplicationEvent[] }) {
  if (events.length === 0) {
    return <p className="muted">No events yet.</p>
  }

  return (
    <ol className="timeline">
      {events.map((event) => (
        <li key={event.id} className="timeline-item">
          <div className={`timeline-dot dot-${dotFamily(event)}`} aria-hidden="true" />
          <div className="timeline-body">
            <div className="timeline-head">
              <span className="timeline-type">{humanize(event.event_type)}</span>
              <time className="muted small">{formatDateTime(event.occurred_at)}</time>
              {event.source !== 'manual' && (
                <span className="chip">via {humanize(event.source)}</span>
              )}
            </div>

            {event.event_type === 'status_changed' ? (
              <div className="transition">
                <StatusBadge status={event.previous_status} />
                <span className="arrow" aria-label="changed to">
                  →
                </span>
                <StatusBadge status={event.new_status} />
              </div>
            ) : (
              <p className="timeline-summary">{event.summary}</p>
            )}

            {/* `created` also carries a status, shown so the timeline starts clearly. */}
            {event.event_type === 'created' && event.new_status && (
              <div className="transition">
                <span className="muted small">Opened as</span>
                <StatusBadge status={event.new_status} />
              </div>
            )}

            {event.document_id !== null && (
              <p className="small">
                <a href={documentDownloadUrl(event.document_id)}>Download this document</a>
              </p>
            )}

            {event.scheduled_for && (
              <p className="small scheduled">
                Scheduled for <strong>{formatDateTime(event.scheduled_for)}</strong>
              </p>
            )}

            {event.body && <p className="timeline-note">{event.body}</p>}
          </div>
        </li>
      ))}
    </ol>
  )
}

function dotFamily(event: ApplicationEvent): string {
  if (event.event_type === 'status_changed' || event.event_type === 'created') return 'status'
  if (event.event_type === 'document_attached') return 'document'
  return 'note'
}
