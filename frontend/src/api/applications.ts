import { api, buildQuery } from './client'
import type {
  Application,
  ApplicationCreatePayload,
  ApplicationDetail,
  ApplicationPage,
  ApplicationUpdatePayload,
  AttachSubmittedCvPayload,
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
