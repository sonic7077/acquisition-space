import { expect, test } from '@playwright/test';

test('素材写手按目标 IP 选线索，切换 IP 后清空已选需求', async ({ page, request }) => {
  await request.post('/api/auth/login', { data: { username: 'admin', password: 'admin' } });
  const team = await (await request.post('/api/teams', { data: { name: '素材线索验收组' } })).json();
  const first = await (await request.post('/api/ips', { data: { team_id: team.id, name: '素材线索甲' } })).json();
  const second = await (await request.post('/api/ips', { data: { team_id: team.id, name: '素材线索乙' } })).json();
  const material = await (await request.post('/api/records/materials', {
    data: { title: '跨 IP 线索验证素材', body: '家庭保障咨询' },
  })).json();
  async function createLead(ipId: string, body: string) {
    const response = await request.post('/api/records/leads', {
      data: { ip_id: ipId, title: '保险咨询', contactable: false, body },
    });
    expect(response.ok()).toBeTruthy();
    return response.json();
  }
  const lead = await createLead(first.id, '保险一年多少钱？');
  const other = await createLead(second.id, '给父母买保险怎么选？');

  await page.goto('/#materials');
  await page.getByRole('button', { name: '进入工作台' }).click();
  await page.getByRole('row').filter({ hasText: material.title }).getByRole('button', { name: '调用写手' }).click();
  const modal = page.getByRole('dialog', { name: '素材调用写手' });
  const target = modal.getByLabel('调用目标IP', { exact: true });
  const demand = modal.getByLabel('关联需求', { exact: true });
  await target.selectOption(first.id);
  await expect(demand.locator(`option[value="${lead.id}"]`)).toContainText('保险一年多少钱？');
  await expect(demand.locator(`option[value="${other.id}"]`)).toHaveCount(0);
  await demand.selectOption(lead.id);
  await modal.getByRole('button', { name: '预览上下文' }).click();
  await expect(modal.getByLabel('素材写手提示包')).toContainText('保险一年多少钱？');

  await target.selectOption(second.id);
  await expect(demand).toHaveValue('');
  await expect(demand.locator(`option[value="${other.id}"]`)).toHaveCount(1);
  await expect(demand.locator(`option[value="${lead.id}"]`)).toHaveCount(0);
  const context = page.waitForRequest(r => r.url().endsWith(`/materials/${material.id}/writing/context`));
  await modal.getByRole('button', { name: '预览上下文' }).click();
  expect((await context).postDataJSON()).toMatchObject({ ip_id: second.id, need_id: null });
});

test('写手的原作品评论标出来源，同一博主的作品切换时各自显示评论', async ({ page, request }) => {
  await request.post('/api/auth/login', { data: { username: 'admin', password: 'admin' } });
  async function create(path: string, data: Record<string, unknown>) {
    const response = await request.post('/api' + path, { data });
    expect(response.ok()).toBeTruthy();
    return response.json();
  }
  const team = await create('/teams', { name: '作品评论验收组' });
  const ip = await create('/ips', { name: '作品评论验收IP', team_id: team.id });
  const author = await create('/records/authors', { ip_id: ip.id, title: '同一保险博主', platform: '抖音' });
  const first = await create('/records/materials', {
    ip_id: ip.id, author_id: author.id, title: '家庭保障预算', platform: '抖音',
    source: 'https://www.douyin.com/video/101',
  });
  const second = await create('/records/materials', {
    ip_id: ip.id, author_id: author.id, title: '家庭医疗规划', platform: '抖音',
    source: 'https://www.douyin.com/video/102',
  });
  await create('/records/leads', { ip_id: ip.id, material_id: first.id, title: '预算评论用户', body: '家庭保障先列多少预算？' });
  await create('/records/leads', { ip_id: ip.id, material_id: second.id, title: '医疗评论用户', body: '医疗险的免赔额怎么理解？' });
  const draft = await create(`/materials/${first.id}/adopt`, {});

  await page.goto('/');
  await page.getByRole('button', { name: '进入工作台' }).click();
  await page.getByRole('button', { name: '选题库', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '素材调用写手' });
  const comments = dialog.getByRole('region', { name: '原作品评论' });
  await page.getByRole('row').filter({ hasText: first.title }).getByRole('button', { name: '调用写手' }).click();
  await expect(dialog.getByText('原作品评论 · 已入库 1 条 · 已选 0 条')).toBeVisible();
  await dialog.getByText('原作品评论 · 已入库 1 条 · 已选 0 条').click();
  await expect(comments.getByText(first.title, { exact: true })).toBeVisible();
  await expect(comments.getByText('抖音', { exact: true })).toBeVisible();
  await expect(comments.getByRole('link', { name: '查看原作品' })).toHaveAttribute('href', first.source);
  await expect(comments.getByText('预算评论用户', { exact: true })).toBeVisible();
  await comments.getByRole('checkbox', { name: /家庭保障先列多少预算/ }).check();
  await expect(comments.getByRole('checkbox', { name: /医疗险的免赔额/ })).toHaveCount(0);
  await expect(dialog.getByText('原作品评论 · 已入库 1 条 · 已选 1 条')).toBeVisible();
  await dialog.getByRole('button', { name: '关闭' }).click();

  await page.getByRole('row').filter({ hasText: second.title }).getByRole('button', { name: '调用写手' }).click();
  await dialog.getByText('原作品评论 · 已入库 1 条 · 已选 0 条').click();
  await expect(comments.getByText(second.title, { exact: true })).toBeVisible();
  await expect(comments.getByRole('checkbox', { name: /医疗险的免赔额/ })).not.toBeChecked();
  await expect(comments.getByRole('checkbox', { name: /家庭保障先列多少预算/ })).toHaveCount(0);
  await dialog.getByRole('button', { name: '关闭' }).click();

  await page.getByRole('button', { name: '审稿', exact: true }).click();
  await page.locator('.draft-item').filter({ hasText: draft.id.slice(0, 8) }).click();
  await page.getByRole('button', { name: '用写手改编' }).click();
  const rewrite = page.getByRole('dialog', { name: '用写手改编' });
  const rewriteComments = rewrite.getByRole('region', { name: '原作品评论' });
  await expect(rewriteComments.getByRole('link', { name: '查看原作品' })).toHaveAttribute('href', first.source);
  await expect(rewriteComments.getByRole('checkbox', { name: /家庭保障先列多少预算/ })).toBeVisible();
  await expect(rewriteComments.getByRole('checkbox', { name: /医疗险的免赔额/ })).toHaveCount(0);
});
