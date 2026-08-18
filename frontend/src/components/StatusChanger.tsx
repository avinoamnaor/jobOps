import { useState } from 'react'
import type { ApiError } from '../api/client'
import { changeStatus } from '../api/applications'
import type { ApplicationStatus, MetaEnums } from '../api/types'
import { asApiError } from '../hooks/useAsync'
import { humanize } from '../lib/format'
import { ErrorBanner } from './Feedback'

interface Props {
  applicationId: number
  currentStatus: ApplicationStatus
  meta: MetaEnums | null
  /** Called after a successful change so the page can refetch. */
  onChanged: () => void
}

/**
 * Change an application's status.
 *
 * Calls POST /applications/{id}/status — never PATCH. That endpoint is the only
 * one that writes a `status_changed` timeline event in the same transaction as
 * the status column, so routing status changes through it is what keeps the
 * history complete.
 */
export function StatusChanger({ applicationId, currentStatus, meta, onChanged }: Props) {
  const [target, setTarget] = useState<ApplicationStatus>(currentStatus)
  const [note, setNote] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)

  const unchanged = target === currentStatus

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setSaving(true)
    setError(null)
    try {
      await changeStatus(applicationId, { to: target, note: note.trim() || null })
      setNote('')
      // Refetch so the header, dates and timeline all reflect the change.
      onChanged()
    } catch (caught) {
      setError(asApiError(caught))
    } finally {
      setSaving(false)
    }
  }

  return (
    <form onSubmit={submit} className="status-changer">
      {error && <ErrorBanner error={error} />}

      <div className="row">
        <label className="field grow">
          <span className="label">Move to status</span>
          <select
            className="input"
            value={target}
            onChange={(event) => setTarget(event.target.value as ApplicationStatus)}
          >
            {meta?.statuses.map((entry) => (
              <option key={entry.value} value={entry.value}>
                {humanize(entry.value)}
                {entry.value === currentStatus ? ' (current)' : ''}
              </option>
            ))}
          </select>
        </label>
      </div>

      <label className="field">
        <span className="label">Note (optional)</span>
        <input
          className="input"
          placeholder="Recruiter called about the technical round…"
          value={note}
          onChange={(event) => setNote(event.target.value)}
        />
      </label>

      <button type="submit" className="btn btn-primary" disabled={saving || unchanged}>
        {saving ? 'Saving…' : 'Record status change'}
      </button>
      {unchanged && <p className="hint">Pick a different status to record a change.</p>}
    </form>
  )
}
