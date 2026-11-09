import { expect, test } from '@jupyterlab/galata';

/**
 * Don't load JupyterLab webpage before running the tests.
 * This is required to ensure we capture all log messages.
 */
test.use({ autoGoto: false });

test('the stardelt top bar is added', async ({ page }) => {
  await page.goto();

  const bar = page.locator('.stardelt-TopBar');
  await expect(bar).toHaveCount(1);

  const novaUrl = process.env.STARDELT_NOVA_URL ?? '';
  const runtime = process.env.STARDELT_RUNTIME ?? '';
  const link = bar.locator('.stardelt-TopBar-nova');
  if (novaUrl) {
    await expect(link).toHaveText('\u25C2 stardelt');
    await expect(link).toHaveAttribute('href', novaUrl);
  } else {
    await expect(link).toHaveCount(0);
  }
  const runtimeLabel = bar.locator('.stardelt-TopBar-runtime');
  if (runtime) {
    await expect(runtimeLabel).toHaveText(`runtime ${runtime}`);
  } else {
    await expect(runtimeLabel).toHaveCount(0);
  }
});

test('both stardelt themes are in the theme menu and load', async ({
  page
}) => {
  await page.goto();

  await page.menu.clickMenuItem('Settings>Theme>stardelt Silver');
  await expect(page.locator('link[href*="silver.css"]')).toHaveCount(1);

  await page.menu.clickMenuItem('Settings>Theme>stardelt Graphite');
  await expect(page.locator('link[href*="graphite.css"]')).toHaveCount(1);
});
