async (page) => await page.evaluate(() => window.__interactionReport ?? window.__previewReport)
