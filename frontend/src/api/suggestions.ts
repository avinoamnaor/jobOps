import { api, buildQuery } from './client'
import type { Suggestion, SuggestionState } from './types'

/**
 * List suggestions, optionally filtered by state. `state: 'pending'` is what
 * both the nav badge count and the review page use.
 */
export function listSuggestions(state?: SuggestionState): Promise<Suggestion[]> {
  return api.get<Suggestion[]>(`/suggestions${buildQuery({ state })}`)
}

/**
 * Accept: the backend routes this through the real status-change service, so a
 * normal `status_changed` timeline event is written and every existing rule
 * (e.g. the submitted-CV requirement) still applies.
 */
export function acceptSuggestion(id: number, note?: string): Promise<Suggestion> {
  return api.post<Suggestion>(`/suggestions/${id}/accept`, { note: note ?? null })
}

/** Reject: marks the suggestion only. The application is never touched. */
export function rejectSuggestion(id: number): Promise<Suggestion> {
  return api.post<Suggestion>(`/suggestions/${id}/reject`)
}
