// The in-page DOM walk that produces a "page snapshot".
//
// IMPORTANT: `collectPageSnapshot` is injected into the page by
// `chrome.scripting.executeScript`, which serialises the function source. It
// must therefore be completely self-contained — no imports, no closures, no
// references to anything in this module. Everything it needs is defined inside.
//
// This layer is deliberately dumb. It reports observable facts about the DOM
// (tag, ancestry, text, link density, headings) and makes no judgement about
// what any of it means. All judgement lives in extract.js, which is pure and
// testable without a browser.

/**
 * Collect a structured snapshot of the current page.
 *
 * @returns {{
 *   title: string, url: string, jsonLd: string[], meta: Record<string,string>,
 *   headings: {level:number, text:string, inNavLike:boolean}[],
 *   blocks: {tag:string, role:string|null, inNavLike:boolean, text:string,
 *            linkCount:number, linkTextLength:number, listItemCount:number,
 *            paragraphCount:number, headings:string[]}[],
 *   selectionText: string, bodyText: string, usedSelection: boolean
 * }}
 */
export function collectPageSnapshot() {
  // --- limits: the snapshot crosses a serialisation boundary, so keep it sane
  const MAX_BLOCKS = 40
  const MAX_BLOCK_TEXT = 30000
  const MAX_BODY_TEXT = 50000
  const MIN_BLOCK_TEXT = 180

  const readText = (element) => {
    if (!element) return ''
    // innerText respects visibility, so hidden menus and off-screen content are
    // excluded — textContent would include them.
    const text = element.innerText || element.textContent || ''
    return String(text).trim()
  }

  const isNavLike = (element) => {
    let node = element
    let depth = 0
    while (node && node !== document.body && depth < 30) {
      const tag = (node.tagName || '').toLowerCase()
      if (tag === 'nav' || tag === 'header' || tag === 'footer' || tag === 'aside') return true
      const role = (node.getAttribute && node.getAttribute('role')) || ''
      if (role === 'navigation' || role === 'banner' || role === 'contentinfo') return true
      node = node.parentElement
      depth += 1
    }
    return false
  }

  // --- JSON-LD (raw strings; parsing happens in the pure layer) -------------
  const jsonLd = []
  document.querySelectorAll('script[type="application/ld+json"]').forEach((node) => {
    const raw = (node.textContent || '').trim()
    if (raw && raw.length < 200000) jsonLd.push(raw)
  })

  // --- metadata -------------------------------------------------------------
  const meta = {}
  const wanted = ['og:site_name', 'og:title', 'og:description', 'application-name']
  document.querySelectorAll('meta[property], meta[name]').forEach((node) => {
    const key = (node.getAttribute('property') || node.getAttribute('name') || '').toLowerCase()
    if (!wanted.includes(key)) return
    const content = (node.getAttribute('content') || '').trim()
    if (content && !meta[key]) meta[key] = content
  })

  // --- headings -------------------------------------------------------------
  const headings = []
  document.querySelectorAll('h1, h2, h3').forEach((node) => {
    if (headings.length >= 25) return
    const text = readText(node)
    if (!text || text.length > 200) return
    headings.push({
      level: Number((node.tagName || 'H3').slice(1)) || 3,
      text,
      inNavLike: isNavLike(node),
    })
  })

  // --- candidate content blocks --------------------------------------------
  const blocks = []
  const seenText = new Set()
  const candidates = document.querySelectorAll('main, article, [role="main"], section, div')

  for (const element of candidates) {
    if (blocks.length >= MAX_BLOCKS) break

    const text = readText(element)
    if (text.length < MIN_BLOCK_TEXT) continue

    // Skip a block whose text we already recorded (a wrapper with a single
    // child produces identical text; keeping both wastes the budget).
    const fingerprint = text.slice(0, 300)
    if (seenText.has(fingerprint)) continue
    seenText.add(fingerprint)

    const links = element.querySelectorAll('a')
    let linkTextLength = 0
    links.forEach((link) => {
      linkTextLength += ((link.innerText || link.textContent || '') + '').trim().length
    })

    const blockHeadings = []
    element.querySelectorAll('h1, h2, h3, h4').forEach((node) => {
      if (blockHeadings.length >= 20) return
      const headingText = readText(node)
      if (headingText && headingText.length <= 200) blockHeadings.push(headingText)
    })

    blocks.push({
      tag: (element.tagName || '').toLowerCase(),
      role: (element.getAttribute && element.getAttribute('role')) || null,
      inNavLike: isNavLike(element),
      text: text.slice(0, MAX_BLOCK_TEXT),
      linkCount: links.length,
      linkTextLength,
      listItemCount: element.querySelectorAll('li').length,
      paragraphCount: element.querySelectorAll('p').length,
      headings: blockHeadings,
    })
  }

  // --- header lines: the short rows right under the job title ---------------
  // Generic: the first few non-empty lines of the highest-priority heading's
  // nearest container, which is where ATS pages put location/job id/type.
  const headerLines = []
  const firstHeading = document.querySelector('h1')
  if (firstHeading) {
    const container = firstHeading.parentElement || firstHeading
    readText(container)
      .split('\n')
      .map((line) => line.trim())
      .filter(Boolean)
      .slice(0, 12)
      .forEach((line) => {
        if (line.length <= 120) headerLines.push(line)
      })
  }

  const selection = window.getSelection ? String(window.getSelection()).trim() : ''

  return {
    title: document.title || '',
    url: location.href || '',
    jsonLd,
    meta,
    headings,
    blocks,
    headerLines,
    selectionText: selection.slice(0, MAX_BODY_TEXT),
    bodyText: (document.body ? readText(document.body) : '').slice(0, MAX_BODY_TEXT),
    usedSelection: Boolean(selection),
  }
}
