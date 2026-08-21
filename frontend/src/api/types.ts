/**
 * TypeScript mirrors of the backend's Pydantic schemas.
 *
 * These are hand-written rather than generated. At this size that is simpler,
 * and writing them out once is a good way to see exactly what the API returns.
 * If they ever drift from the backend, the mistake shows up as a type error
 * rather than as `undefined` appearing somewhere in the UI.
 */

export type ApplicationStatus =
  | 'saved'
  | 'applied'
  | 'recruiter_contact'
  | 'hr_interview'
  | 'technical_interview'
  | 'take_home'
  | 'final_interview'
  | 'offer'
  | 'accepted'
  | 'rejected'
  | 'withdrawn'
  | 'on_hold'

export type ApplicationChannel =
  | 'linkedin'
  | 'company_site'
  | 'recruiter'
  | 'referral'
  | 'job_board'
  | 'other'

export type DocumentKind = 'cv' | 'cover_letter' | 'take_home' | 'portfolio' | 'other'

export type EventType =
  | 'created'
  | 'status_changed'
  | 'note_added'
  | 'document_attached'
  | 'interview_scheduled'
  | 'interview_completed'
  | 'assignment_received'
  | 'assignment_submitted'
  | 'offer_received'
  | 'followed_up'
  | 'imported'

export interface JobDocument {
  id: number
  kind: DocumentKind
  label: string | null
  original_filename: string | null
  content_hash: string
  content_type: string | null
  size_bytes: number
  notes: string | null
  created_at: string
  archived_at: string | null
}

export interface ApplicationEvent {
  id: number
  application_id: number
  event_type: EventType
  occurred_at: string
  scheduled_for: string | null
  source: string
  previous_status: string | null
  new_status: string | null
  document_id: number | null
  summary: string
  body: string | null
  payload: Record<string, unknown> | null
  created_at: string
}

export interface Application {
  id: number
  company_name: string
  company_key: string
  role_title: string
  role_key: string
  status: ApplicationStatus
  application_channel: ApplicationChannel
  job_url: string | null
  job_url_canonical: string | null
  job_description: string | null
  location: string | null
  work_mode: string | null
  notes: string | null
  submitted_cv_document_id: number | null
  applied_at: string | null
  closed_at: string | null
  created_at: string
  updated_at: string
}

export interface ApplicationDetail extends Application {
  events: ApplicationEvent[]
  submitted_cv: JobDocument | null
}

export interface ApplicationFolderExport {
  folder: string
  path: string
}

/** Advisory duplicate-detection request/response — see POST /applications/duplicate-check. */
export interface DuplicateCheckPayload {
  company_name: string
  role_title: string
  job_url?: string | null
  job_description?: string | null
}

export type DuplicateConfidence = 'strong' | 'possible'

export interface DuplicateMatch {
  application_id: number
  company_name: string
  role_title: string
  status: ApplicationStatus
  applied_at: string | null
  confidence: DuplicateConfidence
  reason: string
}

/** A proposed status change awaiting review — see POST /suggestions and friends. */
export type SuggestionSource = 'manual' | 'gmail' | 'claude'
export type SuggestionConfidence = 'high' | 'medium' | 'low'
export type SuggestionState = 'pending' | 'accepted' | 'rejected'

export interface Suggestion {
  id: number
  application_id: number
  proposed_status: ApplicationStatus
  source: SuggestionSource
  confidence: SuggestionConfidence
  rationale: string
  state: SuggestionState
  created_at: string
  resolved_at: string | null
  // Application context embedded so the review list needs no second request.
  company_name: string
  role_title: string
  current_status: ApplicationStatus
}

export interface ApplicationPage {
  items: Application[]
  total: number
  page: number
  page_size: number
}

/**
 * The fields POST /applications accepts.
 *
 * Server-owned fields (id, company_key, job_url_canonical, created_at, ...) are
 * absent on purpose — the backend rejects them with 422, and leaving them out of
 * the type means we cannot send them by accident.
 */
export interface ApplicationCreatePayload {
  company_name: string
  role_title: string
  status?: ApplicationStatus
  application_channel?: ApplicationChannel
  job_url?: string | null
  job_description?: string | null
  location?: string | null
  work_mode?: string | null
  notes?: string | null
  applied_at?: string | null
  /** The CV submitted, when recording an already-applied application. */
  submitted_cv_document_id?: number | null
}

/**
 * The fields PATCH /applications/{id} accepts.
 *
 * Note there is no `status` here, and that is the whole point. The backend
 * refuses a PATCH containing `status` with a 422, because changing status must
 * also write a timeline event. This type makes that rule a compile-time one:
 * you cannot even write the mistake.
 */
export interface ApplicationUpdatePayload {
  company_name?: string
  role_title?: string
  application_channel?: ApplicationChannel
  job_url?: string | null
  job_description?: string | null
  location?: string | null
  work_mode?: string | null
  notes?: string | null
  applied_at?: string | null
}

export interface StatusChangePayload {
  to: ApplicationStatus
  note?: string | null
  occurred_at?: string | null
}

export interface AttachSubmittedCvPayload {
  document_id: number
  note?: string | null
  occurred_at?: string | null
}

export interface MetaEnums {
  statuses: {
    value: ApplicationStatus
    is_terminal: boolean
    is_active: boolean
    stage_order: number | null
    requires_submitted_cv: boolean
  }[]
  event_types: { value: EventType; manually_addable: boolean }[]
  application_channels: ApplicationChannel[]
  event_sources: string[]
}
