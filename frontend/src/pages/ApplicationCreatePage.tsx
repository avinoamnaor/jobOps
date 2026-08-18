import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { createApplication } from '../api/applications'
import { getMetaEnums } from '../api/meta'
import type { ApplicationChannel, ApplicationCreatePayload, ApplicationStatus } from '../api/types'
import { ErrorBanner } from '../components/Feedback'
import { asApiError, useAsync } from '../hooks/useAsync'
import type { ApiError } from '../api/client'
import { emptyToNull, humanize } from '../lib/format'

export function ApplicationCreatePage() {
  const navigate = useNavigate()
  const meta = useAsync(() => getMetaEnums(), [])

  // One state object for the whole form. Simple and easy to follow at this size;
  // a form library would be more machinery than this screen justifies.
  const [form, setForm] = useState({
    company_name: '',
    role_title: '',
    status: 'saved' as ApplicationStatus,
    application_channel: 'other' as ApplicationChannel,
    job_url: '',
    job_description: '',
    location: '',
    work_mode: '',
    notes: '',
  })

  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)

  function update<K extends keyof typeof form>(field: K, value: (typeof form)[K]) {
    setForm((current) => ({ ...current, [field]: value }))
  }

  /** Per-field message from a 422, so it can appear under the right input. */
  function fieldError(field: string): string | undefined {
    return error?.fieldErrors.find((item) => item.field === field)?.message
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault()
    setSubmitting(true)
    setError(null)

    // Built explicitly rather than spreading `form`, so only fields the backend
    // accepts are ever sent. The backend rejects unknown fields with a 422.
    const payload: ApplicationCreatePayload = {
      company_name: form.company_name.trim(),
      role_title: form.role_title.trim(),
      status: form.status,
      application_channel: form.application_channel,
      job_url: emptyToNull(form.job_url),
      job_description: emptyToNull(form.job_description),
      location: emptyToNull(form.location),
      work_mode: emptyToNull(form.work_mode),
      notes: emptyToNull(form.notes),
    }

    try {
      const created = await createApplication(payload)
      navigate(`/applications/${created.id}`)
    } catch (caught) {
      setError(asApiError(caught))
      setSubmitting(false)
    }
  }

  return (
    <div className="narrow">
      <div className="page-head">
        <h1>New application</h1>
        <Link className="btn btn-sm" to="/">
          Cancel
        </Link>
      </div>

      {error && <ErrorBanner error={error} />}

      <form onSubmit={handleSubmit} className="card form">
        <div className="grid-2">
          <label className="field">
            <span className="label">
              Company <span className="required">*</span>
            </span>
            <input
              className="input"
              required
              autoFocus
              value={form.company_name}
              onChange={(event) => update('company_name', event.target.value)}
            />
            {fieldError('company_name') && (
              <span className="field-error">{fieldError('company_name')}</span>
            )}
          </label>

          <label className="field">
            <span className="label">
              Role <span className="required">*</span>
            </span>
            <input
              className="input"
              required
              value={form.role_title}
              onChange={(event) => update('role_title', event.target.value)}
            />
            {fieldError('role_title') && (
              <span className="field-error">{fieldError('role_title')}</span>
            )}
          </label>

          <label className="field">
            <span className="label">Initial status</span>
            <select
              className="input"
              value={form.status}
              onChange={(event) => update('status', event.target.value as ApplicationStatus)}
            >
              {meta.data?.statuses.map((entry) => (
                <option key={entry.value} value={entry.value}>
                  {humanize(entry.value)}
                </option>
              ))}
            </select>
            <span className="hint">
              Choosing “Applied” records today as the application date.
            </span>
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
              {meta.data?.application_channels.map((value) => (
                <option key={value} value={value}>
                  {humanize(value)}
                </option>
              ))}
            </select>
            <span className="hint">How you actually submitted, not where you found it.</span>
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
              placeholder="remote / hybrid / onsite"
              value={form.work_mode}
              onChange={(event) => update('work_mode', event.target.value)}
            />
          </label>
        </div>

        <label className="field">
          <span className="label">Job URL</span>
          <input
            className="input"
            type="url"
            placeholder="https://…"
            value={form.job_url}
            onChange={(event) => update('job_url', event.target.value)}
          />
          {fieldError('job_url') && <span className="field-error">{fieldError('job_url')}</span>}
        </label>

        <label className="field">
          <span className="label">Job description</span>
          <textarea
            className="input"
            rows={8}
            placeholder="Paste the full posting — it will disappear from the internet eventually."
            value={form.job_description}
            onChange={(event) => update('job_description', event.target.value)}
          />
        </label>

        <label className="field">
          <span className="label">Notes</span>
          <textarea
            className="input"
            rows={3}
            placeholder="Recruiter name, referral, salary discussed…"
            value={form.notes}
            onChange={(event) => update('notes', event.target.value)}
          />
        </label>

        <div className="form-actions">
          <button type="submit" className="btn btn-primary" disabled={submitting}>
            {submitting ? 'Saving…' : 'Create application'}
          </button>
          <Link className="btn" to="/">
            Cancel
          </Link>
        </div>
      </form>
    </div>
  )
}
