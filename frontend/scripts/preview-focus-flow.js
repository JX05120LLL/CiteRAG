async (page) => {
  const events = [];
  page.on('framenavigated', (frame) => { if (frame === page.mainFrame()) events.push(frame.url()); });
  await page.goto('http://127.0.0.1:5193/ui-preview.html?design=b&data=sample&source=closed');
  const source = page.getByRole('button', { name: '查看来源：合成产品手册.md' }); await source.waitFor();
  await source.focus(); await page.keyboard.press('Enter');
  await page.getByRole('dialog', { name: '原文来源' }).waitFor();
  await page.waitForTimeout(500); // Let the real drawer open transition complete before the normal close path.
  await page.keyboard.press('Escape');
  await page.getByRole('dialog', { name: '原文来源' }).waitFor({ state: 'hidden' });
  await page.waitForTimeout(500);
  return { events, focus: await source.evaluate((element) => ({ restored: element === document.activeElement,
    activeTag: document.activeElement?.tagName, activeLabel: document.activeElement?.getAttribute('aria-label') })) };
}
