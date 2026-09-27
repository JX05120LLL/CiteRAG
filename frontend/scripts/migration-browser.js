// Real browser / synthetic intercepted API. Never contacts a business backend or provider.
async (page) => {
  for (const child of page.context().pages()) if (child !== page && child.url().startsWith('http://127.0.0.1:5193/')) await child.close();
  await page.goto('http://127.0.0.1:5193/ui-preview.html?design=b&data=sample');
  const sample = await page.evaluate(async () => {
    const module = await import('/src/preview/samples.ts');
    return JSON.parse(JSON.stringify(module));
  });
  const calls = []; const captures = []; const errors = []; const checks = [];
  let bases = sample.sampleBases; let chats = sample.sampleChats; let documents = sample.sampleDocuments;
  let jobs = sample.sampleJobs; let messages = sample.sampleMessages;
  const health = { ...sample.sampleHealth, models_info: { region: 'synthetic', model_names: ['design-answer'] } };
  const testPage = await page.context().newPage();
  testPage.on('pageerror', (error) => errors.push(error.message));
  await testPage.addInitScript(() => {
    sessionStorage.setItem('citerag.workbench.selection', JSON.stringify({ kbId: 'design-library-1', chatId: 'design-chat-1' }));
    window.__migrationMediaCalls = 0;
    if (navigator.mediaDevices) navigator.mediaDevices.getUserMedia = async () => { window.__migrationMediaCalls++; throw new Error('No device capture in synthetic UI checks'); };
    document.addEventListener('DOMContentLoaded', () => {
      const label = document.createElement('div'); label.textContent = '自动化：合成接口与资料，非真实业务 / 模型 / 媒体验收';
      label.style.cssText = 'background:#fff2cc;color:#755313;padding:8px 12px;font:12px sans-serif;'; document.body.prepend(label);
    });
  });
  const acceptedJob = (operation, ids) => ({ ...sample.sampleJobs[0], id: `synthetic-${operation}`, operation,
    status: 'queued', stage: 'accepted', error_code: null, document_ids: ids, can_retry: false });
  await testPage.route('http://127.0.0.1:5193/api/**', async (route) => {
    const request = route.request(); const path = request.url().replace(/^https?:\/\/[^/]+/, ''); const method = request.method();
    calls.push({ path, method });
    let body; let contentType = 'application/json';
    const offset = Number(path.match(/[?&]offset=(\d+)/)?.[1] ?? 0);
    if (method === 'GET') {
      if (path === '/api/knowledge-bases') body = { items: bases };
      else if (path.startsWith('/api/conversations?')) body = { items: chats.slice(offset, offset + 20) };
      else if (path.endsWith('/messages')) body = { items: messages[path.split('/')[3]] ?? [] };
      else if (path.includes('/documents?')) {
        const items = path.includes('scope=deleted') ? sample.sampleDeleted : documents;
        body = { items: items.slice(offset, offset + 10), total: items.length, counts: { ready: 16, failed: 1, deleted: 1 } };
      } else if (path.includes('/jobs?')) body = { items: jobs.slice(offset, offset + 10), total: jobs.length, active_items: [], failed_count: 2 };
      else if (path.startsWith('/api/jobs/')) body = jobs.find((job) => job.id === path.split('/')[3]);
      else if (path.endsWith('/blocks')) body = { items: [{ ordinal: 0, text: '合成公开片段，用于界面定位检查。', locator: { kind: 'lines', line_start: 1, line_end: 2 }, start: 0, end: 23 }] };
      else if (path === '/api/status') body = health;
      else if (path === '/api/voice/status') body = sample.sampleVoice;
      else if (path.startsWith('/api/conversations/')) body = chats.find((chat) => chat.id === path.split('/')[3]);
      else if (path.endsWith('/original')) { body = '合成公开原文'; contentType = 'text/plain'; }
    } else {
      const payload = request.headers()['content-type']?.includes('application/json') ? request.postDataJSON() : null;
      if (path.endsWith('/voice/token')) throw new Error('Unexpected media token request');
      if (path.endsWith('/messages/stream')) {
        const result = { ...sample.sampleMessages['design-chat-1'][0], message_id: 'synthetic-new-message', client_message_id: payload.client_message_id,
          question: payload.text, mode: 'auto', route: 'general', text: '合成已保存回答；没有真实模型调用。', citations: [] };
        messages[path.split('/')[3]] = [result];
        contentType = 'text/event-stream'; body = `event: accepted\ndata: ${JSON.stringify({ ...result, status: 'running', saved: false, text: '' })}\n\nevent: saved\ndata: ${JSON.stringify(result)}\n\n`;
        if (payload.mode !== 'auto' || payload.exact) throw new Error('Lost automatic route contract');
      } else if (path.includes('/messages/') && path.endsWith('/retry')) {
        body = { ...messages[path.split('/')[3]][0], status: 'answered', text: '合成重试完成', citations: [], error_code: null };
        messages[path.split('/')[3]] = [body];
      } else if (method === 'PATCH' && path.includes('/conversations/')) {
        chats = chats.map((chat) => chat.id === path.split('/')[3] ? { ...chat, title: payload.title } : chat); body = chats.find((chat) => chat.id === path.split('/')[3]);
      } else if (method === 'DELETE' && path.includes('/conversations/')) { chats = chats.filter((chat) => chat.id !== path.split('/')[3]); body = { deleted: true }; }
      else if (path === '/api/conversations' && method === 'POST') {
        body = { ...sample.sampleChats[0], id: 'synthetic-new-chat', kb_id: payload.kb_id, title: '新合成聊天' }; chats = [body, ...chats];
      } else if (path === '/api/knowledge-bases' && method === 'POST') {
        body = { id: 'synthetic-new-library', name: payload.name, status: 'empty' }; bases = [...bases, body];
      } else if (path.includes('/knowledge-bases/') && method === 'PATCH') {
        bases = bases.map((base) => base.id === path.split('/')[3] ? { ...base, name: payload.name } : base); body = bases.find((base) => base.id === path.split('/')[3]);
      } else if (path.endsWith('/documents') && method === 'POST') body = acceptedJob('upload', ['synthetic-upload']);
      else if (path.endsWith('/rebuild')) body = acceptedJob('rebuild', ['design-document-1']);
      else if (path.startsWith('/api/documents/') && path.endsWith('/delete')) body = acceptedJob('delete', [path.split('/')[3]]);
      else if (path.endsWith('/replacement')) body = acceptedJob('replace', [path.split('/')[3]]);
      else if (path.startsWith('/api/documents/') && method === 'PATCH') { body = { ...documents.find((doc) => doc.id === path.split('/')[3]), ...payload }; documents = documents.map((doc) => doc.id === body.id ? body : doc); }
      else if (path.startsWith('/api/jobs/') && path.endsWith('/retry')) body = { ...jobs.find((job) => job.id === path.split('/')[3]), status: 'queued', error_code: null };
    }
    if (!body) throw new Error(`Unimplemented isolated fixture: ${method} ${path}`);
    await route.fulfill({ status: 200, contentType, body: typeof body === 'string' ? body : JSON.stringify(body) });
  });
  async function navigate(label) {
    const sidebar = testPage.locator('.primary-sidebar');
    if (await sidebar.isVisible()) await sidebar.getByRole('menuitem', { name: label }).click();
    else { await testPage.getByRole('button', { name: '打开导航', exact: true }).click(); await testPage.getByRole('dialog').getByRole('menuitem', { name: label }).click();
      await testPage.getByRole('dialog').waitFor({ state: 'hidden' }); }
  }
  async function capture(name, width) {
    await testPage.evaluate(() => scrollTo(0, 0));
    await testPage.evaluate(() => document.fonts.ready);
    const metrics = await testPage.evaluate(() => {
      const a = document.querySelector('.composer')?.getBoundingClientRect(); const b = document.querySelector('.message-scroll')?.getBoundingClientRect();
      return { width: innerWidth, documentWidth: document.documentElement.scrollWidth,
        composerAligned: !a || !b || Math.abs(a.left - b.left) < 1 && Math.abs(a.right - b.right) < 1,
        mediaCalls: window.__migrationMediaCalls };
    });
    if (metrics.documentWidth > width + 1 || !metrics.composerAligned || metrics.mediaCalls) throw new Error(`Layout/media defect ${name}/${width}: ${JSON.stringify(metrics)}`);
    await testPage.screenshot({ path: `D:/code/CiteRAG/design/ui/react-candidates/exports/b-final-${name}-${width}.png`, fullPage: !['sources', 'task-detail'].includes(name), animations: 'disabled' });
    captures.push({ name, ...metrics });
  }
  for (const width of [1440, 390, 320]) {
    await testPage.setViewportSize({ width, height: width === 1440 ? 1000 : 844 });
    await testPage.goto('http://127.0.0.1:5193/index.html');
    await testPage.getByRole('button', { name: /合成产品手册.md/ }).waitFor(); await capture('workbench', width);
    const trigger = testPage.getByRole('button', { name: /合成产品手册.md/ }); await trigger.focus(); await trigger.click();
    await testPage.getByRole('dialog', { name: '原文来源' }).waitFor(); await capture('sources', width);
    await testPage.getByRole('dialog').getByRole('button', { name: '关闭', exact: true }).click();
    await testPage.getByRole('dialog').waitFor({ state: 'hidden' });
    await testPage.waitForFunction(() => document.activeElement?.textContent?.includes('合成产品手册.md'), null, { timeout: 3000 });
    await navigate('知识库与资料'); await capture('knowledge-bases', width);
    await testPage.getByRole('button', { name: '管理资料', exact: true }).first().click();
    await testPage.getByText('合成产品手册.md', { exact: true }).waitFor(); await capture('documents', width);
    await testPage.getByTitle('2', { exact: true }).click(); await testPage.getByText('合成操作说明_17.md', { exact: true }).waitFor(); await capture('documents-page2', width);
    await testPage.getByText(/已删除记录 ·/).click(); await testPage.getByText('合成旧版手册.md', { exact: true }).waitFor(); await capture('deleted', width);
    await navigate('处理任务'); await testPage.getByText(/已结束任务 ·/).click();
    await testPage.getByRole('button', { name: '查看任务详情', exact: true }).first().click();
    await testPage.getByRole('dialog', { name: '任务详情' }).waitFor(); await capture('task-detail', width);
    await testPage.getByRole('dialog').getByRole('button', { name: '关闭', exact: true }).click();
    await testPage.getByRole('dialog').waitFor({ state: 'hidden' }); await capture('tasks', width);
    await navigate('系统状态'); await testPage.getByText('design-answer', { exact: true }).waitFor(); await capture('status', width);
    await navigate('语音通话'); await testPage.getByRole('button', { name: '测试本地音频连接', exact: true }).waitFor(); await capture('voice-welcome', width);
    await testPage.goto('http://127.0.0.1:5193/voice.html?conversation=design-chat-1');
    await testPage.getByRole('button', { name: '测试本地音频连接', exact: true }).waitFor(); await capture('voice-standalone', width);
  }
  // Explicit clicks hit real frontend methods; all writes are intercepted synthetic API results.
  await testPage.setViewportSize({ width: 1440, height: 1000 }); await testPage.goto('http://127.0.0.1:5193/index.html');
  await testPage.getByRole('button', { name: '聊天改名', exact: true }).click();
  await testPage.getByRole('textbox', { name: '聊天名称' }).fill('浏览器合成聊天改名'); await testPage.getByRole('button', { name: '保存名称', exact: true }).click();
  await testPage.getByText('浏览器合成聊天改名', { exact: true }).waitFor(); checks.push('chat-rename');
  await testPage.locator('.composer textarea').fill('合成通用提问'); await testPage.getByRole('button', { name: '发送问题 ↑', exact: true }).click();
  await testPage.getByText('合成已保存回答；没有真实模型调用。', { exact: true }).waitFor(); checks.push('auto-route-saved-SSE');
  await navigate('知识库与资料'); await testPage.getByRole('button', { name: '创建知识库', exact: true }).click();
  await testPage.getByRole('dialog').getByRole('textbox').fill('浏览器合成新知识库'); await testPage.getByRole('dialog').getByRole('button', { name: '创建', exact: true }).click();
  await testPage.getByText('浏览器合成新知识库', { exact: true }).waitFor(); checks.push('knowledge-create');
  await testPage.getByRole('button', { name: '管理资料', exact: true }).first().click();
  await testPage.getByRole('button', { name: '删除失败资料', exact: true }).click();
  await testPage.getByRole('dialog').getByRole('button', { name: '确认', exact: true }).click();
  await testPage.getByText('删除任务已受理。请以持久任务的核验及清理结果为准。', { exact: true }).waitFor(); checks.push('failed-document-delete');
  await testPage.getByRole('button', { name: '从原文重建', exact: true }).click();
  if (await testPage.getByRole('dialog').getByRole('button', { name: '重建知识库', exact: true }).isEnabled()) throw new Error('Rebuild lacks impact acknowledgement');
  await testPage.getByRole('checkbox').check(); await testPage.getByRole('dialog').getByRole('button', { name: '重建知识库', exact: true }).click();
  await testPage.getByRole('dialog').getByText('任务已受理。解析与入库结果请以下方持久任务为准。', { exact: true }).waitFor(); checks.push('acknowledged-rebuild');
  await testPage.getByRole('dialog').getByRole('button', { name: '取消', exact: true }).click();
  await testPage.getByRole('dialog').waitFor({ state: 'hidden' });
  await testPage.getByText('添加资料', { exact: true }).click();
  await testPage.locator('input[type=file]').first().setInputFiles('D:/code/CiteRAG/frontend/scripts/synthetic-upload.txt');
  await testPage.getByRole('button', { name: '上传并处理', exact: true }).click();
  await testPage.locator('#main-content').getByText('任务已受理。解析与入库结果请以下方持久任务为准。', { exact: true }).waitFor(); checks.push('file-selection-and-upload-acceptance');
  await testPage.getByRole('button', { name: '资料详情', exact: true }).first().click();
  await testPage.getByRole('dialog').getByRole('textbox', { name: '文档编号', exact: true }).fill('SYNTHETIC-001');
  await testPage.getByRole('dialog').getByRole('button', { name: '确认属性', exact: true }).click();
  await testPage.getByRole('dialog').getByText('确认属性', { exact: true }).waitFor();
  await testPage.waitForFunction(() => document.querySelector('#main-content')?.textContent.includes('已确认资料属性。')); checks.push('document-attributes');
  await testPage.getByRole('dialog').locator('input[type=file]').setInputFiles('D:/code/CiteRAG/frontend/scripts/synthetic-upload.txt');
  await testPage.getByRole('dialog', { name: '确认操作' }).getByRole('button', { name: '确认', exact: true }).click();
  await testPage.locator('#main-content').getByText('替换任务已受理。请以持久任务的核验及清理结果为准。', { exact: true }).waitFor(); checks.push('replacement-with-impact-confirmation');
  await testPage.getByRole('dialog', { name: '资料详情' }).getByRole('button', { name: '关闭', exact: true }).click();
  await testPage.getByRole('dialog').waitFor({ state: 'hidden' });
  await testPage.getByRole('button', { name: '查看解析位置', exact: true }).first().click();
  await testPage.getByText('合成公开片段，用于界面定位检查。', { exact: true }).waitFor(); checks.push('parsed-blocks-read');
  await testPage.getByRole('dialog').getByRole('button', { name: '关闭', exact: true }).click(); await testPage.getByRole('dialog').waitFor({ state: 'hidden' });
  await navigate('处理任务'); await testPage.getByText(/已结束任务 ·/).click();
  await testPage.getByRole('button', { name: '重试任务', exact: true }).first().click();
  await testPage.getByText('原任务已受理重试。请刷新查看处理结果。', { exact: true }).waitFor(); checks.push('job-retry');
  await testPage.getByTitle('2', { exact: true }).click(); await testPage.waitForFunction(() => document.querySelectorAll('.job-row').length === 2); checks.push('job-backend-pagination');
  await navigate('知识库与资料'); await testPage.getByRole('button', { name: '返回知识库', exact: true }).click();
  await testPage.getByRole('button', { name: '知识库改名', exact: true }).first().click();
  await testPage.getByRole('dialog').getByRole('textbox').fill('合成库改名'); await testPage.getByRole('dialog').getByRole('button', { name: '保存名称', exact: true }).click();
  await testPage.getByText('合成库改名', { exact: true }).first().waitFor(); checks.push('knowledge-rename');
  await navigate('对话工作台'); await testPage.getByRole('button', { name: /新建聊天/ }).filter({ visible: true }).first().click();
  await testPage.getByText('新合成聊天', { exact: true }).waitFor(); checks.push('chat-create');
  await testPage.getByRole('button', { name: '删除聊天', exact: true }).click(); await testPage.getByRole('dialog').getByRole('button', { name: '确认', exact: true }).click();
  await testPage.getByText('选择知识库，新建或打开聊天', { exact: true }).waitFor(); checks.push('chat-delete-confirmation');
  if (errors.length) throw new Error(`Browser exceptions: ${errors.join('; ')}`);
  const report = { evidence: 'Real browser / synthetic intercepted API only', checks, captures, calls, errors,
    realBusinessAcceptance: false, realModelCalls: 0, realWebRTC: false, mediaCalls: 0 };
  await page.evaluate((value) => { window.__migrationReport = value; sessionStorage.setItem('citerag.ui-verification', JSON.stringify(value)); }, report);
  await testPage.unroute('http://127.0.0.1:5193/api/**'); await testPage.close();
  return report;
}
