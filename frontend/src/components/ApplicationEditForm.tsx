import { useState } from 'react'
import { updateApplication } from '../api/applications'
import type { ApiError } from '../api/client'
import type {
  Application,
  ApplicationChannel,
  ApplicationUpdatePayload,
  MetaEnums,
} from '../api/types'
import { asApiError } from '../hooks/useAsync'
import { emptyToNull, humanize } from '../lib/format'
import { ErrorBanner } from './Feedback'

interface Props {
  application: Application
  meta: MetaEnums | null
  onSaved: () => void
  onCancel: () => void
}

/**
 * Edit the descriptive fields of an application.
 *
 * There is no status control here on purpose. `ApplicationUpdatePayload` has no
 * `status` key, so TypeScript would reject adding one — the backend's rule that
 * status may only change through its own endpoint is mirrored as a compile-time
 * constraint rather than something to remember.
 */
export function ApplicationEditForm({ application, meta, onSaved, onCancel }: Props) {
  const [form, setForm] = useState({
    company_name: application.company_name,
    role_title: application.role_title,
    application_channel: application.application_channel,
    job_url: application.job_url ?? '',
    job_description: application.job_description ?? '',
    location: application.location ?? '',
    work_mode: application.work_mode ?? '',
    notes: application.notes ?? '',
  })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)

  function update<K extends keyof typeof form>(field: K, value: (typeof form)[K]) {
    setForm((current) => ({ ...current, [field]: value }))
  }

  function fieldError(field: string): string | undefined {
    return error?.fieldErrors.find((item) => item.field === field)?.message
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setSaving(true)
    setError(null)

    const payload: ApplicationUpdatePayload = {
      company_name: form.company_name.trim(),
      role_title: form.role_title.trim(),
      application_channel: form.application_channel,
      job_url: emptyToNull(form.job_url),
      job_description: emptyToNull(form.job_description),
      location: emptyToNull(form.location),
      work_mode: emptyToNull(form.work_mode),
      notes: emptyToNull(form.notes),
    }

    try {
      await updateApplication(application.id, payload)
      onSaved()
    } catch (caught) {
      setError(asApiError(caught))
      setSaving(false)
    }
  }

  return (
    <form onSubmit={submit} className="form">
      {error && <ErrorBanner error={error} />}

      <div className="grid-2">
        <label className="field">
          <span className="label">Company</span>
          <input
            className="input"
            required
            value={form.company_name}
            onChange={(event) => update('company_name', event.target.value)}
          />
          {fieldError('company_name') && (
            <span className="field-error">{fieldError('company_name')}</span>
          )}
        </label>

        <label className="field">
          <span className="label">Role</span>
          <input
            className="input"
            required
            value={form.role_title}
            onChange={(event) => update('role_title', event.target.value)}
          />
        </label>

        <label className="field">
          <span className="label">Channel</span>
          <select
            className="input"
            value={form.application_channel}
            onChange={(event) =>
              update('application_channel', event.target.value as ApplicationChannel)
            }
          >
            {meta?.application_channels.map((value) => (
              <option key={value} value={value}>
                {humanize(value)}
              </option>
            ))}
          </select>
        </label>

        <label className="field">
          <span className="label">Location</span>
          <input
            className="input"
            value={form.location}
            onChange={(event) => update('location', event.target.value)}
          />
        </label>

        <label className="field">
          <span className="label">Work mode</span>
          <input
            className="input"
            value={form.work_mode}
            onChange={(event) => update('work_mode', event.target.value)}
          />
        </label>

        <label className="field">
          <span className="label">Job URL</span>
          <input
            className="input"
            type="url"
            value={form.job_url}
            onChange={(event) => update('job_url', event.target.value)}
          />
        </label>
      </div>

      <label className="field">
        <span className="label">Job description</span>
        <textarea
          className="input"
          rows={10}
          value={form.job_description}
          onChange={(event) => update('job_description', event.target.value)}
        />
      </label>

      <label className="field">
        <span className="label">Notes</span>
        <textarea
          className="input"
          rows={4}
          value={form.notes}
          onChange={(event) => update('notes', event.target.value)}
        />
      </label>

      <div className="form-actions">
        <button type="submit" className="btn btn-primary" disabled={saving}>
          {saving ? 'Saving…' : 'Save changes'}
        </button>
        <button type="button" className="btn" onClick={onCancel} disabled={saving}>
          Cancel
        </button>
      </div>
    </form>
  )
}
