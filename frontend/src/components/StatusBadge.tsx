import { humanize } from '../lib/format'

/**
 * A coloured pill for a status.
 *
 * Grouped into four visual families rather than twelve colours, so the list
 * scans quickly: neutral (not applied yet), in progress, good news, and closed.
 */
const FAMILY: Record<string, string> = {
  saved: 'neutral',
  applied: 'progress',
  recruiter_contact: 'progress',
  hr_interview: 'progress',
  technical_interview: 'progress',
  take_home: 'progress',
  final_interview: 'progress',
  offer: 'good',
  accepted: 'good',
  rejected: 'closed',
  withdrawn: 'closed',
  on_hold: 'neutral',
}

export function StatusBadge({ status }: { status: string | null }) {
  if (!status) return <span className="badge badge-neutral">—</span>
  return (
    <span className={`badge badge-${FAMILY[status] ?? 'neutral'}`}>{humanize(status)}</span>
  )
}
