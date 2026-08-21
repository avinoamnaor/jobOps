// Pure, DOM-free heuristics for guessing Company and Role from a job page's
// title and visible headings. No site-specific selectors, no network — just
// generic patterns — so it stays testable and reusable by the popup.

// Job-title stems (leading word-boundary, so suffixes like "developer",
// "development", "engineering" still match). Not an exhaustive list — a signal.
const JOB_WORDS =
  /\b(engineer|develop|manager|managing|designer|analyst|scientist|intern|lead|architect|consultant|specialist|administrator|coordinator|director|officer|programmer|recruiter|marketing|marketer|sales|support|technician|nurse|teacher|writer|editor|associate|principal|staff|devops|data|product|research|software|hardware|fullstack|full-stack|backend|back-end|frontend|front-end)/i

// Short abbreviations need both boundaries to avoid matching inside real words
// (e.g. "it" inside "Credit").
const JOB_ABBR = /\b(sw|hr|it|qa)\b/i

// Generic marketing/landing headings that are NOT job titles.
const MARKETING =
  /^(join\b|welcome\b|careers?\b|work with us\b|about\b|our team\b|life at\b|why\b|apply\b|we['’]re hiring\b)/i

// Generic trailing noise on a company candidate — words, never real names.
const COMPANY_NOISE =
  /[\s|\-–—·,]*\b(careers?|jobs?|job application|career site|hiring|apply|vacanc(?:y|ies)|openings?|positions?|employment)\b[\s.·|]*$/i

// Split a page title into segments on common separators, including " at ".
const TITLE_SEPARATORS = /\s*[|\-–—·@:]\s*|\s+at\s+/i

export function looksLikeJobTitle(text) {
  const value = (text || '').trim()
  if (value.length < 2 || value.length > 120) return false
  return JOB_WORDS.test(value) || JOB_ABBR.test(value)
}

export function isMarketingHeading(text) {
  return MARKETING.test((text || '').trim())
}

export function stripCompanyNoise(text) {
  let value = (text || '').trim()
  let previous
  do {
    previous = value
    value = value.replace(COMPANY_NOISE, '').trim()
  } while (value !== previous)
  // Trim trailing separators left behind — but keep a legitimate "Inc." period.
  return value.replace(/[\s|\-–—·,]+$/, '').trim()
}

function pickCompany(candidates) {
  for (const candidate of candidates) {
    const stripped = stripCompanyNoise(candidate)
    if (stripped && !looksLikeJobTitle(stripped)) return stripped
  }
  return ''
}

/**
 * Best-effort {company, role} from a page title and headings. Every value is a
 * guess for an editable field — never authoritative. Prefers a job-title-looking
 * TITLE segment for the role (with `document.title` given the strongest weight),
 * decides Company/Role orientation from which segment looks like a job title,
 * and returns blanks rather than a clearly bad guess.
 */
export function guessCompanyAndRole({ title = '', headings = [] } = {}) {
  const segments = title
    .split(TITLE_SEPARATORS)
    .map((segment) => segment.trim())
    .filter(Boolean)

  let role = ''
  let company = ''

  // 1) Role from the title: the segment that looks most like a job title. This
  //    also fixes orientation for both "Role - Company" and "Company - Role".
  const roleIndex = segments.findIndex(looksLikeJobTitle)
  if (roleIndex >= 0) {
    role = segments[roleIndex]
    company = pickCompany(segments.filter((_, index) => index !== roleIndex))
  } else if (segments.length >= 2) {
    // No job-title-like segment: the first non-noise segment is our best company.
    company = pickCompany(segments)
  }

  // 2) Role fallback: the first plausible, non-marketing visible heading —
  //    avoids grabbing "Join the … adventure" when a real title exists.
  if (!role) {
    const headingRole = (headings || []).find(
      (heading) => looksLikeJobTitle(heading) && !isMarketingHeading(heading),
    )
    if (headingRole) role = headingRole.trim()
  }

  // 3) If we now have a role but still no company, take the other title segment.
  if (role && !company && segments.length >= 2) {
    company = pickCompany(segments.filter((segment) => segment !== role))
  }

  // Confidence guards: blank beats a clearly bad guess.
  if (company && (looksLikeJobTitle(company) || company.length > 100)) company = ''
  if (role && (isMarketingHeading(role) || role.length > 120)) role = ''

  return { company: company.trim(), role: role.trim() }
}

const WORK_MODE = /\b(remote|hybrid|on[-\s]?site)\b/i

function normalizeWorkMode(raw) {
  const value = (raw || '').toLowerCase().replace(/[\s-]/g, '')
  if (value === 'remote') return 'Remote'
  if (value === 'hybrid') return 'Hybrid'
  if (value === 'onsite') return 'On-site'
  return ''
}

function cleanLocation(text) {
  return (text || '')
    // Drop a trailing work-mode marker, with or without brackets/separators,
    // e.g. " (Hybrid)", " - Remote", " • Onsite".
    .replace(/[\s([{|\-–—•·,]*\b(remote|hybrid|on[-\s]?site)\b[)\]}\s]*$/i, '')
    .replace(/[\s,;:|\-–—•·]+$/, '')
    .trim()
}

export function looksLikeLocation(text) {
  const value = (text || '').trim()
  if (value.length < 2 || value.length > 60) return false
  // Generic signal: a comma-separated place ("City, Country"), not a sentence.
  if (!value.includes(',')) return false
  if (/[.!?]\s/.test(value)) return false
  return /^[\p{L}0-9 .,'’()/&\-]+$/u.test(value)
}

/**
 * Best-effort {location, work_mode} from headings and the top of the page text
 * (near the job header) — never the whole page, to avoid deep, arbitrary matches.
 * Strips the work-mode marker out of the location. Blank beats a bad guess.
 */
export function guessLocationAndWorkMode({ headings = [], text = '' } = {}) {
  const lines = [
    ...headings,
    ...text
      .split('\n')
      .map((line) => line.trim())
      .filter(Boolean)
      .slice(0, 15),
  ]

  let location = ''
  let workMode = ''

  // 1) Strongest: a header line combining location and mode, e.g.
  //    "Caesarea, Israel (Hybrid)".
  for (const line of lines) {
    const modeMatch = line.match(WORK_MODE)
    if (!modeMatch) continue
    const candidate = cleanLocation(line)
    if (looksLikeLocation(candidate)) {
      location = candidate
      workMode = normalizeWorkMode(modeMatch[1])
      break
    }
    if (!workMode) workMode = normalizeWorkMode(modeMatch[1])
  }

  // 2) If no combined line gave a location, take the first location-like header.
  if (!location) {
    const locationLine = lines.find(looksLikeLocation)
    if (locationLine) location = locationLine.trim()
  }

  return { location, work_mode: workMode }
}

// Job platforms treated as job boards. Everything else (a company careers page
// or an ATS domain) is where you actually apply, so it maps to "company_site".
const JOB_BOARD_HOSTS =
  /(indeed|glassdoor|ziprecruiter|monster|dice|simplyhired|careerbuilder|totaljobs|reed\.co|jobstreet|seek\.)/i

/**
 * Best-effort application channel from the page's URL/domain only — kept fully
 * separate from page scraping. "Where I actually submitted", not where I found
 * it. Editable and non-authoritative; defaults to company_site.
 */
export function guessApplicationChannel(url = '') {
  let host = ''
  try {
    host = new URL(url).hostname.toLowerCase().replace(/^www\./, '')
  } catch {
    return 'company_site'
  }
  if (!host) return 'company_site'
  if (/(^|\.)linkedin\.com$/.test(host)) return 'linkedin'
  if (JOB_BOARD_HOSTS.test(host)) return 'job_board'
  return 'company_site'
}
