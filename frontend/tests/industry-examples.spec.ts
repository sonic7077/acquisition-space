import {test,expect} from '@playwright/test';

test('行业样例预览只补空白项并随写手保存',async({page,request})=>{
  await request.post('/api/auth/login',{data:{username:'admin',password:'admin'}});
  const team=await(await request.post('/api/teams',{data:{name:'行业样例验收'}})).json();
  const ip=await(await request.post('/api/ips',{data:{name:'行业样例IP',team_id:team.id}})).json();
  await page.goto('/');await page.getByRole('button',{name:'进入工作台'}).click();
  await page.getByRole('button',{name:'IP与写手',exact:true}).click();
  await page.getByLabel('目标IP',{exact:true}).selectOption(ip.id);
  const modal=page.getByRole('form',{name:'IP Skill 配置'});
  await modal.getByLabel('写手名称',{exact:true}).fill('装修参考写手');
  await expect(modal.getByRole('button',{name:'生成参考样例',exact:true})).toBeDisabled();
  await modal.getByLabel('所属行业',{exact:true}).fill('家装设计');
  await modal.getByText('填写 12 问',{exact:true}).click();
  await modal.getByLabel('第1问',{exact:true}).fill('我已填写的真实身份');
  await modal.getByRole('button',{name:'生成参考样例',exact:true}).click();
  await expect(modal.getByRole('button',{name:'仅填入空白项',exact:true})).toBeVisible();
  await expect(modal.getByLabel('第2问',{exact:true})).toHaveValue('');
  await modal.getByLabel('第3问',{exact:true}).fill('预览期间补充的真实产品');
  await modal.getByRole('button',{name:'仅填入空白项',exact:true}).click();
  await expect(modal.getByLabel('第1问',{exact:true})).toHaveValue('我已填写的真实身份');
  await expect(modal.getByLabel('第3问',{exact:true})).toHaveValue('预览期间补充的真实产品');
  await expect(modal.getByLabel('第2问',{exact:true})).toHaveValue(/装修/);
  await expect(modal.getByLabel('第11问',{exact:true})).toHaveValue('600');
  await modal.getByRole('button',{name:'保存草稿',exact:true}).click();
  await expect(page.getByRole('status')).toContainText('草稿');
  const writers=await(await request.get('/api/writers')).json();
  expect(writers.find((writer:{ip_id:string})=>writer.ip_id===ip.id)?.industry).toBe('家装设计');
});

test('管理员选择CLI认证并显示明确的权限不足提示',async({page})=>{
  await page.goto('/');await page.getByRole('button',{name:'进入工作台'}).click();
  await page.getByRole('button',{name:'系统设置',exact:true}).click();
  await page.locator('.settings-nav').getByRole('button',{name:'飞书',exact:true}).click();
  await page.locator('select[name="auth_mode"]').selectOption('cli');
  await page.locator('input[name="url"]').fill('https://example-tenant.feishu.cn/wiki/ExampleWikiToken12345');
  await page.getByRole('button',{name:'保存设置',exact:true}).click();
  await expect(page.locator('select[name="auth_mode"]')).toHaveValue('cli');
  await page.route('**/api/integrations/feishu/schema',route=>route.fulfill({status:409,contentType:'application/json',body:JSON.stringify({detail:'飞书 CLI 权限不足（99991672）：请开通多维表格读写权限'})}));
  await page.getByRole('button',{name:'读取三表与字段',exact:true}).click();
  await expect(page.getByRole('alert')).toContainText('99991672');
  await expect(page.getByRole('button',{name:'同步全部博主、作品和评论',exact:true})).toBeDisabled();
});

test('全量材料同步必须完成结构准备与预览并携带管理员导出选项',async({page})=>{
  await page.goto('/');await page.getByRole('button',{name:'进入工作台'}).click();
  await page.getByRole('button',{name:'系统设置',exact:true}).click();
  await page.locator('.settings-nav').getByRole('button',{name:'飞书',exact:true}).click();
  await page.locator('input[name="enabled"]').check();
  await page.getByRole('button',{name:'保存设置',exact:true}).click();
  const full=page.getByRole('button',{name:'同步全部数据与材料',exact:true});
  await expect(full).toBeDisabled();
  await page.route('**/api/integrations/feishu/schema',r=>r.fulfill({json:{ready:true,tables:{},warnings:[]}}));
  await page.route('**/api/integrations/feishu/library/prepare',r=>r.fulfill({json:{ready:true,tables:{},warnings:[]}}));
  await page.getByRole('button',{name:'准备材料同步表',exact:true}).click();
  await expect(full).toBeDisabled();
  await page.route('**/api/integrations/feishu/preview',r=>r.fulfill({json:{counts:{authors:7,works:8,comments:20},library_counts:{knowledge:11,writers:1,builtin_writers:8,drafts:3,ips:1},notice:'知识和写手正文同步'}}));
  await page.getByRole('button',{name:'预览全部现有数据',exact:true}).click();
  await expect(full).toBeEnabled();
  await expect(page.locator('.schema-preview')).toContainText('系统写手 8');
  let options:any;
  await page.route('**/api/integrations/feishu/sync',r=>{options=r.request().postDataJSON();return r.fulfill({json:{id:'test-job'}})});
  await full.click();
  await expect.poll(()=>options).toEqual({all_scopes:true,include_library:true});
});
