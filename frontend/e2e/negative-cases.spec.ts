import { expect, test } from '@playwright/test'
import { API_BASE, fakePdf, resetDatabase, uploadDocumentViaApi } from './helpers'

test.describe('validation and error handling', () => {
  test.beforeEach(() => resetDatabase())

  test('required fields block submission in the browser', async ({ page }) => {
    await page.goto('/applications/new')

    await page.getByRole('button', { name: 'Create application' }).click()

    // Native `required` validation keeps us on the page and focuses the field.
    await expect(page).toHaveURL(/\/applications\/new$/)
    const company = page.getByRole('textbox', { name: 'Company' })
    expect(await company.evaluate((el: HTMLInputElement) => el.validity.valueMissing)).toBe(true)
  })

  test('a backend 422 is shown as a readable per-field message', async ({ page }) => {
    await page.goto('/applications/new')

    // Fill the browser-required fields, then make the backend reject it. A role
    // longer than 200 characters passes HTML validation and fails Pydantic's.
    await page.getByRole('textbox', { name: 'Company' }).fill('Acme')
    await page.getByLabel('Role').fill('R'.repeat(250))
    await page.getByRole('button', { name: 'Create application' }).click()

    const banner = page.getByRole('alert')
    await expect(banner).toBeVisible()
    await expect(banner).toContainText('Please check the form')
    await expect(banner).toContainText('role_title')
    // The raw exception/JSON must never reach the user.
    await expect(banner).not.toContainText('Traceback')
    await expect(banner).not.toContainText('{"detail"')
  })

  test('application not found shows a clear message and a way back', async ({ page }) => {
    await page.goto('/applications/999999')

    const banner = page.getByRole('alert')
    await expect(banner).toBeVisible()
    await expect(banner).toContainText('Not found')
    await expect(page.getByRole('link', { name: 'Back to applications' })).toBeVisible()
  })

  test('an unknown route shows a not-found page', async ({ page }) => {
    await page.goto('/no/such/page')
    await expect(page.getByText('That page does not exist.')).toBeVisible()
  })

  test('backend unavailable is explained, not left as a blank screen', async ({ page }) => {
    // Simulate the API being down without stopping the real server.
    await page.route(`${API_BASE}/applications**`, (route) => route.abort('connectionrefused'))

    await page.goto('/')

    const banner = page.getByRole('alert')
    await expect(banner).toBeVisible()
    await expect(banner).toContainText('Cannot reach the server')
    await expect(banner).toContainText('Is the backend running?')
    await expect(page.getByRole('button', { name: 'Try again' })).toBeVisible()
  })

  test('retry recovers once the backend is reachable again', async ({ page }) => {
    // Fail every /applications request until we explicitly allow them through.
    // A one-shot toggle is unreliable: React StrictMode double-invokes effects
    // in development, so the initial load fires the request more than once and a
    // "fail only the first" flag would let the second succeed.
    let shouldFail = true
    await page.route(`${API_BASE}/applications**`, (route) =>
      shouldFail ? route.abort('connectionrefused') : route.continue(),
    )

    await page.goto('/')
    await expect(page.getByRole('alert')).toBeVisible()

    shouldFail = false
    await page.getByRole('button', { name: 'Try again' }).click()

    await expect(page.getByRole('alert')).toHaveCount(0)
    await expect(page.getByText('No applications yet.')).toBeVisible()
  })
})

test.describe('document edge cases', () => {
  test.beforeEach(() => resetDatabase())

  test('empty document library explains what to do', async ({ page }) => {
    await page.goto('/documents')
    await expect(page.getByText('No documents yet. Upload a CV above to get started.')).toBeVisible()
  })

  test('uploading identical bytes reuses the existing document', async ({ page }) => {
    await page.goto('/documents')

    const content = fakePdf('identical bytes')

    await page
      .getByLabel('File')
      .setInputFiles({ name: 'cv.pdf', mimeType: 'application/pdf', buffer: content })
    await page.getByLabel('Label').fill('First upload')
    await page.getByRole('button', { name: 'Upload document' }).click()
    await expect(page.getByRole('status')).toContainText('Uploaded')
    await expect(page.locator('tbody tr')).toHaveCount(1)

    // Same bytes, different filename and label.
    await page
      .getByLabel('File')
      .setInputFiles({ name: 'a_copy.pdf', mimeType: 'application/pdf', buffer: content })
    await page.getByLabel('Label').fill('Second upload')
    await page.getByRole('button', { name: 'Upload document' }).click()

    await expect(page.getByRole('status')).toContainText('Identical file already in the library')
    // Crucially: still one row, not two.
    await expect(page.locator('tbody tr')).toHaveCount(1)
  })

  test('same filename with different contents creates two documents', async ({ page }) => {
    await page.goto('/documents')

    await page
      .getByLabel('File')
      .setInputFiles({ name: 'cv.pdf', mimeType: 'application/pdf', buffer: fakePdf('version 3') })
    await page.getByRole('button', { name: 'Upload document' }).click()
    await expect(page.locator('tbody tr')).toHaveCount(1)

    await page
      .getByLabel('File')
      .setInputFiles({ name: 'cv.pdf', mimeType: 'application/pdf', buffer: fakePdf('version 4') })
    await page.getByRole('button', { name: 'Upload document' }).click()

    await expect(page.getByRole('status')).toContainText('Uploaded')
    await expect(page.locator('tbody tr')).toHaveCount(2)
  })

  test('a non-CV document cannot be attached as the submitted CV', async ({ page, request }) => {
    // The dropdown only offers CVs, so reach past the UI to prove the rule holds
    // where it actually matters — the API — and that the UI reports it usefully.
    const letter = await uploadDocumentViaApi(request, {
      name: 'cover.pdf',
      kind: 'cover_letter',
      content: fakePdf('a cover letter'),
      label: 'Cover letter',
    })
    const application = await request.post(`${API_BASE}/applications`, {
      data: { company_name: 'Acme', role_title: 'Backend Engineer' },
    })
    const { id } = await application.json()

    const rejected = await request.put(`${API_BASE}/applications/${id}/submitted-cv`, {
      data: { document_id: letter.id },
    })
    expect(rejected.status()).toBe(422)
    expect((await rejected.json()).detail).toContain('cover_letter')

    // And the UI never offered it in the first place.
    await page.goto(`/applications/${id}`)
    const options = await page.getByLabel('Attach a CV').locator('option').allTextContents()
    expect(options.join(' ')).not.toContain('Cover letter')
    await expect(
      page.getByText('No CV recorded as submitted for this application yet.'),
    ).toBeVisible()
  })

  test('attaching the CV that is already attached is refused with a clear message', async ({
    page,
    request,
  }) => {
    const cv = await uploadDocumentViaApi(request, {
      name: 'cv.pdf',
      kind: 'cv',
      content: fakePdf('the only cv'),
      label: 'Only CV',
    })
    const created = await request.post(`${API_BASE}/applications`, {
      data: { company_name: 'Acme', role_title: 'Backend Engineer' },
    })
    const { id } = await created.json()

    const first = await request.put(`${API_BASE}/applications/${id}/submitted-cv`, {
      data: { document_id: cv.id },
    })
    expect(first.status()).toBe(200)

    const second = await request.put(`${API_BASE}/applications/${id}/submitted-cv`, {
      data: { document_id: cv.id },
    })
    expect(second.status()).toBe(409)

    // The UI avoids the situation: the attached CV is not offered again.
    await page.goto(`/applications/${id}`)
    const options = await page
      .getByLabel('Change to another CV')
      .locator('option')
      .allTextContents()
    expect(options.join(' ')).not.toContain('Only CV')
  })
})
