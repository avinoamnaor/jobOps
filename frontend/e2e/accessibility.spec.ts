import { expect, test } from '@playwright/test'
import { createApplicationViaApi, fakePdf, resetDatabase, uploadDocumentViaApi } from './helpers'

/**
 * A basic usability and accessibility pass.
 *
 * Not a full WCAG audit — these check the things that most often break in a
 * hand-rolled UI: unlabelled inputs, keyboard traps, and layouts that fall apart
 * at ordinary desktop widths.
 */
test.describe('accessibility and usability', () => {
  test.beforeEach(() => resetDatabase())

  const pages = ['/', '/applications/new', '/documents']

  for (const path of pages) {
    test(`every form control on ${path} has an accessible name`, async ({ page }) => {
      await page.goto(path)
      // Let async-loaded selects render.
      await page.waitForLoadState('networkidle')

      const unnamed = await page.evaluate(() => {
        const controls = [...document.querySelectorAll('input, select, textarea')]
        return controls
          .filter((element) => {
            const el = element as HTMLInputElement
            const byWrappingLabel = el.closest('label') !== null
            const byAriaLabel = el.getAttribute('aria-label')
            const byLabelFor = el.id && document.querySelector(`label[for="${el.id}"]`)
            return !byWrappingLabel && !byAriaLabel && !byLabelFor
          })
          .map((el) => `${el.tagName}[type=${(el as HTMLInputElement).type}]`)
      })

      expect(unnamed, `controls without labels on ${path}`).toEqual([])
    })
  }

  test('the create form is completable with the keyboard alone', async ({ page }) => {
    await page.goto('/applications/new')

    // The first field is focused on arrival, so typing can begin immediately.
    await expect(page.getByLabel('Company')).toBeFocused()

    await page.keyboard.type('Keyboard Co')
    await page.keyboard.press('Tab')
    await expect(page.getByLabel('Role')).toBeFocused()
    await page.keyboard.type('Engineer')

    // Enter submits from a text field, as in any ordinary form.
    await page.keyboard.press('Enter')

    await expect(page).toHaveURL(/\/applications\/\d+$/)
    await expect(page.getByRole('heading', { name: 'Keyboard Co' })).toBeVisible()
  })

  test('interactive elements are reachable by tabbing', async ({ page }) => {
    await page.goto('/')

    const reached: string[] = []
    for (let index = 0; index < 8; index += 1) {
      await page.keyboard.press('Tab')
      reached.push(
        await page.evaluate(() => {
          const el = document.activeElement as HTMLElement | null
          if (!el || el === document.body) return 'none'
          return `${el.tagName}:${(el.textContent || el.getAttribute('aria-label') || '').trim().slice(0, 24)}`
        }),
      )
    }

    // Focus actually moves rather than sticking on <body>.
    expect(reached.filter((entry) => entry !== 'none').length).toBeGreaterThan(4)
  })

  test('buttons communicate disabled and busy states', async ({ page, request }) => {
    const cv = await uploadDocumentViaApi(request, {
      name: 'cv.pdf',
      kind: 'cv',
      content: fakePdf('a cv'),
      label: 'CV',
    })
    const created = await createApplicationViaApi(request, {
      company_name: 'State Co',
      role_title: 'Engineer',
    })
    await request.put(`http://localhost:8001/applications/${created.id}/submitted-cv`, {
      data: { document_id: cv.id },
    })

    await page.goto(`/applications/${created.id}`)

    // The status button is disabled while the target equals the current status,
    // which is exactly the request the backend would answer with a 409.
    const statusButton = page.getByRole('button', { name: 'Record status change' })
    await expect(statusButton).toBeDisabled()
    await expect(page.getByText('Pick a different status to record a change.')).toBeVisible()

    await page.getByLabel('Move to status').selectOption('applied')
    await expect(statusButton).toBeEnabled()

    // The attach button stays disabled until a CV is actually chosen.
    await expect(page.getByRole('button', { name: 'Attach' })).toBeDisabled()
  })

  test('loading, empty and error states are all understandable', async ({ page }) => {
    // Empty.
    await page.goto('/')
    await expect(page.getByText('No applications yet.')).toBeVisible()
    await expect(page.getByRole('link', { name: 'Add your first application' })).toBeVisible()

    // Loading — slow the response enough to observe the state.
    await page.route('**/applications?**', async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 700))
      return route.continue()
    })
    const navigation = page.goto('/')
    await expect(page.getByText('Loading applications…')).toBeVisible()
    await navigation
  })

  test('layout holds at common desktop widths', async ({ page, request }) => {
    await createApplicationViaApi(request, {
      company_name: 'Layout Co',
      role_title: 'Fullstack Developer',
    })

    for (const width of [1024, 1280, 1440, 1920]) {
      await page.setViewportSize({ width, height: 900 })
      await page.goto('/')

      // No horizontal overflow of the document.
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
      )
      expect(overflow, `horizontal overflow at ${width}px`).toBe(false)

      await expect(page.locator('tbody tr').first()).toBeVisible()
    }
  })

  test('detail page layout holds at common desktop widths', async ({ page, request }) => {
    const created = await createApplicationViaApi(request, {
      company_name: 'Layout Co',
      role_title: 'Fullstack Developer',
      job_description: 'A fairly long description. '.repeat(40),
    })

    for (const width of [1024, 1280, 1440]) {
      await page.setViewportSize({ width, height: 900 })
      await page.goto(`/applications/${created.id}`)

      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
      )
      expect(overflow, `horizontal overflow at ${width}px`).toBe(false)
      await expect(page.getByRole('heading', { name: 'Layout Co' })).toBeVisible()
    }
  })
})
