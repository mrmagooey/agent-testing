import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'

test.beforeEach(async ({ page }) => {
  await mockApi(page)
})

test('navigating to an unknown route shows the 404 heading', async ({ page }) => {
  await page.goto('/zzz-no-such-route')
  await expect(page.getByRole('heading', { name: 'Page not found' })).toBeVisible()
})

test('the current URL path is rendered inside the 404 message', async ({ page }) => {
  await page.goto('/zzz-no-such-route')
  await expect(page.getByText('/zzz-no-such-route')).toBeVisible()
})

test('clicking Back to dashboard navigates to / and shows the Dashboard heading', async ({ page }) => {
  await page.goto('/zzz-no-such-route')
  await page.getByRole('link', { name: 'Back to dashboard' }).click()
  await expect(page).toHaveURL('/')
  await expect(page.getByRole('heading', { name: 'Dashboard' })).toBeVisible()
})

test('the NavBar is still visible on the 404 page', async ({ page }) => {
  await page.goto('/zzz-no-such-route')
  await expect(page.getByRole('navigation')).toBeVisible()
  await expect(page.getByText('SEC·REVIEW')).toBeVisible()
})

test('a deeper unknown path also renders the 404 page', async ({ page }) => {
  await page.goto('/experiments/abc/runs/xyz/zzz-not-real')
  await expect(page.getByRole('heading', { name: 'Page not found' })).toBeVisible()
  await expect(page.getByText('/experiments/abc/runs/xyz/zzz-not-real')).toBeVisible()
})
