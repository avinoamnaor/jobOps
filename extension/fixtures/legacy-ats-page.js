// Regression fixture: a legacy careers-page ATS layout that JobOps Capture got
// wrong (title carrying an internal id, colon-form location, header content
// mixed into the job block, nav copy that happens to include the word
// "remote"). The identifiers below are fictional — this models the *shape* of
// a real page found during manual testing, not its actual content.
//
// This is a *page snapshot* (the structure collect.js produces in the browser),
// hand-built to model that page's shape. It is representative, not scraped:
// the exact wording of the live page's metadata was not copied, so treat the
// values here as "a page of this shape", which is what the extraction logic
// actually has to cope with.
//
// It reproduces every symptom of the original bug report, including a second
// discrepancy found only by testing against the LIVE page after the first fix:
//   * document.title carries the URL's joborderid -> role became
//     "Threat Intelligence Analyst (9988776)"
//   * no JSON-LD at all (legacy PHP careers app) -> nothing structured to lean on
//   * the location row is colon-separated ("Israel: Tel Aviv-Yafo"), which the
//     old comma-only location check rejected
//   * the site navigation advertises a "Remote Access VPN" product, which made a
//     bare-keyword work-mode check answer "Remote" for an office job
//   * there is no <main>, so the old `main || article || body` fallback grabbed
//     document.body and the description started with the Products menu
//   * on the LIVE page, the <h1>'s own parent container wraps only the title —
//     unlike the first version of this fixture, which (incorrectly) modelled
//     location/job-id as reachable through that same narrow container. The real
//     page prints "Back to Job Openings", the title, the location, and a
//     "R&D | Full-time | Job ID: REF123" row as sibling content that lands
//     inside the job-content block itself, not in `collect.js`'s `headerLines`.
//     `headerLines` below is deliberately narrowed to just the title to match.

export const LEGACY_ATS_URL =
  'https://careers.acmesecurity.example/index.php?m=careers&a=show&joborderid=9988776&source=104&mode=clear'

const NAV_TEXT = [
  'Products',
  'NETWORK & SASE',
  'NETWORK',
  'Next-generation Firewalls',
  'Firewall Cluster',
  'SMB Firewalls',
  'DDoS Protection',
  'Remote Access VPN',
  'SASE',
  'Secure Web Gateway',
  'Zero Trust Network Access',
  'CLOUD',
  'Cloud Network Security',
  'Solutions',
  'Support',
  'Partners',
  'Resources',
  'Contact Us',
  'Log In',
].join('\n')

const FOOTER_TEXT = [
  'Products',
  'Quantum Network Security',
  'Remote Access VPN',
  'Support',
  'Contact Us',
  'Privacy Policy',
  'Terms of Use',
  '© 1994-2025 Acme Security Ltd. All rights reserved.',
].join('\n')

const JOB_TEXT = [
  'Back to Job Openings',
  'Threat Intelligence Analyst',
  'Israel: Tel Aviv-Yafo',
  'R&D | Full-time | Job ID: REF123',
  '',
  'Why Join Us?',
  'Join a team that sits at the front line of global cyber defence. You will work',
  'alongside researchers who track advanced persistent threats and turn raw',
  'telemetry into intelligence that protects thousands of organisations.',
  '',
  'Key Responsibilities',
  '- Track and profile threat actors, campaigns and malware families.',
  '- Produce written intelligence assessments for technical and executive readers.',
  '- Correlate telemetry across network, endpoint and cloud sources.',
  '- Support incident response teams with timely, actionable context.',
  '- Build and maintain tooling that automates collection and enrichment.',
  '',
  'Qualifications',
  '- 3+ years in threat intelligence, incident response or security research.',
  '- Strong understanding of the modern attacker lifecycle and MITRE ATT&CK.',
  '- Experience analysing malware behaviour and command-and-control traffic.',
  '- Excellent written English and the ability to brief non-technical audiences.',
  '- Scripting experience (Python preferred) for data enrichment.',
  '',
  'Apply Now',
].join('\n')

/** The snapshot collect.js would produce for this page. */
export const legacyAtsSnapshot = {
  // The title really does carry the internal joborderid — this is the source of
  // the "(9988776)" that leaked into the role.
  title: 'Threat Intelligence Analyst (9988776)',
  url: LEGACY_ATS_URL,

  // Legacy ATS page: no structured JobPosting data anywhere.
  jsonLd: [],

  meta: {
    'og:site_name': 'Acme Security',
    'og:title': 'Threat Intelligence Analyst',
  },

  headings: [
    { level: 2, text: 'Products', inNavLike: true },
    { level: 2, text: 'Solutions', inNavLike: true },
    { level: 1, text: 'Threat Intelligence Analyst', inNavLike: false },
    { level: 2, text: 'Why Join Us?', inNavLike: false },
    { level: 2, text: 'Key Responsibilities', inNavLike: false },
    { level: 2, text: 'Qualifications', inNavLike: false },
    { level: 2, text: 'Support', inNavLike: true },
  ],

  // On the live page the H1's own parent wraps only the title itself — location
  // and job id are sibling content that ends up inside the job block instead
  // (see JOB_TEXT). `extractJobPosting` recovers them from there generically.
  headerLines: ['Threat Intelligence Analyst'],

  blocks: [
    {
      // The mega-menu. Mostly links, and it is what document.body.innerText
      // starts with — the old code shipped this as the job description.
      tag: 'div',
      role: null,
      inNavLike: true,
      text: NAV_TEXT,
      linkCount: 48,
      linkTextLength: NAV_TEXT.length - 20,
      listItemCount: 40,
      paragraphCount: 0,
      headings: ['Products', 'Solutions', 'Support'],
    },
    {
      // A page wrapper that contains everything: nav + job + footer. Not itself
      // inside <nav>, so it is a legitimate candidate — the scorer has to prefer
      // the tighter job block over it.
      tag: 'div',
      role: null,
      inNavLike: false,
      text: [NAV_TEXT, JOB_TEXT, FOOTER_TEXT].join('\n'),
      linkCount: 62,
      linkTextLength: NAV_TEXT.length + FOOTER_TEXT.length - 40,
      listItemCount: 50,
      paragraphCount: 8,
      headings: [
        'Products',
        'Solutions',
        'Threat Intelligence Analyst',
        'Why Join Us?',
        'Key Responsibilities',
        'Qualifications',
        'Support',
      ],
    },
    {
      // The actual job posting.
      tag: 'section',
      role: null,
      inNavLike: false,
      text: JOB_TEXT,
      linkCount: 1,
      linkTextLength: 9,
      listItemCount: 10,
      paragraphCount: 6,
      headings: [
        'Threat Intelligence Analyst',
        'Why Join Us?',
        'Key Responsibilities',
        'Qualifications',
      ],
    },
    {
      tag: 'div',
      role: null,
      inNavLike: true,
      text: FOOTER_TEXT,
      linkCount: 24,
      linkTextLength: FOOTER_TEXT.length - 30,
      listItemCount: 20,
      paragraphCount: 1,
      headings: ['Products', 'Support'],
    },
  ],

  selectionText: '',
  // Exactly the symptom from the report: the page's raw text opens with the menu.
  bodyText: [NAV_TEXT, JOB_TEXT, FOOTER_TEXT].join('\n'),
  usedSelection: false,
}
