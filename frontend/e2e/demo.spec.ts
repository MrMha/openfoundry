import { test, expect } from '@playwright/test';

async function authenticate(page: import('@playwright/test').Page) {
  await page.goto('/');
  await page.getByRole('button', { name: 'Connect backend' }).click();
  await page.getByLabel('Operator token').fill('e2e-operator');
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  await expect(page.getByText('Backend connected', { exact: true })).toBeVisible();
}

test('real self-healing mission renders all live panels, approves, repairs and inspects artifacts', async ({
  page,
}, info) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await authenticate(page);
  await page.getByRole('button', { name: '⚡ Run Self-Healing Demo (No API Key)' }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog).toBeVisible({ timeout: 20000 });
  await expect(dialog.locator('.approval-spec')).toContainText('Calculator');
  // Native modal focus remains inside the review, including Shift-Tab navigation.
  await page.keyboard.press('Shift+Tab');
  expect(await dialog.evaluate((node) => node.contains(document.activeElement))).toBe(true);
  await dialog.getByRole('button', { name: 'Approve & Continue', exact: true }).click();
  await expect(page.getByText('5 of 5 tasks complete', { exact: false })).toBeVisible({
    timeout: 60000,
  });
  await expect(page.getByText('Verification complete', { exact: true })).toBeVisible();
  await expect(
    page.getByRole('region', { name: 'Registered departments' }).locator('article'),
  ).toHaveCount(5);
  await expect(
    page.getByRole('region', { name: 'Registered departments' }).getByText('Measured capability'),
  ).toHaveCount(0);
  await expect(page.getByText('HTTP 200', { exact: true })).toBeVisible();
  await expect(page.getByText('DOM / endpoint passed', { exact: true })).toBeVisible();
  await expect(
    page
      .getByLabel('Self-Healing Loop: Bug Detected → Patching → Verified')
      .locator('li[data-state="verified"]'),
  ).toHaveText('Verified');
  await expect(page.getByText('Repair recorded', { exact: true })).toBeVisible();
  const inspector = page.getByRole('region', { name: 'Code diff inspector' });
  await expect(inspector.locator('.status-failed')).not.toHaveCount(0);
  await expect(inspector.locator('.status-passed')).not.toHaveCount(0);
  await expect(page.getByRole('table', { name: 'Side-by-side code diff' })).toBeVisible();
  await inspector.getByRole('button', { name: 'Unified', exact: true }).click();
  await expect(page.getByLabel('Unified code diff')).toContainText('return a + b');
  await inspector.getByRole('tab', { name: /^files/i }).click();
  await inspector.getByRole('button', { name: 'README.md', exact: true }).click();
  await expect(inspector.locator('.file-preview')).toContainText('after one repair');
  await expect(inspector.locator('.file-list')).toContainText('show_hn.md');
  await expect(page.getByText('Offline demo · $0 API cost')).toBeVisible();
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: `/tmp/openfoundry-phase3-${info.project.name}.png`,
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
  await page.reload();
  await expect(page.getByRole('button', { name: 'Connect backend' })).toBeVisible();
  expect(errors).toEqual([]);
});

test('operator can defer a checkpoint without approving it and later reject the mission', async ({
  page,
}) => {
  await authenticate(page);
  await page.getByRole('button', { name: '⚡ Run Self-Healing Demo (No API Key)' }).click();
  await expect(page.getByRole('dialog')).toBeVisible({ timeout: 20000 });
  await page.getByRole('button', { name: 'Close approval review' }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(page.locator('.mission-indicators .status-awaiting_approval')).toBeVisible();
  await page.getByRole('button', { name: 'Review SPEC.md & checkpoint' }).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.getByRole('dialog').getByRole('button', { name: 'Reject', exact: true }).click();
  await expect(page.locator('.mission-indicators .status-cancelled')).toHaveText('cancelled');
  await expect(page.getByRole('dialog')).toHaveCount(0);
});

test('blueprint catalog populates goals, parameters and template tasks from the API', async ({
  page,
}) => {
  await authenticate(page);
  await page.getByLabel('Blueprint').selectOption('saas_mvp_builder');
  await expect(page.getByLabel('Product idea')).toHaveValue('an accessible notes app');
  await page.getByLabel('Product idea').fill('a lending library');
  await expect(page.getByLabel('CEO mission prompt')).toContainText('a lending library');
  await page.getByText('Template tasks · Chief of Staff adapts this plan').click();
  await expect(page.getByText('Implement the approved MVP', { exact: true })).toBeVisible();
  await page.getByLabel('Blueprint').selectOption('website_self_healer');
  await expect(page.getByLabel('Target application URL')).toHaveValue('http://127.0.0.1:3000');
  await page.getByLabel('Blueprint').selectOption('career_opportunity_hunter');
  await expect(page.getByLabel('Candidate profile')).toHaveValue('PROFILE.md');
});
