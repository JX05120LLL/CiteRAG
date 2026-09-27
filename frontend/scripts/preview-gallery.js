async (page) => {
  await page.goto('file:///D:/code/CiteRAG/design/ui/react-candidates/index.html');
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.getByRole('heading', { name: 'CiteRAG · B 版交付与截图对照' }).waitFor();
  await page.waitForFunction(() => Array.from(document.querySelectorAll('.lead img')).every((image) => image.complete && image.naturalWidth > 0));
  for (const width of [1440, 390, 320]) {
    await page.setViewportSize({ width, height: 1000 });
    const first = page.locator('details').first();
    if (!(await first.evaluate((element) => element.open))) await first.locator('summary').click();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
    if (overflow) throw new Error('Offline gallery overflows at ' + width);
  }
  return { offlineGallery: 'passed', widths: [1440, 390, 320], leadImagesLoaded: await page.locator('.lead img').count(), businessOrMediaAcceptance: false };
}
