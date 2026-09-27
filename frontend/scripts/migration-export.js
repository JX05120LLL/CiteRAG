async (page) => await page.evaluate(() => window.__migrationReport ?? JSON.parse(sessionStorage.getItem('citerag.ui-verification') ?? 'null'))
