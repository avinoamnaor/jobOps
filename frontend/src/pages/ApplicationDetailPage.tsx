import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { getApplication } from '../api/applications'
import { getMetaEnums } from '../api/meta'
import { ApplicationEditForm } from '../components/ApplicationEditForm'
import { ErrorBanner, Loading } from '../components/Feedback'
import { StatusBadge } from '../components/StatusBadge'
import { StatusChanger } from '../components/StatusChanger'
import { SubmittedCvPanel } from '../components/SubmittedCvPanel'
import { Timeline } from '../components/Timeline'
import { useAsync } from '../hooks/useAsync'
import { formatDate, formatDateTime, humanize } from '../lib/format'

export function ApplicationDetailPage() {
  // Route params always arrive as strings.
  const { id } = useParams<{ id: string }>()
  const applicationId = Number(id)

  const [editing, setEditing] = useState(false)

  const meta = useAsync(() => getMetaEnums(), [])
  const application = useAsync(() => getApplication(applicationId), [applicationId])

  if (application.loading && !application.data) return <Loading label="Loading application…" />
  if (application.error) {
    return (
      <div>
        <ErrorBanner error={application.error} onRetry={application.reload} />
        <Link className="btn" to="/">
          Back to applications
        </Link>
      </div>
    )
  }
  if (!application.data) return null

  const app = application.data

  /** Refetch everything after a mutation, so header/dates/timeline stay in sync. */
  const refresh = () => application.reload()

  return (
    <div>
      <div className="breadcrumb">
        <Link to="/">← Applications</Link>
      </div>

      <div className="page-head">
        <div>
          <h1>{app.company_name}</h1>
          <p className="subtitle">{app.role_title}</p>
          <div className="head-meta">
            <StatusBadge status={app.status} />
            <span className="muted small">{humanize(app.application_channel)}</span>
            {app.location && <span className="muted small">· {app.location}</span>}
            {app.work_mode && <span className="muted small">· {humanize(app.work_mode)}</span>}
          </div>
        </div>
        {!editing && (
          <button type="button" className="btn" onClick={() => setEditing(true)}>
            Edit
          </button>
        )}
      </div>

      <div className="detail-grid">
        <div className="detail-main">
          <section className="card">
            <h2 className="card-title">Job details</h2>
            {editing ? (
              <ApplicationEditForm
                application={app}
                meta={meta.data}
                onSaved={() => {
                  setEditing(false)
                  refresh()
                }}
                onCancel={() => setEditing(false)}
              />
            ) : (
              <>
                <dl className="detail-list">
                  <div>
                    <dt>Applied</dt>
                    <dd>{formatDate(app.applied_at)}</dd>
                  </div>
                  <div>
                    <dt>Created</dt>
                    <dd>{formatDate(app.created_at)}</dd>
                  </div>
                  <div>
                    <dt>Last updated</dt>
                    <dd>{formatDateTime(app.updated_at)}</dd>
                  </div>
                  {app.closed_at && (
                    <div>
                      <dt>Closed</dt>
                      <dd>{formatDate(app.closed_at)}</dd>
                    </div>
                  )}
                  <div>
                    <dt>Job URL</dt>
                    <dd>
                      {app.job_url ? (
                        <a href={app.job_url} target="_blank" rel="noreferrer">
                          Open posting
                        </a>
                      ) : (
                        <span className="muted">—</span>
                      )}
                    </dd>
                  </div>
                </dl>

                {app.notes && (
                  <>
                    <h3 className="sub-title">Notes</h3>
                    <p className="prewrap">{app.notes}</p>
                  </>
                )}

                {app.job_description && (
                  <details className="jd">
                    <summary>Job description</summary>
                    <p className="prewrap">{app.job_description}</p>
                  </details>
                )}
              </>
            )}
          </section>

          <section className="card">
            <h2 className="card-title">Timeline</h2>
            <Timeline events={app.events} />
          </section>
        </div>

        <aside className="detail-side">
          <section className="card">
            <h2 className="card-title">Status</h2>
            <StatusChanger
              applicationId={app.id}
              currentStatus={app.status}
              meta={meta.data}
              onChanged={refresh}
            />
          </section>

          <SubmittedCvPanel
            applicationId={app.id}
            submittedCv={app.submitted_cv}
            onChanged={refresh}
          />
        </aside>
      </div>
    </div>
  )
}
