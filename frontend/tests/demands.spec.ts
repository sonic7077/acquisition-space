import { test, expect } from '@playwright/test';

test('客户线索是需求的唯一入口，转选题和写手直接引用线索原话', async ({page, request}) => {
  await request.post('/api/auth/login', {data: {username: 'admin', password: 'admin'}});
  const team = await (await request.post('/api/teams', {data: {name: '线索需求验收组'}})).json();
  const ip = await (await request.post('/api/ips', {data: {name: '线索需求验收IP', team_id: team.id}})).json();
  const lead = await (await request.post('/api/records/leads', {data: {
    ip_id: ip.id, title: '费用咨询', body: '家庭预算多少钱？', contactable: false,
    platform: '抖音', platform_comment_id: 'e2e-' + Date.now(),
  }})).json();
  const objection = await (await request.post('/api/records/leads', {data: {
    ip_id: ip.id, title: '质疑用户', body: '家庭预算都是骗人，智商税', contactable: false,
  }})).json();
  let writer = await (await request.post('/api/writers', {data: {
    ip_id: ip.id, name: '线索关联写手',
    answers: ['顾问', '家庭', '咨询', '待核实', '预算', '不承诺', '无', '直说', '无', '你', '1000', '视频号'],
  }})).json();
  writer = await (await request.post(`/api/writers/${writer.id}/compile`, {data: {version: writer.version}})).json();
  await request.post(`/api/writers/${writer.id}/activate`, {data: {version: writer.version}});

  await page.goto('/#needs');
  await page.getByRole('button', {name: '进入工作台'}).click();
  await expect(page.getByRole('heading', {name: '客户线索', exact: true})).toBeVisible();
  await expect(page).toHaveURL(/#leads$/);
  await expect(page.getByRole('button', {name: '需求库', exact: true})).toHaveCount(0);
  await page.getByLabel('筛选IP', {exact: true}).selectOption(ip.id);
  await page.getByText('评论分类与有效需求标准', {exact: true}).click();
  await expect(page.getByText('非反对', {exact: false}).first()).toBeVisible();
  await page.getByLabel('仅看有效需求', {exact: true}).check();
  const entry = page.locator(`[data-lead-id="${lead.id}"]`);
  await expect(entry).toContainText(lead.body);
  await expect(entry).toContainText('问价格');
  await expect(page.locator(`[data-lead-id="${objection.id}"]`)).toHaveCount(0);

  const response = page.waitForResponse(r => r.url().endsWith('/api/drafts') && r.request().method() === 'POST');
  await entry.getByRole('button', {name: '转为选题', exact: true}).click();
  const create = page.getByRole('dialog', {name: '从线索转为选题'});
  await create.getByLabel('选题标题').fill('怎样估算家庭保障预算');
  await create.getByRole('button', {name: '创建选题稿', exact: true}).click();
  const result = await response;
  expect(result.ok()).toBeTruthy();
  const draft = await result.json();
  expect(draft.need_id).toBe(lead.id);
  expect(draft.demand_snapshot.quotes[0].body).toBe(lead.body);
  expect(await (await request.get('/api/records/needs')).json()).toEqual([]);

  await page.getByRole('button', {name: '用写手改编', exact: true}).click();
  const rewrite = page.getByRole('dialog', {name: '用写手改编'});
  await expect(rewrite.getByLabel('改编文体', {exact: true})).toHaveValue('original-video-writer');
  await expect(rewrite.getByLabel('关联需求', {exact: true})).toHaveValue(lead.id);
  await rewrite.getByRole('button', {name: '预览改编上下文', exact: true}).click();
  await expect(rewrite.getByLabel('改编提示包', {exact: true})).toContainText(lead.body);
  await rewrite.getByRole('button', {name: '关闭', exact: true}).click();

  await page.getByRole('button', {name: 'IP与写手', exact: true}).click();
  await page.getByLabel('目标IP', {exact: true}).selectOption(ip.id);
  await page.getByText('写作与分析', {exact: true}).click();
  const writing = page.locator('.card').filter({has: page.getByRole('heading', {name: '创建内容任务', exact: true})});
  const demand = writing.getByLabel('关联需求（可选）', {exact: true});
  await demand.selectOption(lead.id);
  await expect(demand.locator(`option[value="${objection.id}"]`)).toHaveCount(0);
  await writing.getByLabel('内容主题与要求', {exact: true}).fill('回答咨询费用问题');
  await writing.getByRole('button', {name: '生成上下文预览', exact: true}).click();
  await expect(page.getByLabel('写作上下文预览', {exact: true})).toContainText(lead.body);
});
