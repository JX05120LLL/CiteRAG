// Run via @playwright/cli run-code --filename. Samples only; no business writes or media calls.
async (page) => {
  const folder = 'D:/code/CiteRAG/design/ui/react-candidates/exports';
  const captures = []; const errors = []; const writes = []; let apiRequests = 0;
  page.on('pageerror', (error) => errors.push(error.message));
  page.on('request', (request) => {
    if (/^https?:\/\/[^/]+\/api\//.test(request.url())) apiRequests++;
    if (request.method() !== 'GET') writes.push(request.method());
  });
  await page.context().addInitScript(() => {
    window.__previewMediaCalls = 0;
    if (navigator.mediaDevices) navigator.mediaDevices.getUserMedia = async () => { window.__previewMediaCalls++; throw new Error('Preview must not capture media'); };
  });
  async function capture(design, width, name) {
    await page.evaluate(() => document.fonts.ready);
    const metrics = await page.evaluate(() => {
      const composer = document.querySelector('.composer')?.getBoundingClientRect();
      const messages = document.querySelector('.message-scroll')?.getBoundingClientRect();
      return { viewport: innerWidth, documentWidth: document.documentElement.scrollWidth,
        composerAligned: !composer || !messages || Math.abs(composer.left - messages.left) < 1 && Math.abs(composer.right - messages.right) < 1,
        documentTextWidth: Math.min(...Array.from(document.querySelectorAll('.document-info')).filter((element) => element.getBoundingClientRect().width > 0).map((element) => element.getBoundingClientRect().width)),
        mediaCalls: window.__previewMediaCalls, synthetic: document.body.textContent.includes('合成设计样例，不是实际业务或通话结果。') };
    });
    if (metrics.documentWidth > width + 1) throw new Error(`Horizontal overflow: ${design}/${width}/${name}: ${metrics.documentWidth}`);
    if (!metrics.composerAligned || metrics.mediaCalls || !metrics.synthetic) throw new Error(`Safety / alignment failure: ${design}/${width}/${name}`);
    if (width <= 600 && metrics.documentTextWidth < width - 100) throw new Error(`Narrow document text column: ${design}/${width}/${name}`);
    const overlayOpen = await page.getByRole('dialog').count() > 0;
    await page.screenshot({ path: `${folder}/${design}-${name}-${width}.png`, fullPage: !overlayOpen, animations: 'disabled' });
    captures.push({ design, width, name, ...metrics });
  }
  for (const width of [1440, 390, 320]) {
    await page.setViewportSize({ width, height: width === 1440 ? 1000 : 844 });
    for (const design of ['b']) {
      const url = `http://127.0.0.1:5193/ui-preview.html?design=${design}&data=sample`;
      await page.goto(`${url}&page=workbench&source=closed`);
      await page.getByRole('button', { name: '查看来源：合成产品手册.md' }).waitFor();
      await capture(design, width, 'workbench');
      await page.getByRole('button', { name: '查看来源：合成产品手册.md' }).click();
      await page.getByText('引用摘录', { exact: true }).filter({ visible: true }).waitFor();
      await capture(design, width, 'sources');
      await page.getByRole('button', { name: '关闭来源' }).filter({ visible: true }).click();
      await page.goto(`${url}&page=knowledge`);
      await page.getByText('合成产品手册.md', { exact: true }).filter({ visible: true }).waitFor();
      await capture(design, width, 'knowledge');
      await page.getByRole('button', { name: '查看重建影响' }).click();
      await page.getByRole('dialog', { name: '重建影响说明' }).waitFor();
      if (await page.getByRole('button', { name: '确认重建' }).isEnabled()) throw new Error('Preview rebuild enabled');
      await capture(design, width, 'rebuild-impact');
      await page.getByRole('button', { name: '关闭说明' }).click();
      await page.getByTitle('2', { exact: true }).click();
      await page.getByText('合成操作说明_17.md', { exact: true }).filter({ visible: true }).waitFor();
      await capture(design, width, 'documents-page2');
      await page.getByText('已删除记录 · 展开查看与分页', { exact: true }).click();
      await page.getByText('合成旧版手册.md', { exact: true }).filter({ visible: true }).waitFor();
      await capture(design, width, 'deleted-history');
      await page.goto(`${url}&page=tasks`);
      await page.getByRole('button', { name: '查看任务详情' }).first().waitFor();
      await capture(design, width, 'tasks');
      await page.getByText(/已结束任务 ·/).click();
      await page.getByRole('button', { name: '查看任务详情' }).nth(1).click();
      await page.getByText('已记录的失败原因', { exact: true }).waitFor();
      await capture(design, width, 'task-detail');
      await page.goto(`${url}&page=status`);
      await page.getByText('尚无连通性验证结果', { exact: false }).waitFor();
      await capture(design, width, 'status');
      for (const phase of ['welcome', 'connecting', 'connected', 'reconnecting', 'ending', 'failed']) {
        await page.goto(`${url}&page=voice&phase=${phase}`);
        await page.getByRole('button', { name: '测试本地音频连接' }).waitFor();
        if (await page.getByRole('button', { name: '测试本地音频连接' }).isEnabled()) throw new Error('Preview voice connection enabled');
        const hangup = page.getByRole('button', { name: '挂断', exact: true });
        if (await hangup.count() && await hangup.isEnabled()) throw new Error('Preview hangup enabled');
        await capture(design, width, `voice-${phase}`);
      }
      if (width < 900) {
        await page.getByRole('button', { name: '打开导航' }).click();
        await page.getByRole('dialog', { name: '页面导航' }).waitFor();
        await capture(design, width, 'mobile-navigation');
        await page.getByRole('menuitem', { name: /系统状态/ }).filter({ visible: true }).click();
        await page.getByRole('heading', { name: '系统状态', exact: true }).waitFor();
      }
    }
    console.log(`Captured and checked ${width}px candidates`);
  }
  const result = { scope: 'Synthetic design preview only. No business or media acceptance.', captures, errors, writes, apiRequests };
  await page.evaluate((value) => { window.__previewReport = value; }, result);
  if (errors.length || writes.length || apiRequests) throw new Error(JSON.stringify({ errors, writes, apiRequests }));
  return { screenshots: captures.length, errors, writes, apiRequests, captures };
}
