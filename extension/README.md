# JobOps Capture — Chrome Extension (Part B)

Capture a job page, review/edit **Company**, **Role**, **URL** and **Job
description**, then click **Open in JobOps** to open the New Application form with
those fields pre-filled. The extension never creates an application — JobOps stays
the confirmation point where you choose status/CV and click Create Application.

## How it works

Manifest V3 with `activeTab` + `scripting` (generic capture, no site-specific
selectors) plus a narrow `host_permissions` for `http://localhost:5173/*` (used
only for a "is JobOps running?" pre-flight check).

Extraction is split in two, which is what makes it testable:

1. **Collect** (`collect.js`) — injected into the page. Walks the DOM and reports
   observable facts: JSON-LD blocks, metadata, headings, and candidate content
   blocks with their link/list/paragraph density. It makes no judgements, and it
   must stay self-contained (Chrome serialises the function, so it cannot use
   imports).
2. **Extract** (`extract.js`) — pure and DOM-free. Takes that snapshot and
   decides what everything means. All logic lives here, so it is unit-tested
   without a browser.

Precedence, highest confidence first:

| Field | Order |
|---|---|
| Role | JSON-LD `title` → job-content heading → page title → blank |
| Company | JSON-LD `hiringOrganization` → page name confirmed by the site domain → `og:site_name` → title guess → blank |
| Location | JSON-LD `jobLocation` → job-header row → labelled "Location:" line → blank |
| Work mode | JSON-LD `TELECOMMUTE` → bare word on a job-header row → explicit phrase in the JD → blank |
| Description | JSON-LD `description` → best-scoring DOM block → your selection → page text |

Two rules do most of the work:

- **A confident blank beats a confident wrong answer.** Every field is editable,
  so an empty field costs one correction while a wrong one can be saved unnoticed.
- **Work mode needs job-level evidence.** A bare "remote" in site navigation (a
  security vendor selling "Remote Access VPN", say) or in prose is not evidence
  about the job, so it yields blank rather than "Remote".

Other behaviour:

- **Your selection always wins.** Highlight the description before clicking and
  that is what gets captured.
- **Open in JobOps** hands the values over in the opened tab's URL **hash**
  (`#jobops=…`) — not a query string, so nothing is sent to a server — and the
  New Application page strips it from history the moment it reads it.
- If JobOps isn't running, the popup says so instead of failing silently.

## Tests

```bash
cd extension && node --test
```

No dependencies and no network: the tests run against page *snapshots*
(`fixtures/`), which is the same structure `collect.js` produces in a browser.

## Load it in Chrome (unpacked)

1. Open `chrome://extensions`.
2. Turn on **Developer mode** (top-right).
3. Click **Load unpacked** and select this `extension/` folder.
4. After changing any file here, click the **reload** ↻ icon on the extension card.

## Files

- `manifest.json` — MV3 config and permissions.
- `popup.html` / `popup.js` — the popup UI and the Open-in-JobOps handoff.
- `collect.js` — the in-page DOM walk (injected; must stay self-contained).
- `extract.js` — pure extraction logic; everything interesting lives here.
- `guess.js` — older title/heading heuristics, still used as a fallback.
- `fixtures/` — page snapshots used by the tests.
