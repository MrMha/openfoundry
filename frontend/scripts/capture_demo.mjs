/** Capture genuine UI states; provider timing is slowed, no mission data is fabricated. */
import { chromium } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
const [baseURL, directory] = process.argv.slice(2);
if (!baseURL || !directory || !process.env.OPENFOUNDRY_CAPTURE_TOKEN)
  throw new Error('Capture requires an API origin, frames directory and operator token');
await mkdir(directory, { recursive: true });
const browser = await chromium.launch(
  process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE
    ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE }
    : {},
);
const context = await browser.newContext({
  viewport: { width: 1280, height: 800 },
  deviceScaleFactor: 2,
  colorScheme: 'dark',
  reducedMotion: 'reduce',
});
const page = await context.newPage();
const errors = [];
page.on('pageerror', (error) => errors.push(error.message));
const manifest = [];
async function capture(name, label) {
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: join(directory, `${name}.png`), animations: 'disabled' });
  manifest.push({ name, label, url: page.url() });
}
try {
  await page.goto(baseURL);
  await page.getByRole('button', { name: 'Connect backend' }).click();
  await page.getByLabel('Operator token').fill(process.env.OPENFOUNDRY_CAPTURE_TOKEN);
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  const createdResponse = page.waitForResponse(
    (response) =>
      response.url().endsWith('/api/missions/demo') && response.request().method() === 'POST',
  );
  await page.getByRole('button', { name: '⚡ Run Self-Healing Demo (No API Key)' }).click();
  const created = await (await createdResponse).json();
  const review = page.getByRole('dialog');
  await review.waitFor();
  await review.getByRole('button', { name: 'Approve & Continue' }).click();
  await page.getByRole('button', { name: 'Focus War Room' }).click();
  const inspector = page.getByRole('region', { name: 'Code diff inspector' });
  await inspector.getByRole('button', { name: 'Unified', exact: true }).click({ timeout: 30000 });
  await capture('01-mission-start', 'Mission Start · Specification approved, code generated');
  await page
    .locator('.healing-steps li:first-child[data-state="observed"]')
    .waitFor({ timeout: 45000 });
  await page.getByText('DOM / endpoint failed', { exact: true }).waitFor();
  await capture(
    '02-bug-detected',
    'QA Failure Detected · Tests and DOM evidence caught the defect',
  );
  await page.locator('.healing-steps li[data-state="active"]').waitFor({ timeout: 30000 });
  await capture('03-auto-patch', 'Engineer Auto-Patch · Original acceptance checks are preserved');
  await page.getByText('5 of 5 tasks complete', { exact: false }).waitFor({ timeout: 45000 });
  await page.getByText('DOM / endpoint passed', { exact: true }).waitFor();
  await capture('04-verified', 'All Checks Passed · One repair, verified output, zero API cost');
  const response = await context.request.get(`${baseURL}/api/missions/${created.id}`, {
    headers: { Authorization: `Bearer ${process.env.OPENFOUNDRY_CAPTURE_TOKEN}` },
  });
  const mission = await response.json();
  if (!response.ok() || mission.status !== 'completed' || Number(mission.total_cost_usd) !== 0)
    throw new Error('Capture mission did not complete at zero API cost');
  if (errors.length) throw new Error(errors.join('\n'));
  await writeFile(join(directory, 'manifest.json'), JSON.stringify(manifest, null, 2));
} finally {
  await browser.close();
}
