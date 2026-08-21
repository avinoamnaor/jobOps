import { api, buildQuery } from './client'
import type {
  Application,
  ApplicationCreatePayload,
  ApplicationDetail,
  ApplicationFolderExport,
  ApplicationPage,
  ApplicationUpdatePayload,
  AttachSubmittedCvPayload,
  DuplicateCheckPayload,
  DuplicateMatch,
  StatusChangePayload,
} from './types'

export interface ListApplicationsParams {
  status?: string
  channel?: string
  q?: string
  page?: number
  page_size?: number
}

export function listApplications(params: ListApplicationsParams): Promise<ApplicationPage> {
  return api.get<ApplicationPage>(`/applications${buildQuery({ ...params })}`)
}

export function getApplication(id: number): Promise<ApplicationDetail> {
  return api.get<ApplicationDetail>(`/applications/${id}`)
}

export function createApplication(payload: ApplicationCreatePayload): Promise<Application> {
  return api.post<Application>('/applications', payload)
}

/**
 * Edit descriptive fields.
 *
 * `ApplicationUpdatePayload` has no `status` key, so a status change cannot be
 * smuggled through here — see `changeStatus` below.
 */
export function updateApplication(
  id: number,
  payload: ApplicationUpdatePayload,
): Promise<Application> {
  return api.patch<Application>(`/applications/${id}`, payload)
}

/**
 * The ONLY way this frontend changes a status.
 *
 * The backend guarantees that this endpoint writes a `status_changed` timeline
 * event in the same transaction as the column update, which is why status must
 * never travel through PATCH.
 */
export function changeStatus(
  id: number,
  payload: StatusChangePayload,
): Promise<ApplicationDetail> {
  return api.post<ApplicationDetail>(`/applications/${id}/status`, payload)
}

export function attachSubmittedCv(
  id: number,
  payload: AttachSubmittedCvPayload,
): Promise<ApplicationDetail> {
  return api.put<ApplicationDetail>(`/applications/${id}/submitted-cv`, payload)
}

export function deleteApplication(id: number): Promise<void> {
  return api.delete<void>(`/applications/${id}`)
}

/**
 * Advisory-only: existing applications that may be the same posting. Never
 * blocks creation — the caller decides what, if anything, to do with the result.
 */
export function checkForDuplicates(payload: DuplicateCheckPayload): Promise<DuplicateMatch[]> {
  return api.post<DuplicateMatch[]>('/applications/duplicate-check', payload)
}

/**
 * Prepare (or rebuild) the application's local folder with a copy of the
 * submitted CV. A filesystem convenience — writes nothing to the database.
 */
export function exportApplicationFolder(id: number): Promise<ApplicationFolderExport> {
  return api.post<ApplicationFolderExport>(`/applications/${id}/export-folder`)
}

/**
 * Prepare the folder if needed, then open it in the OS file manager. The
 * backend launches File Explorer (local single-user app).
 */
export function openApplicationFolder(id: number): Promise<ApplicationFolderExport> {
  return api.post<ApplicationFolderExport>(`/applications/${id}/open-folder`)
}

/**
 * Prepare and open a submission folder BEFORE the application is created, from a
 * company/role and a selected CV document. Creates no application row.
 */
export function prepareDraftFolder(payload: {
  company_name: string
  role_title: string
  document_id: number
}): Promise<ApplicationFolderExport> {
  return api.post<ApplicationFolderExport>('/applications/prepare-folder', payload)
}
