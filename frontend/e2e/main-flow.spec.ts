import { expect, test } from '@playwright/test'
import {
  collectConsoleProblems,
  fakePdf,
  ignorableConsoleNoise,
  resetDatabase,
} from './helpers'

/**
 * The whole journey, in one test, in the order a real user does it.
 *
 * Kept as a single test on purpose: each step depends on the previous one, and
 * splitting it would either need shared mutable state between tests or a lot of
 * repeated setup.
 */
test.describe('main user flow', () => {
  test.beforeEach(() => resetDatabase())

  test('create, attach CV, change status, edit, download, and see it in the list', async ({
    page,
  }) => {
    const consoleProblems = collectConsoleProblems(page)

    // --- 1. Empty applications list ------------------------------------
    await page.goto('/')
    await expect(page.getByRole('heading', { name: 'Applications' })).toBeVisible()
    await expect(page.getByText('No applications yet.')).toBeVisible()

    // --- 2. Create an application --------------------------------------
    await page.getByRole('link', { name: 'New application' }).click()
    await expect(page).toHaveURL(/\/applications\/new$/)

    // Target the input by role: once the Channel <select> loads its options,
    // its accessible name includes the text "Company Site", so a bare
    // getByLabel('Company') would match two controls.
    await page.getByRole('textbox', { name: 'Company' }).fill('ProgrammaticX Ltd.')
    await page.getByLabel('Role').fill('Fullstack Developer (m/f/d)')
    await page.getByLabel('Channel').selectOption('linkedin')
    await page.getByLabel('Location').fill('Tel Aviv')
    await page.getByLabel('Work mode').fill('hybrid')
    await page
      .getByLabel('Job URL')
      .fill('https://www.example.com/jobs/4821/?utm_source=linkedin&refId=abc')
    await page.getByLabel('Job description').fill('Own features end to end across the stack.')
    await page.getByLabel('Notes').fill('Recruiter: Dana Levi')

    await page.getByRole('button', { name: 'Create application' }).click()

    // --- 3. Detail page --------------------------------------------------
    await expect(page).toHaveURL(/\/applications\/\d+$/)
    await expect(page.getByRole('heading', { name: 'ProgrammaticX Ltd.' })).toBeVisible()
    await expect(page.getByText('Fullstack Developer (m/f/d)')).toBeVisible()
    // The timeline opens with the `created` event.
    await expect(page.getByText('Application created with status')).toBeVisible()
    await expect(
      page.getByText('No CV recorded as submitted for this application yet.'),
    ).toBeVisible()

    // --- 4 & 5. Upload a CV and attach it in one action -------------------
    await page
      .getByLabel('Or upload a new CV')
      .setInputFiles({
        name: 'Demo_CV_v3.pdf',
        mimeType: 'application/pdf',
        buffer: fakePdf('CV v3 node heavy'),
      })

    const cvPanel = page.locator('section').filter({ hasText: 'SUBMITTED CV' }).first()
    await expect(cvPanel.getByText('Demo_CV_v3.pdf').first()).toBeVisible()
    // The attachment is recorded on the timeline.
    await expect(page.getByText('Submitted CV attached: Demo_CV_v3.pdf')).toBeVisible()

    // --- 6. Change status -------------------------------------------------
    await page.getByLabel('Move to status').selectOption('applied')
    await page.getByLabel('Note (optional)').fill('Submitted through the careers site.')
    await page.getByRole('button', { name: 'Record status change' }).click()

    // --- 7. Timeline reflects the transition ------------------------------
    const timeline = page.locator('.timeline')
    await expect(timeline.getByText('Status Changed').first()).toBeVisible()
    await expect(timeline.getByText('Submitted through the careers site.')).toBeVisible()
    // Rendered as Saved → Applied, from the event's own previous/new columns.
    const firstTransition = page.locator('.timeline-item').first()
    await expect(firstTransition.getByText('Saved')).toBeVisible()
    await expect(firstTransition.getByText('Applied')).toBeVisible()

    // The header badge and the applied date both update.
    await expect(page.locator('.head-meta .badge')).toHaveText('Applied')
    await expect(page.getByText('—', { exact: true })).toHaveCount(0)

    // A second transition, to confirm history accumulates.
    await page.getByLabel('Move to status').selectOption('hr_interview')
    await page.getByRole('button', { name: 'Record status change' }).click()
    await expect(page.locator('.head-meta .badge')).toHaveText('HR Interview')
    await expect(page.locator('.timeline-item')).toHaveCount(4)

    // --- 8. Edit application details --------------------------------------
    await page.getByRole('button', { name: 'Edit' }).click()
    await page.getByLabel('Notes').fill('Recruiter: Dana Levi. Screening call Tuesday 14:00.')
    await page.getByRole('button', { name: 'Save changes' }).click()

    await expect(page.getByText('Screening call Tuesday 14:00.')).toBeVisible()
    // Editing must not invent a timeline event.
    await expect(page.locator('.timeline-item')).toHaveCount(4)
    // …and must not disturb the status.
    await expect(page.locator('.head-meta .badge')).toHaveText('HR Interview')

    // --- 9. Download the CV and verify the bytes --------------------------
    const downloadPromise = page.waitForEvent('download')
    await cvPanel.getByRole('link', { name: 'Download' }).click()
    const download = await downloadPromise

    expect(download.suggestedFilename()).toBe('Demo_CV_v3.pdf')
    const downloadPath = await download.path()
    const { readFileSync } = await import('node:fs')
    expect(readFileSync(downloadPath!)).toEqual(fakePdf('CV v3 node heavy'))

    // --- 10. Back to the list ---------------------------------------------
    await page.getByRole('link', { name: '← Applications' }).click()
    await expect(page).toHaveURL(/\/$/)

    const row = page.locator('tbody tr').first()
    await expect(row.getByText('ProgrammaticX Ltd.')).toBeVisible()
    await expect(row.getByText('Fullstack Developer (m/f/d)')).toBeVisible()
    await expect(row.getByText('HR Interview')).toBeVisible()
    await expect(row.getByText('LinkedIn')).toBeVisible()
    await expect(row.getByText('Attached')).toBeVisible()

    // --- Console must be clean throughout ---------------------------------
    const realProblems = consoleProblems.filter((entry) => !ignorableConsoleNoise(entry))
    expect(realProblems, `console problems:\n${realProblems.join('\n')}`).toEqual([])
  })
})
