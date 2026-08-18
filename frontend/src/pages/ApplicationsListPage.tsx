import { useState } from 'react'
import { Link } from 'react-router-dom'
import { listApplications } from '../api/applications'
import { getMetaEnums } from '../api/meta'
import { EmptyState, ErrorBanner, Loading } from '../components/Feedback'
import { StatusBadge } from '../components/StatusBadge'
import { useAsync } from '../hooks/useAsync'
import { useDebounced } from '../hooks/useDebounced'
import { formatDate, humanize } from '../lib/format'

const PAGE_SIZE = 25

export function ApplicationsListPage() {
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState('')
  const [channel, setChannel] = useState('')
  const [page, setPage] = useState(1)

  // Wait for typing to settle before hitting the API.
  const debouncedSearch = useDebounced(search)

  const meta = useAsync(() => getMetaEnums(), [])

  const applications = useAsync(
    () => listApplications({ q: debouncedSearch, status, channel, page, page_size: PAGE_SIZE }),
    [debouncedSearch, status, channel, page],
  )

  // Any filter change invalidates the current page number.
  function changeFilter(apply: () => void) {
    apply()
    setPage(1)
  }

  const totalPages = applications.data ? Math.ceil(applications.data.total / PAGE_SIZE) : 1
  const hasFilters = Boolean(debouncedSearch || status || channel)

  return (
    <div>
      <div className="page-head">
        <div>
          <h1>Applications</h1>
          {applications.data && (
            <p className="muted small">
              {applications.data.total} total
              {hasFilters ? ' matching your filters' : ''}
            </p>
          )}
        </div>
        <Link className="btn btn-primary" to="/applications/new">
          New application
        </Link>
      </div>

      <div className="filters">
        <input
          type="search"
          className="input"
          placeholder="Search company or role…"
          value={search}
          onChange={(event) => changeFilter(() => setSearch(event.target.value))}
          aria-label="Search company or role"
        />

        <select
          className="input"
          value={status}
          onChange={(event) => changeFilter(() => setStatus(event.target.value))}
          aria-label="Filter by status"
        >
          <option value="">All statuses</option>
          {meta.data?.statuses.map((entry) => (
            <option key={entry.value} value={entry.value}>
              {humanize(entry.value)}
            </option>
          ))}
        </select>

        <select
          className="input"
          value={channel}
          onChange={(event) => changeFilter(() => setChannel(event.target.value))}
          aria-label="Filter by channel"
        >
          <option value="">All channels</option>
          {meta.data?.application_channels.map((value) => (
            <option key={value} value={value}>
              {humanize(value)}
            </option>
          ))}
        </select>

        {hasFilters && (
          <button
            type="button"
            className="btn btn-sm"
            onClick={() => {
              setSearch('')
              setStatus('')
              setChannel('')
              setPage(1)
            }}
          >
            Clear
          </button>
        )}
      </div>

      {applications.error && (
        <ErrorBanner error={applications.error} onRetry={applications.reload} />
      )}

      {applications.loading && !applications.data && <Loading label="Loading applications…" />}

      {applications.data && applications.data.items.length === 0 && (
        <EmptyState title={hasFilters ? 'No applications match those filters.' : 'No applications yet.'}>
          {!hasFilters && (
            <Link className="btn btn-primary" to="/applications/new">
              Add your first application
            </Link>
          )}
        </EmptyState>
      )}

      {applications.data && applications.data.items.length > 0 && (
        <>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Company</th>
                  <th>Role</th>
                  <th>Status</th>
                  <th>Channel</th>
                  <th>Applied</th>
                  <th>CV</th>
                </tr>
              </thead>
              <tbody>
                {applications.data.items.map((application) => (
                  <tr key={application.id}>
                    <td>
                      <Link className="link-strong" to={`/applications/${application.id}`}>
                        {application.company_name}
                      </Link>
                    </td>
                    <td>{application.role_title}</td>
                    <td>
                      <StatusBadge status={application.status} />
                    </td>
                    <td className="muted">{humanize(application.application_channel)}</td>
                    <td className="muted">{formatDate(application.applied_at)}</td>
                    <td>
                      {application.submitted_cv_document_id ? (
                        <span className="chip chip-ok">Attached</span>
                      ) : (
                        <span className="muted small">—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {totalPages > 1 && (
            <div className="pager">
              <button
                type="button"
                className="btn btn-sm"
                disabled={page <= 1}
                onClick={() => setPage((current) => current - 1)}
              >
                Previous
              </button>
              <span className="muted small">
                Page {page} of {totalPages}
              </span>
              <button
                type="button"
                className="btn btn-sm"
                disabled={page >= totalPages}
                onClick={() => setPage((current) => current + 1)}
              >
                Next
              </button>
            </div>
          )}
        </>
      )}
    </div>
  )
}
