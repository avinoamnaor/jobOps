// Structured-first job extraction.
//
// Pure and DOM-free: every function here takes a plain "page snapshot" object
// (see collect.js for how one is built inside the page) and returns plain data.
// That keeps all the judgement — which block is the job description, is this
// really a remote job, is that number part of the title — in one place that can
// be tested exhaustively without a browser or a DOM library.
//
// The guiding rule throughout: a confident blank beats a confident wrong answer.
// Every field is editable in the popup, so an empty field costs the user one
// correction, while a wrong-but-plausible field can be saved without noticing.

import { isMarketingHeading, looksLikeJobTitle, stripCompanyNoise } from './guess.js'

// ---------------------------------------------------------------------------
// HTML → text (only ever applied to a JSON-LD `description`, never to the page)
// ---------------------------------------------------------------------------

const NAMED_ENTITIES = {
  amp: '&',
  lt: '<',
  gt: '>',
  quot: '"',
  apos: "'",
  nbsp: ' ',
}

export function decodeEntities(text) {
  return String(text || '')
    .replace(/&#x([0-9a-f]+);/gi, (_, hex) => String.fromCodePoint(parseInt(hex, 16)))
    .replace(/&#(\d+);/g, (_, dec) => String.fromCodePoint(parseInt(dec, 10)))
    .replace(/&([a-z]+);/gi, (match, name) => NAMED_ENTITIES[name.toLowerCase()] ?? match)
}

/**
 * Flatten an HTML fragment to readable plain text.
 *
 * Deliberately not a parser: this only ever runs on a JSON-LD `description`,
 * which is a known-benign field we are converting for display. Block-level tags
 * become newlines and list items become "- " so section structure survives.
 */
export function htmlToText(html) {
  if (!html) return ''
  let text = String(html)

  // Script/style content is never readable text.
  text = text.replace(/<(script|style)\b[^>]*>[\s\S]*?<\/\1>/gi, '')
  text = text.replace(/<!--[\s\S]*?-->/g, '')

  text = text.replace(/<br\s*\/?>/gi, '\n')
  text = text.replace(/<li\b[^>]*>/gi, '\n- ')
  text = text.replace(/<\/(p|div|section|article|ul|ol|li|tr|h[1-6])\s*>/gi, '\n')
  text = text.replace(/<(p|div|section|article|ul|ol|tr|h[1-6])\b[^>]*>/gi, '\n')
  text = text.replace(/<[^>]+>/g, '')

  return normalizeWhitespace(decodeEntities(text))
}

/** Collapse runs of blank lines and trailing spaces without losing structure. */
export function normalizeWhitespace(text) {
  return String(text || '')
    .replace(/\r\n?/g, '\n')
    .replace(/[ \t ]+/g, ' ')
    .split('\n')
    .map((line) => line.trim())
    .join('\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}

// ---------------------------------------------------------------------------
// JSON-LD
// ---------------------------------------------------------------------------

/**
 * Parse every JSON-LD block into a flat list of candidate objects.
 *
 * Handles a single object, a top-level array, and `@graph` nesting. A malformed
 * or unrelated block is skipped rather than throwing — a broken analytics tag
 * must never stop us reading a valid JobPosting next to it.
 */
export function parseJsonLdObjects(rawBlocks) {
  const objects = []

  const visit = (node, depth = 0) => {
    if (!node || depth > 6) return
    if (Array.isArray(node)) {
      node.forEach((item) => visit(item, depth + 1))
      return
    }
    if (typeof node !== 'object') return
    objects.push(node)
    if (node['@graph']) visit(node['@graph'], depth + 1)
  }

  for (const raw of rawBlocks || []) {
    try {
      visit(JSON.parse(raw))
    } catch {
      // Malformed JSON-LD is common in the wild; ignore this block only.
    }
  }

  return objects
}

function typeIncludes(node, wanted) {
  const type = node?.['@type']
  if (!type) return false
  const types = Array.isArray(type) ? type : [type]
  return types.some((entry) => String(entry).toLowerCase() === wanted.toLowerCase())
}

/** The first JSON-LD object that declares itself a JobPosting, or null. */
export function findJobPosting(rawBlocks) {
  return parseJsonLdObjects(rawBlocks).find((node) => typeIncludes(node, 'JobPosting')) || null
}

/** Treat empty strings, whitespace and obvious placeholders as absent. */
function usableText(value) {
  const text = typeof value === 'string' ? value.trim() : ''
  if (!text) return ''
  if (/^(n\/?a|none|null|undefined|tbd|unknown)$/i.test(text)) return ''
  return text
}

// ---------------------------------------------------------------------------
// Vocabulary signals
// ---------------------------------------------------------------------------

// Headings that mark real job-description content. Generic across ATS vendors —
// deliberately not tuned to any one site.
const JD_SECTION =
  /\b(responsibilit|qualification|requirement|about the (?:role|job|position|team)|what you['’]?ll do|what you will do|who you are|your (?:impact|role|day)|why join|why work|benefits|we offer|skills|experience|nice to have|preferred|minimum|the role|the job|job description|duties|essential functions)\b/i

// Site chrome vocabulary. Only ever used as a negative signal, never to decide
// what the description IS.
const NAV_VOCAB =
  /\b(products?|solutions?|resources?|support|contact us|log ?in|sign ?in|sign ?up|register|pricing|partners?|blog|newsroom|press|投资|investors|cookie|privacy policy|terms of use|all rights reserved|follow us|subscribe|menu|navigation|search|toggle)\b/i

// Buttons/CTAs that are not part of the description.
const CTA_LINE =
  /^(apply(?: now| for this job| here)?|submit(?: your)? (?:application|resume|cv)|save (?:job|this job)|share(?: this job)?|back to (?:job )?(?:jobs?|search(?: results)?|results?|careers?|listings?|openings?|positions?)|view all jobs|print|email this job|refer a friend)[\s.!:]*$/i

// A visible job/requisition id line, e.g. "Job ID: REF123" or "Req # 1234".
// Used both to extract the id and to recognise a header meta-row as chrome.
const JOB_ID_LINE =
  /\b(job|requisition|req|reference|ref|posting)\s*(?:id|number|no\.?|#)?\s*[:#]\s*([A-Za-z0-9][A-Za-z0-9_-]{2,})\b/i

// A URL or something URL-shaped ("www.example.com/careers"). Never part of a
// location or a genuine job description — language-independent, since a URL
// is a URL regardless of the page's language.
const URL_LIKE = /https?:\/\/\S+|www\.[a-z0-9-]+\.[a-z]{2,}\S*/i

// An email address. Same reasoning as URL_LIKE.
const EMAIL_LIKE = /[^\s@]+@[^\s@]+\.[a-z]{2,}/i

// Legal/accessibility/contact/site-metadata phrases — specific enough that
// genuine job content essentially never repeats them, unlike generic nav
// words (e.g. "support", "search") a real JD can legitimately use in prose.
const SITE_INFO_VOCAB =
  /\b(accessibility statement|accessibility policy|privacy policy|privacy notice|cookie policy|cookie notice|terms of (?:use|service)|legal notice|disclaimer|site ?map|all rights reserved|copyright ©|contact us|customer service|help center|frequently asked questions)\b/i

// Work-mode evidence strong enough to act on when found in job content.
const REMOTE_STRONG =
  /\b(fully remote|100% remote|remote position|remote role|remote job|remote opportunity|remote[- ]first|work from home|telecommut\w*|this (?:role|position|job) is remote)\b/i
const HYBRID_STRONG = /\b(hybrid(?:\s+(?:role|position|work|model|schedule))?)\b/i
const ONSITE_STRONG = /\b(on[- ]?site|in[- ]office|in the office|onsite)\b/i

// A bare mode word — only trusted on a short job-header line, never in prose.
const BARE_MODE = /\b(remote|hybrid|on[-\s]?site)\b/i

function normalizeMode(raw) {
  const value = String(raw || '').toLowerCase().replace(/[\s-]/g, '')
  if (value.startsWith('remote') || value.startsWith('telecommut')) return 'Remote'
  if (value.startsWith('hybrid')) return 'Hybrid'
  if (value.startsWith('onsite') || value.startsWith('inoffice') || value.startsWith('intheoffice'))
    return 'On-site'
  if (value.startsWith('workfromhome') || value.startsWith('fullyremote')) return 'Remote'
  if (value.includes('remote')) return 'Remote'
  return ''
}

// ---------------------------------------------------------------------------
// Job-description block selection
// ---------------------------------------------------------------------------

const SEMANTIC_BONUS = { main: 6, article: 6, section: 2, div: 0 }

/**
 * Score one candidate block as "is this the job description?".
 *
 * Returns a number; higher is better. `-Infinity` means disqualified outright.
 * The signals are DOM-semantic (tag, link density, list/paragraph density,
 * containment of the role heading) with vocabulary used only as a tiebreaker,
 * so this generalises past the site it was debugged on.
 */
export function scoreDescriptionBlock(block, { roleHint = '' } = {}) {
  const text = String(block?.text || '')
  const length = text.length

  // Site chrome is never the description, however good it otherwise looks.
  if (block?.inNavLike) return -Infinity
  // Too short to be a job description; too long means we grabbed the page.
  if (length < 180 || length > 40000) return -Infinity

  let score = 0

  score += SEMANTIC_BONUS[String(block.tag || '').toLowerCase()] ?? 0
  if (block.role === 'main') score += 4

  // Real JD section headings are the single strongest positive signal.
  const headings = Array.isArray(block.headings) ? block.headings : []
  const sectionHeadings = headings.filter((heading) => JD_SECTION.test(heading))
  score += Math.min(sectionHeadings.length, 5) * 6
  // Some pages use bold paragraphs instead of real headings, so also look at the
  // text itself — worth less, because prose can mention "requirements" casually.
  const bodyMatches = text.match(new RegExp(JD_SECTION, 'gi')) || []
  score += Math.min(bodyMatches.length, 4) * 2

  // The block that contains the job title is very likely the job block.
  if (roleHint && text.toLowerCase().includes(roleHint.toLowerCase())) score += 5

  // Link density: navigation is mostly links, a description is mostly prose.
  const linkTextLength = Number(block.linkTextLength || 0)
  const linkRatio = length > 0 ? linkTextLength / length : 0
  if (linkRatio > 0.5) return -Infinity
  if (linkRatio > 0.3) score -= 10
  else if (linkRatio < 0.1) score += 3

  const linkCount = Number(block.linkCount || 0)
  if (linkCount > 40) score -= 8

  // Prose/list structure.
  score += Math.min(Number(block.paragraphCount || 0), 10)
  score += Math.min(Number(block.listItemCount || 0), 15) * 0.5

  // Nav vocabulary near the very start is a strong "this is a menu" signal.
  if (NAV_VOCAB.test(text.slice(0, 400))) score -= 12

  // Legal/accessibility/contact boilerplate, wherever in the block it
  // appears — unlike the NAV_VOCAB check above (start-of-block only), a
  // legal or accessibility statement page can open with ordinary-looking
  // prose and only reveal itself a paragraph in.
  const siteInfoMatches = text.match(new RegExp(SITE_INFO_VOCAB.source, 'gi')) || []
  if (siteInfoMatches.length >= 2) return -Infinity
  score -= siteInfoMatches.length * 8

  // URL-heavy content (sitemaps, contact/footer link lists) is not prose.
  const urlMatches = text.match(new RegExp(URL_LIKE.source, 'gi')) || []
  if (urlMatches.length >= 3) return -Infinity
  score -= urlMatches.length * 4

  // Prefer a comfortable description length.
  if (length >= 600 && length <= 20000) score += 4

  return score
}

/**
 * Pick the best description block from the snapshot's candidates.
 *
 * Ties break toward the SHORTER block: when a section and its wrapper both look
 * like the description, the tighter one carries less surrounding chrome.
 */
export function selectDescriptionBlock(blocks, { roleHint = '' } = {}) {
  const scored = (blocks || [])
    .map((block) => ({ block, score: scoreDescriptionBlock(block, { roleHint }) }))
    .filter((entry) => entry.score > 0)

  if (scored.length === 0) return null

  scored.sort((a, b) => {
    if (b.score !== a.score) return b.score - a.score
    return String(a.block.text).length - String(b.block.text).length
  })

  const best = scored[0]

  // If a materially shorter candidate scores nearly as well and is contained in
  // the winner, prefer it — same content, less wrapper.
  const bestText = String(best.block.text)
  const tighter = scored.find((entry) => {
    if (entry === best) return false
    if (entry.score < best.score - 4) return false
    const text = String(entry.block.text)
    return text.length < bestText.length * 0.9 && bestText.includes(text.slice(0, 200))
  })

  return (tighter || best).block
}

// A short, punctuation-free line that looks like the job title repeated as a
// header, rather than a sentence that happens to use a job word.
function looksLikeStandaloneTitle(line) {
  if (line.length >= 100) return false
  if (/[.!?]/.test(line)) return false
  if (/\b(we|you|our|the role|apply|join us|team)\b/i.test(line)) return false
  return looksLikeJobTitle(line)
}

// "R&D | Full-time | Job ID: REF123" — a short pipe/bullet-separated meta
// row, not a real sentence.
function looksLikeMetaRow(line) {
  if (line.length >= 120) return false
  if (/[.!?]/.test(line)) return false
  if (JD_SECTION.test(line)) return false
  return /[|•·]/.test(line)
}

// Any line that belongs to the job's header chrome (title/location/id/back
// link) rather than the description body.
function isHeaderChromeLine(line) {
  return (
    CTA_LINE.test(line) ||
    JOB_ID_LINE.test(line) ||
    looksLikeLocationLine(line) ||
    looksLikeStandaloneTitle(line) ||
    looksLikeMetaRow(line)
  )
}

function toLines(text) {
  return normalizeWhitespace(text)
    .split('\n')
    .map((line) => line.trim())
}

/**
 * Split off the LEADING run of header-chrome lines (title/location/job-id/back
 * link — the same short rows `collect.js` tries to isolate as `headerLines`),
 * stopping at the first real JD section heading or the first line that isn't
 * chrome-shaped. Deliberately only looks at the front of the text: prose
 * further down is never reinterpreted as a header row.
 */
function splitHeaderChrome(lines) {
  const chrome = []
  let index = 0
  while (index < lines.length) {
    const line = lines[index]
    if (!line) {
      index += 1
      continue
    }
    if (JD_SECTION.test(line)) break
    if (!isHeaderChromeLine(line)) break
    chrome.push(line)
    index += 1
  }
  return { chrome, remainder: lines.slice(index) }
}

/**
 * The leading header-chrome lines from a description candidate (JSON-LD text
 * or a DOM block's text), for use as extra `headerLines` when the page's own
 * header container (see `collect.js`) did not capture them. Conservative by
 * construction: only the front of the text is inspected, and each line must
 * independently look like chrome (see `isHeaderChromeLine`).
 */
export function leadingHeaderChromeLines(text) {
  return splitHeaderChrome(toLines(text)).chrome
}

/**
 * Tidy a description for storage: drop leading header chrome, CTA buttons and
 * leftover nav lines, collapse repeated blocks, and bound the length.
 */
export function cleanDescription(text, { maxLength = 20000 } = {}) {
  const { remainder: lines } = splitHeaderChrome(toLines(text))

  const kept = []
  const seen = new Set()
  for (const line of lines) {
    if (!line) {
      if (kept.length && kept[kept.length - 1] !== '') kept.push('')
      continue
    }
    if (CTA_LINE.test(line)) continue
    // Drop short, link-like nav leftovers ("Products", "Support") but never a
    // real sentence or a heading that carries JD meaning.
    if (line.length < 30 && NAV_VOCAB.test(line) && !JD_SECTION.test(line)) continue

    // Collapse a section repeated verbatim (common when a page renders the JD
    // twice for mobile/desktop layouts).
    const key = line.toLowerCase()
    if (line.length > 40) {
      if (seen.has(key)) continue
      seen.add(key)
    }
    kept.push(line)
  }

  let result = kept.join('\n').replace(/\n{3,}/g, '\n\n').trim()
  if (result.length > maxLength) result = result.slice(0, maxLength).trim()
  return result
}

// ---------------------------------------------------------------------------
// Company
// ---------------------------------------------------------------------------

/** Lowercase alphanumeric form, for comparing a name against a domain label. */
export function normalizeName(text) {
  return String(text || '').toLowerCase().replace(/[^a-z0-9]/g, '')
}

/**
 * The registrable-ish label of a host, ignoring common site prefixes.
 * `careers.acmesecurity.example` -> `acmesecurity`
 */
export function companyDomainLabel(url) {
  let host = ''
  try {
    host = new URL(url).hostname.toLowerCase()
  } catch {
    return ''
  }
  const parts = host.split('.').filter(Boolean)
  // Drop leading site-role prefixes and the public suffix.
  const ignoredPrefix = /^(www|careers?|jobs?|apply|talent|recruiting|hire|hiring|work|my|about)$/
  const meaningful = parts.filter((part, index) => !(index === 0 && ignoredPrefix.test(part)))
  if (meaningful.length === 0) return ''
  // Drop the TLD (and a second-level public suffix like `co.uk`).
  const trimmed = meaningful.slice(0, Math.max(1, meaningful.length - 1))
  const label = trimmed[trimmed.length - 1] || ''
  return ignoredPrefix.test(label) ? '' : label
}

/**
 * Company, in confidence order:
 *   1. JSON-LD hiringOrganization.name
 *   2. a page-visible name confirmed by the site's own domain
 *   3. site metadata (og:site_name)
 *   4. a confident title-segment guess
 *   5. '' — the user fills it in
 *
 * Step 2 is what turns `careers.acmesecurity.example` into "Acme Security"
 * without a hardcoded company map: the domain does not supply the name, it
 * only *confirms* a spelling the page already shows, so capitalisation and
 * spacing stay correct.
 */
export function extractCompany({ jobPosting, meta = {}, title = '', url = '', headings = [], titleGuess = '' }) {
  const fromJsonLd = usableText(jobPosting?.hiringOrganization?.name)
  if (fromJsonLd) return { value: stripCompanyNoise(fromJsonLd) || fromJsonLd, source: 'jsonld' }

  const label = companyDomainLabel(url)
  if (label) {
    // Candidate names the page itself displays, cheapest/most reliable first.
    const candidates = [
      usableText(meta['og:site_name']),
      usableText(meta['application-name']),
      ...String(title || '')
        .split(/\s*[|\-–—·:]\s*/)
        .map((segment) => segment.trim()),
      ...headings,
    ].filter(Boolean)

    for (const candidate of candidates) {
      const cleaned = stripCompanyNoise(candidate)
      if (!cleaned || cleaned.length > 60) continue
      const normalized = normalizeName(cleaned)
      // Exact match, or the candidate starts with the domain label (so
      // "Acme Security Software" still matches `acmesecurity`).
      if (normalized === label || normalized.startsWith(label)) {
        return { value: cleaned, source: 'domain-confirmed' }
      }
    }
  }

  const siteName = stripCompanyNoise(usableText(meta['og:site_name']))
  if (siteName && !looksLikeJobTitle(siteName) && siteName.length <= 60) {
    return { value: siteName, source: 'metadata' }
  }

  if (titleGuess && !looksLikeJobTitle(titleGuess)) {
    return { value: titleGuess, source: 'title' }
  }

  return { value: '', source: 'none' }
}

// ---------------------------------------------------------------------------
// Role
// ---------------------------------------------------------------------------

/**
 * Remove a trailing bracketed identifier that came from the URL rather than the
 * visible title — e.g. `Threat Intelligence Analyst (9988776)` where
 * 9988776 is the `joborderid` query parameter.
 *
 * The identifier is only stripped when it actually appears in the URL, so a
 * genuine title like `Analyst (Cyber)` or a real visible ref is left alone.
 */
export function stripUrlIdentifier(role, url) {
  const value = String(role || '').trim()
  if (!value || !url) return value

  const match = value.match(/^(.*?)[\s]*[([{]\s*([A-Za-z0-9_-]{3,})\s*[)\]}]\s*$/)
  if (!match) return value

  const [, base, identifier] = match
  if (!base.trim()) return value

  // Only strip when the URL contains this exact token — that is the evidence
  // that it is a system id and not part of the human title.
  const inUrl = new RegExp(`(?:^|[^A-Za-z0-9])${escapeRegExp(identifier)}(?:[^A-Za-z0-9]|$)`).test(
    String(url),
  )
  return inUrl ? base.trim() : value
}

function escapeRegExp(text) {
  return String(text).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

/**
 * Role, in confidence order:
 *   1. JSON-LD title
 *   2. a job-title-looking heading from the job content
 *   3. a confident title-segment guess
 *   4. ''
 * URL identifiers are stripped at every step.
 */
export function extractRole({ jobPosting, headings = [], titleGuess = '', url = '', meta = {} }) {
  const fromJsonLd = usableText(jobPosting?.title)
  if (fromJsonLd) return { value: stripUrlIdentifier(fromJsonLd, url), source: 'jsonld' }

  const headingRole = headings.find(
    (heading) => looksLikeJobTitle(heading) && !isMarketingHeading(heading),
  )
  if (headingRole) return { value: stripUrlIdentifier(headingRole, url), source: 'heading' }

  if (titleGuess) return { value: stripUrlIdentifier(titleGuess, url), source: 'title' }

  const ogTitle = usableText(meta['og:title'])
  if (ogTitle && looksLikeJobTitle(ogTitle)) {
    return { value: stripUrlIdentifier(ogTitle, url), source: 'metadata' }
  }

  return { value: '', source: 'none' }
}

// ---------------------------------------------------------------------------
// Location
// ---------------------------------------------------------------------------

/**
 * A job-header location line: "Tel Aviv, Israel", "Israel: Tel Aviv-Yafo",
 * "Location: Tel Aviv". Accepts comma OR colon separation (the colon form is
 * what the current code misses), rejects sentences and nav vocabulary.
 */
export function looksLikeLocationLine(text) {
  const original = String(text || '').trim()
  const value = cleanLocationLabel(original)
  // An explicit "Location:" label is itself the evidence, so a single-city
  // value like "Location: Tel Aviv" does not additionally need a separator.
  const wasLabelled = value !== original

  if (value.length < 2 || value.length > 80) return false
  if (!wasLabelled && !/[,:]/.test(value) && !/\b(remote|anywhere)\b/i.test(value)) return false
  // Sentences are not locations.
  if (/[.!?]\s/.test(value) || /\b(we|you|our|the role|apply)\b/i.test(value)) return false
  if (NAV_VOCAB.test(value) || SITE_INFO_VOCAB.test(value)) return false
  // URLs and email addresses are never a location, whatever shape they take.
  if (URL_LIKE.test(value) || EMAIL_LIKE.test(value)) return false
  return /^[\p{L}0-9 .,:'’()/&\-]+$/u.test(value)
}

/** Strip a trailing work-mode marker: "Tel Aviv (Hybrid)" -> "Tel Aviv". */
export function stripModeFromLocation(text) {
  return String(text || '')
    .replace(/[\s([{|\-–—•·,]*\b(remote|hybrid|on[-\s]?site|onsite)\b[)\]}\s]*$/i, '')
    .replace(/[\s,;:|\-–—•·]+$/, '')
    .trim()
}

function addressFromJsonLd(jobPosting) {
  const raw = jobPosting?.jobLocation
  const first = Array.isArray(raw) ? raw[0] : raw
  const address = first?.address || first
  if (!address || typeof address !== 'object') return ''

  const parts = [
    usableText(address.addressLocality),
    usableText(address.addressRegion),
    usableText(address.addressCountry?.name ?? address.addressCountry),
  ].filter(Boolean)

  if (parts.length) return parts.join(', ')
  return usableText(address.name)
}

/**
 * Location, in confidence order:
 *   1. JSON-LD jobLocation address
 *   2. a location-shaped line in the job header / job content
 *   3. an explicitly labelled "Location: …" line anywhere in job content
 *   4. ''
 */
export function extractLocation({ jobPosting, headerLines = [], descriptionLines = [] }) {
  const fromJsonLd = addressFromJsonLd(jobPosting)
  if (fromJsonLd) return { value: fromJsonLd, source: 'jsonld' }

  for (const line of headerLines) {
    const candidate = stripModeFromLocation(line)
    if (looksLikeLocationLine(candidate)) {
      return { value: cleanLocationLabel(candidate), source: 'header' }
    }
  }

  for (const line of descriptionLines) {
    if (!/^(location|based in|work location|office)\s*[:\-–]/i.test(line)) continue
    const candidate = stripModeFromLocation(line)
    if (looksLikeLocationLine(candidate)) {
      return { value: cleanLocationLabel(candidate), source: 'labelled' }
    }
  }

  return { value: '', source: 'none' }
}

function cleanLocationLabel(text) {
  return String(text || '')
    .replace(/^(location|based in|office|work location|city)\s*[:\-–]\s*/i, '')
    .trim()
}

// ---------------------------------------------------------------------------
// Work mode  (conservative by design)
// ---------------------------------------------------------------------------

/**
 * Work mode, in confidence order:
 *   1. JSON-LD jobLocationType === TELECOMMUTE
 *   2. a bare mode word on a short job-header line ("Tel Aviv, Israel — Remote")
 *   3. an explicit phrase in the job description ("fully remote", "hybrid role")
 *   4. '' — unknown
 *
 * Nothing outside the job header and the selected description is ever consulted.
 * That is the fix for a real regression where a site's own navigation
 * advertised a "Remote Access VPN" product: a bare "remote" in page chrome, or
 * anywhere in prose, is not evidence about the job.
 */
export function extractWorkMode({ jobPosting, headerLines = [], description = '' }) {
  const locationType = jobPosting?.jobLocationType
  const types = Array.isArray(locationType) ? locationType : [locationType]
  if (types.some((entry) => String(entry || '').toUpperCase().includes('TELECOMMUTE'))) {
    return { value: 'Remote', source: 'jsonld' }
  }

  // A short header line is high-confidence: it is the job's own summary row.
  for (const line of headerLines) {
    if (line.length > 90) continue
    const match = line.match(BARE_MODE)
    if (match) return { value: normalizeMode(match[1]), source: 'header' }
  }

  // In prose, require an unambiguous phrase — never a bare "remote".
  const text = String(description || '')
  const remote = text.match(REMOTE_STRONG)
  if (remote) return { value: 'Remote', source: 'description' }
  const hybrid = text.match(HYBRID_STRONG)
  if (hybrid) return { value: 'Hybrid', source: 'description' }
  const onsite = text.match(ONSITE_STRONG)
  if (onsite) return { value: 'On-site', source: 'description' }

  return { value: '', source: 'none' }
}

// ---------------------------------------------------------------------------
// Visible job identifier (informational only — not part of the role)
// ---------------------------------------------------------------------------

/** A visible job/requisition id from JSON-LD or the job header, if shown. */
export function extractJobId({ jobPosting, headerLines = [], descriptionLines = [] }) {
  const identifier = jobPosting?.identifier
  const fromJsonLd =
    usableText(typeof identifier === 'object' ? identifier?.value : identifier) ||
    usableText(jobPosting?.jobPostingId)
  if (fromJsonLd) return fromJsonLd

  for (const line of [...headerLines, ...descriptionLines]) {
    const match = String(line).match(JOB_ID_LINE)
    if (match) return match[2]
  }
  return ''
}

// ---------------------------------------------------------------------------
// Top-level pipeline
// ---------------------------------------------------------------------------

/**
 * The job's own header context: non-navigation headings plus the short rows the
 * page prints under the job title.
 *
 * Deliberately excludes description prose. A bare "remote" is trusted on one of
 * these lines because a header row is the job's own summary — but prose can say
 * "remote access" about a product, so prose must clear a much higher bar (see
 * extractWorkMode).
 */
function buildHeaderLines(snapshot) {
  const headings = snapshot.headings || []
  const jobHeadings = headings
    .filter((heading) => (typeof heading === 'object' ? !heading.inNavLike : true))
    .map((heading) => (typeof heading === 'string' ? heading : heading.text))

  return [...jobHeadings, ...(snapshot.headerLines || [])]
    .filter(Boolean)
    .map((line) => String(line).trim())
}

/**
 * Extract everything the capture popup needs from a page snapshot.
 *
 * Structured data first, page heuristics second, blank last. Every returned
 * field is a best-effort guess the user can edit before saving.
 */
export function extractJobPosting(snapshot = {}, { titleGuess = {} } = {}) {
  const rawJsonLd = snapshot.jsonLd || []
  const jobPosting = findJobPosting(rawJsonLd)
  const meta = snapshot.meta || {}
  const url = snapshot.url || ''

  // --- description -------------------------------------------------------
  const jsonLdDescription = usableText(jobPosting?.description)
  let description = ''
  let descriptionSource = 'none'
  // Header-like lines (title/location/job-id) found at the front of whichever
  // content the description came from — merged into `headerLines` below so
  // location/work-mode/job-id extraction can see them even when the page's own
  // header container (`collect.js`) did not capture them separately.
  let contentHeaderLines = []

  if (jsonLdDescription) {
    const rawText = htmlToText(jsonLdDescription)
    description = cleanDescription(rawText)
    descriptionSource = 'jsonld'
    contentHeaderLines = leadingHeaderChromeLines(rawText)
  }

  const headingTextsForHint = (snapshot.headings || [])
    .map((heading) => (typeof heading === 'string' ? heading : heading?.text))
    .filter(Boolean)
  const roleHint =
    usableText(jobPosting?.title) ||
    headingTextsForHint.find((heading) => looksLikeJobTitle(heading) && !isMarketingHeading(heading)) ||
    ''

  if (!description) {
    const block = selectDescriptionBlock(snapshot.blocks || [], { roleHint })
    if (block) {
      description = cleanDescription(block.text)
      descriptionSource = 'dom'
      contentHeaderLines = leadingHeaderChromeLines(block.text)
    }
  }

  // Last resort only: the user's own selection, then raw page text. Raw text is
  // known to carry navigation, so it is never preferred over a real candidate.
  if (!description && usableText(snapshot.selectionText)) {
    description = cleanDescription(snapshot.selectionText)
    descriptionSource = 'selection'
  }
  if (!description && usableText(snapshot.bodyText)) {
    description = cleanDescription(snapshot.bodyText)
    descriptionSource = 'body-fallback'
  }

  // --- header context ----------------------------------------------------
  const headerLines = [...buildHeaderLines(snapshot), ...contentHeaderLines]
  const descriptionLines = String(description || '')
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)

  // --- fields ------------------------------------------------------------
  const jobHeadingTexts = (snapshot.headings || [])
    .filter((heading) => (typeof heading === 'object' ? !heading.inNavLike : true))
    .map((heading) => (typeof heading === 'string' ? heading : heading.text))
    .filter(Boolean)

  const role = extractRole({
    jobPosting,
    headings: jobHeadingTexts,
    titleGuess: titleGuess.role || '',
    url,
    meta,
  })

  const company = extractCompany({
    jobPosting,
    meta,
    title: snapshot.title || '',
    url,
    headings: jobHeadingTexts,
    titleGuess: titleGuess.company || '',
  })

  const location = extractLocation({ jobPosting, headerLines, descriptionLines })
  const workMode = extractWorkMode({ jobPosting, headerLines, description })
  const jobId = extractJobId({ jobPosting, headerLines, descriptionLines })

  return {
    company: company.value,
    role: role.value,
    location: location.value,
    work_mode: workMode.value,
    job_description: description,
    job_id: jobId,
    // Where each value came from — shown nowhere, but invaluable when debugging
    // a bad capture on a new site.
    sources: {
      company: company.source,
      role: role.source,
      location: location.source,
      work_mode: workMode.source,
      job_description: descriptionSource,
      structured: Boolean(jobPosting),
    },
  }
}
