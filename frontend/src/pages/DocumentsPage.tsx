import { useRef, useState } from 'react'
import type { ApiError } from '../api/client'
import { documentDownloadUrl, listDocuments, uploadDocument } from '../api/documents'
import type { DocumentKind } from '../api/types'
import { EmptyState, ErrorBanner, Loading, SuccessBanner } from '../components/Feedback'
import { asApiError, useAsync } from '../hooks/useAsync'
import { documentName, formatDate, formatFileSize, humanize } from '../lib/format'

const KINDS: DocumentKind[] = ['cv', 'cover_letter', 'take_home', 'portfolio', 'other']

export function DocumentsPage() {
  const [kindFilter, setKindFilter] = useState<DocumentKind | ''>('')
  const documents = useAsync(
    () => listDocuments(kindFilter === '' ? undefined : kindFilter),
    [kindFilter],
  )

  const [uploadKind, setUploadKind] = useState<DocumentKind>('cv')
  const [label, setLabel] = useState('')
  const [notes, setNotes] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)
  const [message, setMessage] = useState('')
  const fileInput = useRef<HTMLInputElement>(null)

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    if (!file) return

    setUploading(true)
    setError(null)
    setMessage('')

    const knownHashes = new Set(documents.data?.map((document) => document.content_hash))

    try {
      const uploaded = await uploadDocument({ file, kind: uploadKind, label, notes })

      // The backend deduplicates by SHA-256. If we already had this hash, the
      // upload reused the existing row rather than storing a second copy —
      // worth telling the user, so "nothing appeared" is not confusing.
      setMessage(
        knownHashes.has(uploaded.content_hash)
          ? `Identical file already in the library — reusing “${documentName(uploaded)}”.`
          : `Uploaded “${documentName(uploaded)}”.`,
      )

      setLabel('')
      setNotes('')
      setFile(null)
      if (fileInput.current) fileInput.current.value = ''
      documents.reload()
    } catch (caught) {
      setError(asApiError(caught))
    } finally {
      setUploading(false)
    }
  }

  return (
    <div>
      <div className="page-head">
        <div>
          <h1>Documents</h1>
          <p className="muted small">
            Files are stored by content hash, so the same file is never stored twice and an
            existing document can never be silently altered.
          </p>
        </div>
      </div>

      <section className="card">
        <h2 className="card-title">Upload</h2>

        {error && <ErrorBanner error={error} />}
        {message && <SuccessBanner message={message} />}

        <form onSubmit={submit} className="form">
          <div className="grid-2">
            <label className="field">
              <span className="label">
                File <span className="required">*</span>
              </span>
              <input
                ref={fileInput}
                type="file"
                className="input"
                required
                onChange={(event) => setFile(event.target.files?.[0] ?? null)}
              />
            </label>

            <label className="field">
              <span className="label">Kind</span>
              <select
                className="input"
                value={uploadKind}
                onChange={(event) => setUploadKind(event.target.value as DocumentKind)}
              >
                {KINDS.map((kind) => (
                  <option key={kind} value={kind}>
                    {humanize(kind)}
                  </option>
                ))}
              </select>
            </label>

            <label className="field">
              <span className="label">Label</span>
              <input
                className="input"
                placeholder="Fullstack CV v3 (Node-heavy)"
                value={label}
                onChange={(event) => setLabel(event.target.value)}
              />
              <span className="hint">A name you will recognise in six months.</span>
            </label>

            <label className="field">
              <span className="label">Notes</span>
              <input
                className="input"
                value={notes}
                onChange={(event) => setNotes(event.target.value)}
              />
            </label>
          </div>

          <div className="form-actions">
            <button type="submit" className="btn btn-primary" disabled={uploading || !file}>
              {uploading ? 'Uploading…' : 'Upload document'}
            </button>
          </div>
        </form>
      </section>

      <div className="filters">
        <select
          className="input"
          value={kindFilter}
          onChange={(event) => setKindFilter(event.target.value as DocumentKind | '')}
          aria-label="Filter by kind"
        >
          <option value="">All kinds</option>
          {KINDS.map((kind) => (
            <option key={kind} value={kind}>
              {humanize(kind)}
            </option>
          ))}
        </select>
      </div>

      {documents.error && <ErrorBanner error={documents.error} onRetry={documents.reload} />}
      {documents.loading && !documents.data && <Loading label="Loading documents…" />}

      {documents.data && documents.data.length === 0 && (
        <EmptyState title="No documents yet. Upload a CV above to get started." />
      )}

      {documents.data && documents.data.length > 0 && (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Kind</th>
                <th>Size</th>
                <th>Uploaded</th>
                <th>Content hash</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {documents.data.map((document) => (
                <tr key={document.id}>
                  <td>
                    <span className="link-strong">{documentName(document)}</span>
                    {document.label && document.original_filename && (
                      <div className="muted small">{document.original_filename}</div>
                    )}
                  </td>
                  <td className="muted">{humanize(document.kind)}</td>
                  <td className="muted">{formatFileSize(document.size_bytes)}</td>
                  <td className="muted">{formatDate(document.created_at)}</td>
                  <td className="muted small mono" title={document.content_hash}>
                    {document.content_hash.slice(0, 12)}…
                  </td>
                  <td>
                    <a className="btn btn-sm" href={documentDownloadUrl(document.id)}>
                      Download
                    </a>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
