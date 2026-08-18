import { api } from './client'
import type { MetaEnums } from './types'

/**
 * Fetch the vocabulary (statuses, channels, event types) from the backend.
 *
 * The backend exposes this precisely so the UI does not hardcode a status list
 * that then drifts out of sync. Adding a status becomes a backend-only change.
 */
export function getMetaEnums(): Promise<MetaEnums> {
  return api.get<MetaEnums>('/meta/enums')
}
