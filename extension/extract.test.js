import assert from 'node:assert/strict'
import { test } from 'node:test'

import {
  cleanDescription,
  companyDomainLabel,
  decodeEntities,
  extractCompany,
  extractJobPosting,
  extractLocation,
  extractWorkMode,
  findJobPosting,
  htmlToText,
  leadingHeaderChromeLines,
  looksLikeLocationLine,
  parseJsonLdObjects,
  scoreDescriptionBlock,
  selectDescriptionBlock,
  stripUrlIdentifier,
} from './extract.js'
import { LEGACY_ATS_URL, legacyAtsSnapshot } from './fixtures/legacy-ats-page.js'

// ---------------------------------------------------------------------------
// The legacy-ATS regression — every symptom from a real page found during
// manual testing (identifiers in the fixture are fictional; see
// fixtures/legacy-ats-page.js for what shape of page this models)
// ---------------------------------------------------------------------------

test('legacy ATS: company is detected', () => {
  const result = extractJobPosting(legacyAtsSnapshot)
  assert.equal(result.company, 'Acme Security')
})

test('legacy ATS: role is clean and never carries the URL joborderid', () => {
  const result = extractJobPosting(legacyAtsSnapshot)
  assert.equal(result.role, 'Threat Intelligence Analyst')
  assert.ok(!result.role.includes('9988776'), 'URL id must not leak into the role')
})

test('legacy ATS: colon-separated location is detected', () => {
  const result = extractJobPosting(legacyAtsSnapshot)
  assert.match(result.location, /Tel Aviv-Yafo/)
  assert.match(result.location, /Israel/)
})

test('legacy ATS: work mode is NOT falsely classified as Remote', () => {
  // The site navigation sells a "Remote Access VPN" product. That is not
  // evidence about the job, so the honest answer is "unknown".
  const result = extractJobPosting(legacyAtsSnapshot)
  assert.equal(result.work_mode, '', 'a nav product name must not set work mode')
})

test('legacy ATS: description contains the real job sections', () => {
  const { job_description: jd } = extractJobPosting(legacyAtsSnapshot)
  assert.match(jd, /Why Join Us\?/)
  assert.match(jd, /Key Responsibilities/)
  assert.match(jd, /Qualifications/)
  assert.match(jd, /threat actors/)
})

test('legacy ATS: description excludes the navigation menu', () => {
  const { job_description: jd } = extractJobPosting(legacyAtsSnapshot)
  assert.ok(!jd.startsWith('Products'), 'JD must not start with the nav menu')
  assert.ok(!jd.includes('NETWORK & SASE'), 'nav must not appear in the JD')
  assert.ok(!jd.includes('DDoS Protection'), 'nav must not appear in the JD')
  assert.ok(!jd.includes('Next-generation Firewalls'), 'nav must not appear in the JD')
})

test('legacy ATS: description excludes footer boilerplate and the Apply button', () => {
  const { job_description: jd } = extractJobPosting(legacyAtsSnapshot)
  assert.ok(!jd.includes('All rights reserved'), 'footer must not appear in the JD')
  assert.ok(!/^Apply Now$/m.test(jd), 'the Apply button is not part of the JD')
})

test('legacy ATS: the visible job id is kept separately, not glued to the role', () => {
  const result = extractJobPosting(legacyAtsSnapshot)
  assert.equal(result.job_id, 'REF123')
  assert.ok(!result.role.includes('REF123'))
})

test('legacy ATS: the description block is preferred over the whole-page wrapper', () => {
  const result = extractJobPosting(legacyAtsSnapshot)
  assert.equal(result.sources.job_description, 'dom')
  assert.equal(result.sources.structured, false)
})

test('legacy ATS: the JD excludes header chrome and starts at the real content', () => {
  // The live page's H1 container only wraps the title (see
  // fixtures/legacy-ats-page.js), so "Back to Job Openings", the title, the
  // location and the meta row all land inside the selected job block ahead of
  // the actual description.
  const { job_description: jd } = extractJobPosting(legacyAtsSnapshot)
  assert.ok(!jd.includes('Back to Job Openings'), 'the back-link is header chrome, not JD content')
  assert.ok(!/^Threat Intelligence Analyst/.test(jd), 'the JD should not re-open with the title')
  assert.ok(!jd.includes('R&D | Full-time | Job ID'), 'the meta row is header chrome, not JD content')
  assert.ok(jd.trimStart().startsWith('Why Join Us?'), 'the JD should begin at the real content')
  assert.ok(!jd.includes('Israel: Tel Aviv-Yafo'), 'the location row is header chrome, not JD content')
})

// ---------------------------------------------------------------------------
// JSON-LD
// ---------------------------------------------------------------------------

test('JSON-LD: a plain JobPosting object is found', () => {
  const raw = JSON.stringify({
    '@context': 'https://schema.org',
    '@type': 'JobPosting',
    title: 'Backend Engineer',
  })
  assert.equal(findJobPosting([raw])?.title, 'Backend Engineer')
})

test('JSON-LD: an array of objects is searched', () => {
  const raw = JSON.stringify([
    { '@type': 'Organization', name: 'Acme' },
    { '@type': 'JobPosting', title: 'Data Scientist' },
  ])
  assert.equal(findJobPosting([raw])?.title, 'Data Scientist')
})

test('JSON-LD: @graph nesting is searched', () => {
  const raw = JSON.stringify({
    '@context': 'https://schema.org',
    '@graph': [
      { '@type': 'WebSite', name: 'Careers' },
      { '@type': 'JobPosting', title: 'Platform Engineer' },
    ],
  })
  assert.equal(findJobPosting([raw])?.title, 'Platform Engineer')
})

test('JSON-LD: @type given as an array still matches', () => {
  const raw = JSON.stringify({ '@type': ['Thing', 'JobPosting'], title: 'QA Engineer' })
  assert.equal(findJobPosting([raw])?.title, 'QA Engineer')
})

test('JSON-LD: malformed blocks are skipped, not thrown', () => {
  const good = JSON.stringify({ '@type': 'JobPosting', title: 'SRE' })
  assert.doesNotThrow(() => findJobPosting(['{ not json at all', good]))
  assert.equal(findJobPosting(['{ not json at all', good])?.title, 'SRE')
})

test('JSON-LD: unrelated structured data yields no JobPosting', () => {
  const raw = JSON.stringify({ '@type': 'BreadcrumbList', itemListElement: [] })
  assert.equal(findJobPosting([raw]), null)
  assert.equal(parseJsonLdObjects([raw]).length, 1)
})

test('JSON-LD drives every field when present', () => {
  const raw = JSON.stringify({
    '@type': 'JobPosting',
    title: 'Senior Platform Engineer',
    identifier: { '@type': 'PropertyValue', value: 'REQ-1234' },
    hiringOrganization: { '@type': 'Organization', name: 'BluePeak Systems' },
    jobLocation: {
      '@type': 'Place',
      address: {
        '@type': 'PostalAddress',
        addressLocality: 'Berlin',
        addressCountry: 'Germany',
      },
    },
    description: '<p>Build platforms.</p><ul><li>Own services</li></ul>',
  })

  const result = extractJobPosting({
    title: 'irrelevant page title',
    url: 'https://example.com/jobs/9',
    jsonLd: [raw],
    meta: {},
    headings: [],
    blocks: [],
  })

  assert.equal(result.role, 'Senior Platform Engineer')
  assert.equal(result.company, 'BluePeak Systems')
  assert.equal(result.location, 'Berlin, Germany')
  assert.equal(result.job_id, 'REQ-1234')
  assert.match(result.job_description, /Build platforms\./)
  assert.match(result.job_description, /- Own services/)
  assert.equal(result.sources.structured, true)
})

test('JSON-LD: jobLocationType TELECOMMUTE marks the job Remote', () => {
  const raw = JSON.stringify({
    '@type': 'JobPosting',
    title: 'Remote Engineer',
    jobLocationType: 'TELECOMMUTE',
  })
  const result = extractJobPosting({ url: 'https://example.com', jsonLd: [raw], blocks: [] })
  assert.equal(result.work_mode, 'Remote')
  assert.equal(result.sources.work_mode, 'jsonld')
})

test('JSON-LD: empty/placeholder fields are not trusted', () => {
  const raw = JSON.stringify({
    '@type': 'JobPosting',
    title: '   ',
    hiringOrganization: { name: 'N/A' },
    description: '',
  })
  const result = extractJobPosting({
    title: 'Data Engineer - Acme',
    url: 'https://acme.com/jobs/1',
    jsonLd: [raw],
    blocks: [],
    headings: [{ level: 1, text: 'Data Engineer', inNavLike: false }],
  })
  // Falls through to page signals rather than storing "   " or "N/A".
  assert.equal(result.role, 'Data Engineer')
  assert.notEqual(result.company, 'N/A')
})

// ---------------------------------------------------------------------------
// HTML description handling
// ---------------------------------------------------------------------------

test('htmlToText keeps structure and decodes entities', () => {
  const html =
    '<h3>Responsibilities</h3><ul><li>Ship &amp; own features</li><li>Mentor</li></ul><p>We&#39;re hiring.</p>'
  const text = htmlToText(html)
  assert.match(text, /Responsibilities/)
  assert.match(text, /- Ship & own features/)
  assert.match(text, /- Mentor/)
  assert.match(text, /We're hiring\./)
  assert.ok(!text.includes('<'), 'no tags should survive')
})

test('htmlToText strips script and style content', () => {
  const text = htmlToText('<p>Real</p><script>alert(1)</script><style>.a{color:red}</style>')
  assert.match(text, /Real/)
  assert.ok(!text.includes('alert'))
  assert.ok(!text.includes('color:red'))
})

test('decodeEntities handles named, decimal and hex forms', () => {
  assert.equal(decodeEntities('a &amp; b'), 'a & b')
  assert.equal(decodeEntities('&#65;&#66;'), 'AB')
  assert.equal(decodeEntities('&#x41;&#x42;'), 'AB')
})

test('cleanDescription removes CTAs and repeated sections', () => {
  const messy = [
    'Key Responsibilities',
    'Own the platform end to end and keep it healthy for everyone.',
    'Apply Now',
    'Own the platform end to end and keep it healthy for everyone.',
    'Submit your application',
  ].join('\n')
  const cleaned = cleanDescription(messy)
  assert.equal(cleaned.match(/Own the platform/g).length, 1, 'duplicate block removed')
  assert.ok(!/Apply Now/.test(cleaned))
  assert.ok(!/Submit your application/.test(cleaned))
  assert.match(cleaned, /Key Responsibilities/)
})

test('cleanDescription respects a length limit', () => {
  const cleaned = cleanDescription('x'.repeat(500), { maxLength: 100 })
  assert.equal(cleaned.length, 100)
})

// ---------------------------------------------------------------------------
// Description block selection
// ---------------------------------------------------------------------------

const jobBlock = {
  tag: 'section',
  inNavLike: false,
  text: [
    'About the Role',
    'You will build and operate our data platform for a large engineering org.',
    'Key Responsibilities',
    '- Design pipelines',
    '- Operate services',
    'Qualifications',
    '- 5 years of experience building production systems',
  ].join('\n'),
  linkCount: 1,
  linkTextLength: 8,
  listItemCount: 4,
  paragraphCount: 4,
  headings: ['About the Role', 'Key Responsibilities', 'Qualifications'],
}

const navBlock = {
  tag: 'div',
  inNavLike: true,
  text: 'Products\nSolutions\nSupport\nContact Us\nLog In\n'.repeat(12),
  linkCount: 60,
  linkTextLength: 500,
  listItemCount: 60,
  paragraphCount: 0,
  headings: ['Products', 'Solutions'],
}

test('a navigation block is disqualified outright', () => {
  assert.equal(scoreDescriptionBlock(navBlock), -Infinity)
})

test('a link-dense block is disqualified even outside <nav>', () => {
  const linkFarm = {
    tag: 'div',
    inNavLike: false,
    text: 'Recommended jobs '.repeat(40),
    linkCount: 50,
    linkTextLength: 600,
    listItemCount: 50,
    paragraphCount: 0,
    headings: [],
  }
  assert.equal(scoreDescriptionBlock(linkFarm), -Infinity)
})

test('the job block is selected over navigation and a page wrapper', () => {
  const wrapper = {
    tag: 'div',
    inNavLike: false,
    text: `${navBlock.text}\n${jobBlock.text}`,
    linkCount: 62,
    linkTextLength: 520,
    listItemCount: 64,
    paragraphCount: 4,
    headings: [...navBlock.headings, ...jobBlock.headings],
  }
  const selected = selectDescriptionBlock([navBlock, wrapper, jobBlock])
  assert.equal(selected.text, jobBlock.text)
})

test('an accessibility/legal statement block is disqualified outright', () => {
  const accessibilityBlock = {
    tag: 'div',
    inNavLike: false,
    text: [
      'Accessibility Statement',
      'We are committed to digital accessibility for all users.',
      'This site follows WCAG 2.1 guidelines wherever possible.',
      'Privacy Policy and Cookie Policy govern your use of this site.',
      'For accessibility issues, contact us at accessibility@example.com.',
    ].join('\n\n'.repeat(6)),
    linkCount: 3,
    linkTextLength: 30,
    listItemCount: 0,
    paragraphCount: 4,
    headings: ['Accessibility Statement'],
  }
  assert.ok(accessibilityBlock.text.length > 180, 'fixture must clear the min-length gate')
  assert.equal(scoreDescriptionBlock(accessibilityBlock), -Infinity)
})

test('a URL-heavy sitemap/contact block is disqualified outright', () => {
  const urlBlock = {
    tag: 'div',
    inNavLike: false,
    // Padded well past the 180-char minimum so this exercises the URL-density
    // check itself, not just the "too short to be a description" cutoff.
    text: [
      'Useful links',
      'https://example.com/careers',
      'https://example.com/about',
      'https://example.com/contact',
      'www.example.com/legal',
    ].join('\n\n'.repeat(12)),
    linkCount: 4,
    linkTextLength: 40,
    listItemCount: 4,
    paragraphCount: 0,
    headings: [],
  }
  assert.ok(urlBlock.text.length > 180, 'fixture must clear the min-length gate to test URL density')
  assert.equal(scoreDescriptionBlock(urlBlock), -Infinity)
})

test('accessibility/legal boilerplate does not outrank a real job-content candidate', () => {
  const accessibilityBlock = {
    tag: 'div',
    inNavLike: false,
    text: [
      'Accessibility Statement',
      'We are committed to digital accessibility for all users.',
      'This site follows WCAG 2.1 guidelines wherever possible.',
      'Privacy Policy and Cookie Policy govern your use of this site.',
      'For accessibility issues, contact us at accessibility@example.com.',
    ].join('\n\n'.repeat(6)),
    linkCount: 3,
    linkTextLength: 30,
    listItemCount: 0,
    paragraphCount: 4,
    headings: ['Accessibility Statement'],
  }
  const selected = selectDescriptionBlock([accessibilityBlock, jobBlock])
  assert.equal(selected.text, jobBlock.text)
})

test('selection returns null when nothing looks like a description', () => {
  assert.equal(selectDescriptionBlock([navBlock]), null)
  assert.equal(selectDescriptionBlock([]), null)
})

// ---------------------------------------------------------------------------
// Header chrome — generic, not tuned to any one site
// ---------------------------------------------------------------------------

test('leading header chrome (back-link, title, colon-location, meta row) is stripped before the real JD', () => {
  const block = {
    tag: 'section',
    inNavLike: false,
    text: [
      'Back to Careers',
      'Senior Data Engineer',
      'United States: New York',
      'Engineering | Full-time | Req #4821',
      '',
      'About the Role',
      'We build the pipelines that power every product team.',
      'Responsibilities',
      '- Design and operate streaming pipelines',
    ].join('\n'),
    linkCount: 1,
    linkTextLength: 6,
    listItemCount: 1,
    paragraphCount: 3,
    headings: ['About the Role', 'Responsibilities'],
  }
  const result = extractJobPosting({
    title: 'Senior Data Engineer',
    url: 'https://careers.example.com/jobs/1',
    jsonLd: [],
    headings: [{ level: 1, text: 'Senior Data Engineer', inNavLike: false }],
    blocks: [block],
  })

  assert.ok(!result.job_description.includes('Back to Careers'))
  assert.ok(!result.job_description.includes('Req #4821'))
  assert.ok(result.job_description.trimStart().startsWith('About the Role'))
  // The header chrome recovered from the content feeds location extraction too.
  assert.equal(result.location, 'United States: New York')
})

test('leadingHeaderChromeLines only looks at the front of the text, never mid-description', () => {
  const text = [
    'Product Manager',
    '',
    'About the Role',
    'You will own the roadmap.',
    'Location: mentioned only in prose here, not a header row',
  ].join('\n')
  const chrome = leadingHeaderChromeLines(text)
  assert.deepEqual(chrome, ['Product Manager'])
})

test('cleanDescription leaves ordinary content alone when there is no header chrome', () => {
  const text = 'About the Role\nWe build things.\nResponsibilities\n- Ship code'
  assert.equal(cleanDescription(text), text)
})

test('no JSON-LD falls back to a good DOM block, never raw body text', () => {
  const result = extractJobPosting({
    title: 'Data Platform Engineer - Acme',
    url: 'https://careers.acme.com/jobs/12',
    jsonLd: [],
    meta: {},
    headings: [{ level: 1, text: 'Data Platform Engineer', inNavLike: false }],
    blocks: [navBlock, jobBlock],
    bodyText: `${navBlock.text}\n${jobBlock.text}`,
  })
  assert.equal(result.sources.job_description, 'dom')
  assert.match(result.job_description, /Key Responsibilities/)
  assert.ok(!result.job_description.includes('Contact Us'))
})

// ---------------------------------------------------------------------------
// Work mode — conservative by design
// ---------------------------------------------------------------------------

test('a bare "remote" in navigation never marks a job Remote', () => {
  const result = extractWorkMode({
    headerLines: ['Cyber Threat Intelligence Analyst', 'Israel: Tel Aviv-Yafo'],
    description: 'We protect customers using Remote Access VPN technology every day.',
  })
  assert.equal(result.value, '')
})

test('a bare "remote" in ordinary prose is not enough', () => {
  const result = extractWorkMode({
    headerLines: [],
    description: 'You will support remote offices and travelling staff.',
  })
  assert.equal(result.value, '')
})

test('an explicit job-level remote phrase does mark it Remote', () => {
  for (const phrase of [
    'This is a fully remote position.',
    'We are hiring for a remote role based anywhere in the EU.',
    'This position is remote and reports to the CTO.',
    'You may work from home full time.',
  ]) {
    assert.equal(extractWorkMode({ headerLines: [], description: phrase }).value, 'Remote', phrase)
  }
})

test('an explicit hybrid phrase is detected', () => {
  const result = extractWorkMode({ headerLines: [], description: 'This is a hybrid role, 3 days in office.' })
  assert.equal(result.value, 'Hybrid')
})

test('an explicit on-site phrase is detected', () => {
  const result = extractWorkMode({ headerLines: [], description: 'This role is fully on-site in Berlin.' })
  assert.equal(result.value, 'On-site')
})

test('a job-header row is trusted for a bare mode word', () => {
  const result = extractWorkMode({ headerLines: ['Tel Aviv, Israel — Remote'], description: '' })
  assert.equal(result.value, 'Remote')
  assert.equal(result.source, 'header')
})

// ---------------------------------------------------------------------------
// Location
// ---------------------------------------------------------------------------

test('looksLikeLocationLine accepts comma and colon forms, rejects prose', () => {
  assert.ok(looksLikeLocationLine('Tel Aviv, Israel'))
  assert.ok(looksLikeLocationLine('Israel: Tel Aviv-Yafo'))
  assert.ok(looksLikeLocationLine('Location: Tel Aviv'))
  assert.ok(!looksLikeLocationLine('We are hiring, and you will love it.'))
  assert.ok(!looksLikeLocationLine('Products: Firewalls'))
  assert.ok(!looksLikeLocationLine('Engineer'))
})

test('a URL is never accepted as a location', () => {
  assert.ok(!looksLikeLocationLine('http://example.com'))
  assert.ok(!looksLikeLocationLine('Careers: www.example.com/jobs'))
  assert.ok(
    !extractLocation({
      headerLines: ['http://example.com', 'Tel Aviv, Israel'],
    }).value.includes('example.com'),
  )
})

test('an email address is never accepted as a location', () => {
  assert.ok(!looksLikeLocationLine('hr@example.com'))
  assert.ok(!looksLikeLocationLine('Contact: careers@example.com'))
})

test('legal/accessibility/contact boilerplate is never accepted as a location', () => {
  assert.ok(!looksLikeLocationLine('Accessibility Statement'))
  assert.ok(!looksLikeLocationLine('Privacy Policy'))
  assert.ok(!looksLikeLocationLine('Contact Us'))
  assert.ok(!looksLikeLocationLine('All Rights Reserved'))
})

test('a URL/email header line does not block a real location line further down', () => {
  const result = extractLocation({
    headerLines: ['http://example.com', 'hr@example.com', 'Tel Aviv, Israel'],
  })
  assert.equal(result.value, 'Tel Aviv, Israel')
})

test('a work-mode marker is stripped out of the location', () => {
  const result = extractLocation({ headerLines: ['Caesarea, Israel (Hybrid)'] })
  assert.equal(result.value, 'Caesarea, Israel')
})

test('a labelled location inside the description is used as a fallback', () => {
  const result = extractLocation({
    headerLines: [],
    descriptionLines: ['About the role', 'Location: Berlin, Germany'],
  })
  assert.equal(result.value, 'Berlin, Germany')
})

test('no location evidence yields blank, not a guess', () => {
  assert.equal(extractLocation({ headerLines: ['Engineer'], descriptionLines: ['We build things.'] }).value, '')
})

// ---------------------------------------------------------------------------
// Company
// ---------------------------------------------------------------------------

test('companyDomainLabel ignores careers/jobs prefixes and the TLD', () => {
  assert.equal(companyDomainLabel('https://careers.acmesecurity.example/x'), 'acmesecurity')
  assert.equal(companyDomainLabel('https://jobs.acme.io/1'), 'acme')
  assert.equal(companyDomainLabel('https://www.bluepeak.com'), 'bluepeak')
  assert.equal(companyDomainLabel('not a url'), '')
})

test('the domain confirms a page spelling rather than inventing one', () => {
  // "acmesecurity" the domain label cannot tell us where the space goes; the
  // page supplies "Acme Security" and the domain only confirms the site owner.
  const result = extractCompany({
    meta: { 'og:site_name': 'Acme Security Software Technologies' },
    url: 'https://careers.acmesecurity.example/index.php?joborderid=1',
    title: 'Analyst (1)',
    headings: [],
  })
  assert.equal(result.value, 'Acme Security Software Technologies')
  assert.equal(result.source, 'domain-confirmed')
})

test('an unrelated site name is not confused with the domain owner', () => {
  const result = extractCompany({
    meta: { 'og:site_name': 'Workday' },
    url: 'https://acme.wd1.myworkdayjobs.com/job/1',
    title: 'Engineer',
    headings: [],
  })
  // Falls through to plain metadata rather than claiming domain confirmation.
  assert.equal(result.source, 'metadata')
})

test('missing company evidence yields blank rather than garbage', () => {
  const result = extractCompany({ meta: {}, url: 'https://example.com/1', title: 'Engineer', headings: [] })
  assert.equal(result.value, '')
  assert.equal(result.source, 'none')
})

// ---------------------------------------------------------------------------
// URL identifiers must never leak into the role
// ---------------------------------------------------------------------------

test('a trailing bracketed id present in the URL is stripped', () => {
  assert.equal(
    stripUrlIdentifier('Threat Intelligence Analyst (9988776)', LEGACY_ATS_URL),
    'Threat Intelligence Analyst',
  )
})

test('a bracketed suffix that is NOT in the URL is preserved', () => {
  assert.equal(
    stripUrlIdentifier('Analyst (Cyber)', 'https://example.com/jobs/55'),
    'Analyst (Cyber)',
  )
  assert.equal(
    stripUrlIdentifier('Engineer (REF123)', 'https://example.com/jobs/55'),
    'Engineer (REF123)',
  )
})

test('stripping never empties the role', () => {
  assert.equal(stripUrlIdentifier('(9988776)', LEGACY_ATS_URL), '(9988776)')
})

// ---------------------------------------------------------------------------
// Safety: a hostile / empty page must not produce nonsense
// ---------------------------------------------------------------------------

test('an empty snapshot produces blanks, not errors', () => {
  const result = extractJobPosting({})
  assert.deepEqual(
    { company: result.company, role: result.role, location: result.location, mode: result.work_mode },
    { company: '', role: '', location: '', mode: '' },
  )
})

test('a page with only navigation produces no description from a real candidate', () => {
  const result = extractJobPosting({
    title: 'Acme',
    url: 'https://acme.com',
    jsonLd: [],
    blocks: [navBlock],
    headings: [{ level: 2, text: 'Products', inNavLike: true }],
  })
  assert.notEqual(result.sources.job_description, 'dom')
})
