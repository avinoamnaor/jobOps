// JobOps Capture — Part B.
//
// Capture a job page, best-effort guess Company/Role (all editable), and hand the
// values to the JobOps New Application form. This does NOT create an application:
// JobOps stays the confirmation point.
//
// Handoff: the values are passed in the opened tab's URL *hash* (not a query
// string, so nothing reaches a server) and JobOps strips them from history the
// moment it reads them.

import { collectPageSnapshot } from './collect.js'
import { extractJobPosting } from './extract.js'
import { guessApplicationChannel, guessCompanyAndRole } from './guess.js'

const JOBOPS_URL = 'http://localhost:5173'

const companyEl = document.getElementById('company')
const roleEl = document.getElementById('role')
const locationEl = document.getElementById('location')
const workModeEl = document.getElementById('work_mode')
const channelEl = document.getElementById('channel')
const urlEl = document.getElementById('url')
const textEl = document.getElementById('text')
const titleEl = document.getElementById('title')
const statusEl = document.getElementById('status')
const openButton = document.getElementById('open')
const recaptureButton = document.getElementById('recapture')

function setStatus(message) {
  statusEl.textContent = message
}

/**
 * A short, honest summary of how the capture went.
 *
 * Says where the description came from, because "structured job data" and "our
 * best guess at which part of the page is the job" deserve different levels of
 * trust from the person about to save it.
 */
function describeCapture(snapshot, extracted) {
  if (snapshot.usedSelection) {
    return 'Captured your selection. All fields are editable — check them before opening JobOps.'
  }

  const source = extracted.sources.job_description
  const lead =
    source === 'jsonld'
      ? 'Read structured job data from the page.'
      : source === 'dom'
        ? 'Found the job description on the page.'
        : source === 'body-fallback'
          ? "Couldn't isolate the job description — captured the page text, so trim it."
          : 'No job description found — paste or select it, then Recapture.'

  const missing = [
    !extracted.company && 'company',
    !extracted.role && 'role',
    !extracted.location && 'location',
  ].filter(Boolean)

  const gap = missing.length ? ` Couldn't determine ${missing.join(', ')}.` : ''
  return `${lead}${gap} All fields are editable — check them before opening JobOps.`
}

async function capture() {
  setStatus('Capturing…')
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true })
    if (!tab || tab.id === undefined) {
      setStatus('No active tab to capture.')
      return
    }

    const results = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      // Injected into the page: must stay self-contained (see collect.js).
      func: collectPageSnapshot,
    })
    const snapshot = results && results[0] ? results[0].result : null
    if (!snapshot) {
      setStatus("Couldn't read this page.")
      return
    }

    // The page title is still a useful weak signal, so it feeds the extractor as
    // a *fallback* — structured data and job-content headings outrank it.
    const titleGuess = guessCompanyAndRole({
      title: snapshot.title,
      headings: (snapshot.headings || [])
        .filter((heading) => !heading.inNavLike)
        .map((heading) => heading.text),
    })

    const extracted = extractJobPosting(snapshot, { titleGuess })

    titleEl.value = snapshot.title
    urlEl.value = snapshot.url
    // A deliberate selection always wins: if the user highlighted the JD, that
    // is the strongest possible signal about what they want captured.
    textEl.value = snapshot.usedSelection ? snapshot.selectionText : extracted.job_description
    companyEl.value = extracted.company
    roleEl.value = extracted.role
    locationEl.value = extracted.location
    workModeEl.value = extracted.work_mode
    channelEl.value = guessApplicationChannel(snapshot.url)

    setStatus(describeCapture(snapshot, extracted))
  } catch (error) {
    setStatus("Chrome won't let the extension read this page (e.g. chrome:// or store pages).")
  }
}

async function jobOpsReachable() {
  try {
    // no-cors: we only need to know the dev server answers, not read the body.
    await fetch(`${JOBOPS_URL}/`, { mode: 'no-cors', cache: 'no-store' })
    return true
  } catch (error) {
    return false
  }
}

async function openInJobOps() {
  // Send exactly what's in the fields now, including any edits.
  const capture = {
    company_name: companyEl.value.trim(),
    role_title: roleEl.value.trim(),
    location: locationEl.value.trim(),
    work_mode: workModeEl.value.trim(),
    application_channel: channelEl.value,
    job_url: urlEl.value.trim(),
    job_description: textEl.value,
  }

  setStatus('Checking JobOps…')
  if (!(await jobOpsReachable())) {
    setStatus(`JobOps isn't running at ${JOBOPS_URL}. Start the frontend (npm run dev) and try again.`)
    return
  }

  const encoded = encodeURIComponent(JSON.stringify(capture))
  await chrome.tabs.create({ url: `${JOBOPS_URL}/applications/new#jobops=${encoded}` })
  window.close()
}

recaptureButton.addEventListener('click', capture)
openButton.addEventListener('click', openInJobOps)

// Script tag sits at the end of <body>, so the DOM is ready — capture now.
capture()
