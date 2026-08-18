import { useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { attachSubmittedCv } from '../api/applications'
import type { ApiError } from '../api/client'
import { documentDownloadUrl, listDocuments, uploadDocument } from '../api/documents'
import type { JobDocument } from '../api/types'
import { asApiError, useAsync } from '../hooks/useAsync'
import { documentName, formatDate, formatFileSize } from '../lib/format'
import { ErrorBanner } from './Feedback'

interface Props {
  applicationId: number
  submittedCv: JobDocument | null
  onChanged: () => void
}

/**
 * Show and change the CV recorded as submitted for this application.
 *
 * Two ways to set it, because both are real workflows:
 *   * pick a CV already in the library (the common case — you reuse a CV)
 *   * upload a new file, which uploads and attaches in one action
 *
 * Only `cv`-kind documents are offered. The backend enforces that rule too and
 * would return 422 — this just avoids offering a choice that cannot work.
 */
export function SubmittedCvPanel({ applicationId, submittedCv, onChanged }: Props) {
  const cvs = useAsync(() => listDocuments('cv'), [])
  const [selectedId, setSelectedId] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)

  async function attachExisting(event: React.FormEvent) {
    event.preventDefault()
    if (!selectedId) return
    setBusy(true)
    setError(null)
    try {
      await attachSubmittedCv(applicationId, { document_id: Number(selectedId) })
      setSelectedId('')
      onChanged()
    } catch (caught) {
      setError(asApiError(caught))
    } finally {
      setBusy(false)
    }
  }

  async function uploadAndAttach(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    if (!file) return

    setBusy(true)
    setError(null)
    try {
      // Two requests: store the file, then link it. The backend deduplicates by
      // content hash, so re-uploading a CV you already have reuses that row.
      const uploaded = await uploadDocument({ file, kind: 'cv', label: file.name })
      await attachSubmittedCv(applicationId, { document_id: uploaded.id })
      cvs.reload()
      onChanged()
    } catch (caught) {
      setError(asApiError(caught))
    } finally {
      setBusy(false)
      // Reset so choosing the same file again still fires a change event.
      if (fileInput.current) fileInput.current.value = ''
    }
  }

  const otherCvs = cvs.data?.filter((cv) => cv.id !== submittedCv?.id) ?? []

  return (
    <section className="card">
      <h2 className="card-title">Submitted CV</h2>

      {error && <ErrorBanner error={error} />}

      {submittedCv ? (
        <div className="cv-current">
          <div>
            <p className="cv-name">{documentName(submittedCv)}</p>
            <p className="muted small">
              {submittedCv.original_filename} · {formatFileSize(submittedCv.size_bytes)} · uploaded{' '}
              {formatDate(submittedCv.created_at)}
            </p>
            <p className="muted small mono" title="SHA-256 of the file contents">
              {submittedCv.content_hash.slice(0, 16)}…
            </p>
          </div>
          <a className="btn btn-sm" href={documentDownloadUrl(submittedCv.id)}>
            Download
          </a>
        </div>
      ) : (
        <p className="muted">No CV recorded as submitted for this application yet.</p>
      )}

      <div className="divider" />

      <form onSubmit={attachExisting} className="row row-end">
        <label className="field grow">
          <span className="label">{submittedCv ? 'Change to another CV' : 'Attach a CV'}</span>
          <select
            className="input"
            value={selectedId}
            onChange={(event) => setSelectedId(event.target.value)}
            disabled={busy || otherCvs.length === 0}
          >
            <option value="">
              {otherCvs.length === 0 ? 'No other CVs in the library' : 'Choose a CV…'}
            </option>
            {otherCvs.map((cv) => (
              <option key={cv.id} value={cv.id}>
                {documentName(cv)} ({formatFileSize(cv.size_bytes)})
              </option>
            ))}
          </select>
        </label>
        <button type="submit" className="btn" disabled={busy || !selectedId}>
          Attach
        </button>
      </form>

      <div className="upload-inline">
        {/* The label wraps the input so it is programmatically associated with
            it, matching the file field on the Documents page. */}
        <label className="field">
          <span className="label">Or upload a new CV</span>
          <input
            ref={fileInput}
            type="file"
            className="input"
            disabled={busy}
            onChange={uploadAndAttach}
          />
        </label>
        <p className="hint">
          Uploads and attaches in one step. Identical files are detected and reused rather than
          stored twice. See the <Link to="/documents">document library</Link>.
        </p>
      </div>

      {busy && <p className="muted small">Working…</p>}
    </section>
  )
}
