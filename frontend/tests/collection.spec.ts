import { test, expect, type Page } from '@playwright/test';

test('选题库提交有界采集、展示视频作者和研究评论、管理员配置TikHub', async ({page, request}) => {
  const errors: string[]=[]; page.on('pageerror', e=>errors.push(e.message));
  await request.post('/api/auth/login',{data:{username:'admin',password:'admin'}});
  const team=await (await request.post('/api/teams',{data:{name:'采集验收组'}})).json();
  const ip=await (await request.post('/api/ips',{data:{name:'采集验收IP',team_id:team.id}})).json();
  let submitted: any;
  await page.route('**/api/collection/status',r=>r.fulfill({json:{configured:true,enabled:true,daily_budget:100,today_calls:3,feishu_enabled:false}}));
  await page.route('**/api/collection/runs',r=>r.fulfill({json:[]}));
  await page.route('**/api/collection/search',async r=>{submitted=r.request().postDataJSON();await r.fulfill({json:{id:'preview-run',status:'queued'}})});
  await page.route('**/api/records/materials',r=>r.fulfill({json:[{id:'test-work',ip_id:ip.id,title:'先问清楚服务范围',body:'原视频描述',platform_id:'v1',author_id:'test-author',author:'样例博主',likes:500,duration:45,comments_count:2,collected_comments:1,source_type:'tikhub',eligible:true,source:'https://www.douyin.com/video/v1',status:'new',keyword:'咨询服务'}]}));
  await page.route('**/api/records/authors',r=>r.fulfill({json:[{id:'test-author',ip_id:ip.id,title:'样例博主',followers:6000,sample_count:1,platform_id:'u1',source_type:'tikhub'}]}));
  await page.route('**/api/records/leads',r=>r.fulfill({json:[{id:'test-comment',ip_id:ip.id,title:'评论用户',body:'请问多少钱？',work_title:'先问清楚服务范围',source_type:'platform_comment',contactable:false,intent:'问价格',material_id:'test-work'}]}));
  await page.goto('/');await page.getByRole('button',{name:'进入工作台'}).click();
  await page.getByRole('button',{name:'选题库',exact:true}).click();
  await expect(page.getByRole('heading',{name:'按关键词找对标内容'})).toBeVisible();
  await expect(page.locator('.card-head h2').first()).toHaveText('爆款素材');
  await expect(page.getByRole('button',{name:'采集评论',exact:true})).toHaveCount(0);
  await expect(page.getByRole('button',{name:'全部',exact:true})).toHaveClass(/active/);
  await page.getByLabel('搜索关键词').fill('咨询服务');
  await page.getByLabel('视频号',{exact:true}).check();
  await page.getByLabel('归属IP（可选）',{exact:true}).selectOption(ip.id);
  await page.getByRole('button',{name:'搜索并自动入库',exact:true}).click();
  await expect.poll(()=>submitted?.keywords).toBe('咨询服务');
  expect(submitted.platforms).toEqual(['douyin','xiaohongshu','wechat_channels']);
  expect(submitted.comment_pages).toBeGreaterThan(0);
  expect(submitted.ip_id).toBe(ip.id); expect(submitted.pages).toBeLessThanOrEqual(5);
  await expect(page.getByText('先问清楚服务范围',{exact:true})).toBeVisible();
  await expect(page.getByText('样例博主',{exact:true})).toBeVisible();
  await page.screenshot({path:'/tmp/acquisition-topics.png',fullPage:true});
  await page.getByRole('button',{name:'对标博主',exact:true}).click();
  await expect(page.getByText('样例博主',{exact:true})).toBeVisible();
  await expect(page.getByText('平台ID：u1')).toBeVisible();
  await page.getByRole('button',{name:'客户线索',exact:true}).click();
  await page.getByRole('button',{name:'展开全部',exact:true}).click();
  await expect(page.locator('.lead-entry').getByText('研究评论',{exact:true})).toBeVisible();
  await expect(page.getByText('来自作品：先问清楚服务范围')).toBeVisible();
  await page.getByRole('button',{name:'系统设置',exact:true}).click();
  await page.getByRole('button',{name:'TikHub采集',exact:true}).click();
  await page.getByLabel('API Key · 未配置').fill('e2e-fake-key');
  await page.getByLabel('启用TikHub采集').check();
  await page.getByRole('button',{name:'保存设置',exact:true}).click();
  await expect(page.getByLabel('API Key · 已配置')).toHaveValue('');
  await expect(page.getByLabel('启用TikHub采集')).toBeChecked();
  await page.setViewportSize({width:390,height:844});
  await page.getByRole('button',{name:'选题库',exact:true}).click();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
  expect(errors).toEqual([]);
});

test('飞书三表字段预览与准备入口只展示映射结果',async({page})=>{
  await page.route('**/api/integrations/feishu/schema',r=>r.fulfill({json:{ready:false,tables:{authors:{name:'博主',table_id:'tbl-a',fields:[{field_name:'标题'}],mapping:{title:{field_name:'标题',type:1}}}},warnings:['作品缺少文本字段：获客空间ID、内容结构；请检查并准备同步字段']}}));
  await page.route('**/api/integrations/feishu/prepare',r=>r.fulfill({json:{ready:true,tables:Object.fromEntries([['authors','博主'],['works','作品'],['comments','评论']].map(([k,name])=>[k,{name,table_id:'tbl-'+k,fields:[{field_name:'获客空间ID'},{field_name:'内容结构'}],mapping:{local_id:{field_name:'获客空间ID',type:1},structure:{field_name:'内容结构',type:1}}}])),warnings:[]}}));
  await page.goto('/');await page.getByRole('button',{name:'进入工作台'}).click();
  await page.getByRole('button',{name:'系统设置',exact:true}).click();
  await page.getByRole('button',{name:'飞书',exact:true}).click();
  await expect(page.getByLabel('多维表格 / 知识空间链接')).toHaveValue(/Me3owpcvFirsbEkqMc9cZ7PLnAf/);
  await page.getByRole('button',{name:'读取三表与字段'}).click();
  await expect(page.getByText('请完成目标表与字段配置',{exact:true})).toBeVisible();
  await page.getByRole('button',{name:'检查并准备同步字段'}).click();
  await expect(page.getByText('三表结构可同步',{exact:true})).toBeVisible();
  await page.evaluate(()=>window.scrollTo(0,0));
  await page.screenshot({path:'/tmp/acquisition-feishu-schema.png',fullPage:true});
});

test('默认全部和平台过滤，搜索无需IP，未归属素材写稿时选IP',async({page,request})=>{
  await request.post('/api/auth/login',{data:{username:'admin',password:'admin'}});
  const ips=await(await request.get('/api/ips')).json();
  let submitted:any,transcriptRequests=0;
  await page.route('**/api/collection/status',r=>r.fulfill({json:{enabled:true,configured:true,today_calls:0,daily_budget:100}}));
  await page.route('**/api/collection/runs',r=>r.fulfill({json:[]}));
  await page.route('**/api/records/materials',r=>r.fulfill({json:[{id:'unassigned-xhs',ip_id:null,platform:'小红书',transcript_supported:true,title:'低赞笔记也展示',likes:1,eligible:false,comments_status:'completed',collected_comments:2,status:'new'},{id:'wx-work',ip_id:null,platform:'视频号',title:'视频号内容',likes:200,status:'excluded'}]}));
  await page.route('**/api/collection/search',async r=>{submitted=r.request().postDataJSON();await r.fulfill({json:{id:'unassigned-run'}})});
  await page.route('**/api/materials/unassigned-xhs/transcript',r=>{
    transcriptRequests++;
    return r.fulfill({json:{text:'这段话来自原视频的转写。',source:'speech_recognition',cached:false}});
  });
  await page.goto('/');await page.getByRole('button',{name:'进入工作台'}).click();await page.getByRole('button',{name:'选题库',exact:true}).click();
  await expect(page.getByText('低赞笔记也展示',{exact:true})).toBeVisible();
  await expect(page.getByText('视频号内容',{exact:true})).toBeVisible();
  await page.getByLabel('素材来源').selectOption('小红书');
  await expect(page.getByText('视频号内容',{exact:true})).toHaveCount(0);
  await expect(page.getByText('已入库 2',{exact:true})).toBeVisible();
  await page.getByLabel('搜索关键词').fill('职场沟通,咨询服务');
  await page.getByLabel('抖音',{exact:true}).uncheck();
  await expect(page.getByLabel('归属IP（可选）',{exact:true})).not.toHaveAttribute('required','');
  await page.getByRole('button',{name:'搜索并自动入库',exact:true}).click();
  await expect.poll(()=>submitted?.ip_id===null).toBe(true);expect(submitted.platforms).toEqual(['xiaohongshu']);
  await expect(page.getByRole('button',{name:'加入写稿',exact:true})).toHaveCount(0);
  await page.getByRole('button',{name:'调用写手',exact:true}).click();
  const dialog=page.getByRole('dialog',{name:'素材调用写手'});
  await dialog.getByLabel('调用目标IP',{exact:true}).selectOption(ips[0].id);
  await expect(dialog.getByLabel('对标逐字稿',{exact:true})).toHaveValue('这段话来自原视频的转写。');
  await expect(dialog.getByRole('checkbox',{name:/已核对逐字稿/})).not.toBeChecked();
  expect(transcriptRequests).toBe(1);
});

test('自动转写失败可重试，返回结果保留用户手工录入', async ({page, request}) => {
  await request.post('/api/auth/login', {data:{username:'admin',password:'admin'}});
  const team = await (await request.post('/api/teams', {data:{name:'转写重试验收'}})).json();
  const ip = await (await request.post('/api/ips', {data:{name:'转写重试IP',team_id:team.id}})).json();
  await page.route('**/api/records/materials', route => route.fulfill({json:[
    {id:'video-retry',ip_id:ip.id,title:'转写重试素材',platform:'视频号',transcript_supported:true},
  ]}));
  let calls = 0;
  await page.route('**/api/materials/video-retry/transcript', route => {
    calls++;
    return calls === 1
      ? route.fulfill({status:422,json:{detail:'本次未能读取视频音轨'}})
      : route.fulfill({json:{text:'后台辨识到的原始语音。',cached:false}});
  });
  await openTopicCollection(page);
  await page.getByRole('button',{name:'调用写手',exact:true}).click();
  const dialog = page.getByRole('dialog',{name:'素材调用写手'});
  const reference = dialog.getByLabel('对标逐字稿',{exact:true});
  await expect(dialog).toContainText('本次未能读取视频音轨');
  await reference.fill('手工核对后的对标逐字稿。');
  await dialog.getByRole('button',{name:'重试提取',exact:true}).click();
  await expect(dialog.getByRole('button',{name:'填入自动转写结果',exact:true})).toBeVisible();
  await expect(reference).toHaveValue('手工核对后的对标逐字稿。');
  await dialog.getByRole('button',{name:'填入自动转写结果',exact:true}).click();
  await expect(reference).toHaveValue('后台辨识到的原始语音。');
  expect(calls).toBe(2);
});

async function openTopicCollection(page: Page) {
  await page.route('**/api/collection/status', route => route.fulfill({
    json: { enabled: true, configured: true, today_calls: 0, daily_budget: 100 },
  }));
  await page.goto('/');
  await page.getByRole('button', { name: '进入工作台' }).click();
  await page.getByRole('button', { name: '选题库', exact: true }).click();
}

test('采集表单使用爆款系统的默认设置', async ({ page }) => {
  await page.route('**/api/collection/search', route => route.fulfill({ json: { id: 'preview-run' } }));
  await openTopicCollection(page);
  await expect(page.getByLabel('我的搜索方案')).toHaveCount(0);
  await expect(page.getByLabel('搜索方案名称')).toHaveCount(0);
  await expect(page.getByRole('button', { name: /保存搜索方案|删除方案/ })).toHaveCount(0);
  await page.getByLabel('搜索关键词').fill('咨询服务');
  const request = page.waitForRequest('**/api/collection/search');
  await page.getByRole('button', { name: '搜索并自动入库' }).click();
  expect((await request).postDataJSON()).toEqual({
    ip_id: null,
    keywords: '咨询服务',
    platforms: ['douyin', 'xiaohongshu'],
    pages: 1,
    per_word_limit: 10,
    max_videos: 20,
    comment_pages: 1,
    enrich_authors: false,
    sort_type: '1',
    publish_time: 'platform_default',
  });
  await expect(page.getByLabel('视频号', { exact: true })).not.toBeChecked();
  await expect(page.getByLabel('发布时间')).toHaveValue('platform_default');
  await page.getByRole('button', { name: '采集范围 · 展开', exact: true }).click();
  await expect(page.getByRole('textbox', { name: /最低点赞|最低时长/ })).toHaveCount(0);
  await expect(page.getByText('作品入库后，按系统设置中的作品推荐规则统一计算。')).toBeVisible();
});

test('可用素材按作品规则判断历史记录', async ({ page }) => {
  await page.route('**/api/records/materials', route => route.fulfill({ json: [
    { id: 'legacy-available', title: '历史入库的低赞作品', likes: 5, status: 'new', eligible: false,
      discovery: { available: true, recommended: false } },
    { id: 'unavailable', title: '来源不可用的作品', likes: 900, status: 'new', eligible: true,
      discovery: { available: false, recommended: false } },
    { id: 'recommended', title: '推荐参考的作品', likes: 1000, status: 'new',
      discovery: { available: true, recommended: true, candidate: true } },
  ] }));
  await openTopicCollection(page);
  await expect(page.getByText('历史入库的低赞作品', { exact: true })).toBeVisible();
  await expect(page.getByText('来源不可用的作品', { exact: true })).toBeVisible();

  await page.getByRole('button', { name: '可用素材', exact: true }).click();
  await expect(page.getByText('历史入库的低赞作品', { exact: true })).toBeVisible();
  await expect(page.getByText('推荐参考的作品', { exact: true })).toBeVisible();
  await expect(page.getByText('来源不可用的作品', { exact: true })).toHaveCount(0);
  await expect(page.getByText('未达可用素材筛选条件', { exact: true })).toHaveCount(0);

  await page.getByRole('button', { name: '推荐', exact: true }).click();
  await expect(page.getByText('推荐参考的作品', { exact: true })).toBeVisible();
  await expect(page.getByText('历史入库的低赞作品', { exact: true })).toHaveCount(0);
});

test('采集表单的数字可清空、逐字输入和粘贴', async ({ page, context }) => {
  await page.route('**/api/collection/search', route => route.fulfill({ json: { id: 'preview-run' } }));
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  await openTopicCollection(page);
  await page.getByLabel('搜索关键词').fill('保障范围');
  await page.getByRole('button', { name: '采集范围 · 展开', exact: true }).click();

  const perWord = page.getByLabel('每词每平台最多作品数');
  await perWord.fill('');
  await expect(perWord).toHaveValue('');
  await perWord.pressSequentially('015');
  await expect(perWord).toHaveValue('015');
  await perWord.press('Tab');
  await expect(perWord).toHaveValue('15');

  const pages = page.getByLabel('每词每平台搜索页数');
  await page.evaluate(() => navigator.clipboard.writeText('3'));
  await pages.selectText();
  await pages.press('ControlOrMeta+V');
  await expect(pages).toHaveValue('3');

  const request = page.waitForRequest('**/api/collection/search');
  await page.getByRole('button', { name: '搜索并自动入库' }).click();
  expect((await request).postDataJSON()).toMatchObject({ per_word_limit: 15, pages: 3 });
});

for (const { label, value } of [{ label: '空值', value: '' }, { label: '越界值', value: '21' }]) {
  test(`搜索时展开采集参数并指向${label}`, async ({ page }) => {
    let submitted = false;
    await page.route('**/api/collection/search', route => {
      submitted = true;
      return route.fulfill({ json: { id: 'unexpected-request' } });
    });
    await openTopicCollection(page);
    await page.getByLabel('搜索关键词').fill('保障范围');
    const advanced = page.getByRole('button', { name: /^采集范围 · / });
    await advanced.click();
    const perWord = page.getByLabel('每词每平台最多作品数');
    await perWord.fill(value);
    await advanced.click();
    await expect(advanced).toHaveAttribute('aria-expanded', 'false');

    await page.getByRole('button', { name: '搜索并自动入库', exact: true }).click();

    await expect(advanced).toHaveAttribute('aria-expanded', 'true');
    await expect(page.getByRole('alert')).toHaveText('请检查采集范围中的数字。');
    await expect(perWord).toHaveAttribute('aria-invalid', 'true');
    await expect(perWord).toBeFocused();
    expect(submitted).toBe(false);
  });
}
