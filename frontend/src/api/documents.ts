import { api, apiUrl, buildQuery } from './client'
import type { DocumentKind, JobDocument } from './types'

export function listDocuments(kind?: DocumentKind): Promise<JobDocument[]> {
  return api.get<JobDocument[]>(`/documents${buildQuery({ kind })}`)
}

export interface UploadDocumentInput {
  file: File
  kind: DocumentKind
  label?: string
  notes?: string
}

/**
 * Upload a file.
 *
 * Sent as multipart/form-data because that is what the backend's
 * `UploadFile`/`Form` parameters expect. The backend deduplicates by SHA-256, so
 * uploading a file it already has returns the existing document rather than
 * storing a second copy.
 */
export function uploadDocument({ file, kind, label, notes }: UploadDocumentInput) {
  const form = new FormData()
  form.append('file', file)
  form.append('kind', kind)
  if (label?.trim()) form.append('label', label.trim())
  if (notes?.trim()) form.append('notes', notes.trim())

  return api.postForm<JobDocument>('/documents', form)
}

/**
 * A direct URL to the file.
 *
 * Used as an ordinary link `href`. A top-level navigation is not subject to
 * CORS, and the backend sends `Content-Disposition: attachment`, so the browser
 * downloads the file instead of trying to display it.
 */
export function documentDownloadUrl(id: number): string {
  return apiUrl(`/documents/${id}/download`)
}

/**
 * Download URL that serves the same bytes under a clean, employer-facing
 * filename (configured server-side). The stored document is unchanged.
 */
export function documentSubmissionDownloadUrl(id: number): string {
  return apiUrl(`/documents/${id}/download?submission=true`)
}
