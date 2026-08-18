import { expect, test } from '@playwright/test'
import { createApplicationViaApi, resetDatabase } from './helpers'

test.describe('list, search, filters and pagination', () => {
  test.beforeEach(() => resetDatabase())

  test('search and filters combine, and clear resets them', async ({ page, request }) => {
    await createApplicationViaApi(request, {
      company_name: 'ProgrammaticX',
      role_title: 'Fullstack Developer',
      status: 'applied',
      application_channel: 'linkedin',
    })
    await createApplicationViaApi(request, {
      company_name: 'Acme Analytics',
      role_title: 'Data Engineer',
      status: 'rejected',
      application_channel: 'referral',
    })
    await createApplicationViaApi(request, {
      company_name: 'ProgrammaticX',
      role_title: 'Backend Engineer',
      status: 'saved',
      application_channel: 'linkedin',
    })

    await page.goto('/')
    await expect(page.locator('tbody tr')).toHaveCount(3)

    // Search alone.
    await page.getByLabel('Search company or role').fill('programmatic')
    await expect(page.locator('tbody tr')).toHaveCount(2)

    // Search + status together.
    await page.getByLabel('Filter by status').selectOption('applied')
    await expect(page.locator('tbody tr')).toHaveCount(1)
    await expect(page.locator('tbody tr').first()).toContainText('Fullstack Developer')

    // A combination that matches nothing gets the filtered empty state.
    await page.getByLabel('Filter by status').selectOption('offer')
    await expect(page.getByText('No applications match those filters.')).toBeVisible()

    // Clear brings everything back.
    await page.getByRole('button', { name: 'Clear' }).click()
    await expect(page.locator('tbody tr')).toHaveCount(3)
    await expect(page.getByLabel('Search company or role')).toHaveValue('')
  })

  test('search matches the role as well as the company', async ({ page, request }) => {
    await createApplicationViaApi(request, {
      company_name: 'Acme Analytics',
      role_title: 'Data Engineer',
    })
    await createApplicationViaApi(request, {
      company_name: 'ProgrammaticX',
      role_title: 'Fullstack Developer',
    })

    await page.goto('/')
    await page.getByLabel('Search company or role').fill('data')

    await expect(page.locator('tbody tr')).toHaveCount(1)
    await expect(page.locator('tbody tr').first()).toContainText('Acme Analytics')
  })

  test('channel filter narrows the list', async ({ page, request }) => {
    await createApplicationViaApi(request, {
      company_name: 'Via LinkedIn',
      role_title: 'Engineer',
      application_channel: 'linkedin',
    })
    await createApplicationViaApi(request, {
      company_name: 'Via Referral',
      role_title: 'Engineer',
      application_channel: 'referral',
    })

    await page.goto('/')
    await page.getByLabel('Filter by channel').selectOption('referral')

    await expect(page.locator('tbody tr')).toHaveCount(1)
    await expect(page.locator('tbody tr').first()).toContainText('Via Referral')
  })

  test('pagination appears past one page and navigates correctly', async ({ page, request }) => {
    // 30 records against a page size of 25 gives exactly two pages.
    for (let index = 0; index < 30; index += 1) {
      await createApplicationViaApi(request, {
        company_name: `Company ${String(index).padStart(2, '0')}`,
        role_title: 'Engineer',
      })
    }

    await page.goto('/')
    await expect(page.getByText('30 total')).toBeVisible()
    await expect(page.locator('tbody tr')).toHaveCount(25)
    await expect(page.getByText('Page 1 of 2')).toBeVisible()
    await expect(page.getByRole('button', { name: 'Previous' })).toBeDisabled()

    await page.getByRole('button', { name: 'Next' }).click()

    await expect(page.getByText('Page 2 of 2')).toBeVisible()
    await expect(page.locator('tbody tr')).toHaveCount(5)
    await expect(page.getByRole('button', { name: 'Next' })).toBeDisabled()
    await expect(page.getByRole('button', { name: 'Previous' })).toBeEnabled()

    await page.getByRole('button', { name: 'Previous' }).click()
    await expect(page.getByText('Page 1 of 2')).toBeVisible()
  })

  test('filtering while on page 2 returns to page 1', async ({ page, request }) => {
    for (let index = 0; index < 30; index += 1) {
      await createApplicationViaApi(request, {
        company_name: `Company ${String(index).padStart(2, '0')}`,
        role_title: 'Engineer',
        status: index === 0 ? 'offer' : 'saved',
      })
    }

    await page.goto('/')
    await page.getByRole('button', { name: 'Next' }).click()
    await expect(page.getByText('Page 2 of 2')).toBeVisible()

    // Without the page reset this would ask for page 2 of a 1-page result and
    // show an empty table.
    await page.getByLabel('Filter by status').selectOption('offer')

    await expect(page.locator('tbody tr')).toHaveCount(1)
    await expect(page.getByText('Page 1 of')).toHaveCount(0)
  })

  test('a single page shows no pagination controls', async ({ page, request }) => {
    await createApplicationViaApi(request, { company_name: 'Only One', role_title: 'Engineer' })

    await page.goto('/')

    await expect(page.getByRole('button', { name: 'Next' })).toHaveCount(0)
  })
})
