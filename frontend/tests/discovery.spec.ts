import { test, expect } from '@playwright/test';

async function login(page:any) {
  await page.goto('/');
  await page.getByRole('button',{name:'进入工作台'}).click();
}

test('博主管理、关键词发现与可解释推荐', async ({page,request})=>{
  await request.post('/api/auth/login',{data:{username:'admin',password:'admin'}});
  const team=await(await request.post('/api/teams',{data:{name:'博主规则组'}})).json();
  const ip=await(await request.post('/api/ips',{data:{name:'博主规则IP',team_id:team.id}})).json();
  const a=await(await request.post('/api/records/authors',{data:{ip_id:ip.id,title:'规则测试博主',platform:'抖音',platform_id:'rule-author',followers:100,source:'https://example.com/author'}})).json();
  for(const [n,likes] of [100,100,3000].entries())await request.post('/api/records/materials',{data:{ip_id:ip.id,title:'规则素材'+n,author_id:a.id,platform:'抖音',likes,comments_count:50,published_at:Math.floor(Date.now()/1000)-3600}});
  await login(page);
  await page.getByLabel('筛选IP').selectOption(ip.id);
  await page.getByRole('button',{name:'对标博主',exact:true}).click();
  const row=page.getByRole('row').filter({hasText:'规则测试博主'});
  await expect(row.getByText('平台ID：rule-author')).toBeVisible();
  await row.getByRole('button',{name:'查看 / 编辑'}).click();
  await page.getByLabel('博主分层').selectOption('跨界结构');
  await page.getByLabel('博主备注').fill('人工保存的研究方向');
  await page.getByRole('button',{name:'保存博主',exact:true}).click();
  await expect(row.getByText('跨界结构',{exact:true})).toBeVisible();
  await page.getByRole('button',{name:'关键词发现博主',exact:true}).click();
  await expect(page.getByRole('heading',{name:'按关键词找对标内容'})).toBeVisible();
  await page.getByRole('button',{name:'推荐',exact:true}).click();
  await expect(page.getByText('规则素材2',{exact:true})).toBeVisible();
  await expect(page.getByText('超基线倍数达标',{exact:true})).toBeVisible();
  await page.getByRole('button',{name:'对标博主',exact:true}).click();
  await row.getByRole('button',{name:'查看 / 编辑'}).click();
  await page.getByLabel('这是我的账号').check();
  await page.getByRole('button',{name:'保存博主',exact:true}).click();
  await page.getByRole('button',{name:'选题库',exact:true}).click();
  await page.getByRole('button',{name:'推荐',exact:true}).click();
  await expect(page.getByText('规则素材2',{exact:true})).toHaveCount(0);
  await page.getByRole('button',{name:'全部',exact:true}).click();
  await expect(page.getByText('规则素材2',{exact:true})).toBeVisible();
  await expect(page.getByText('本人账号',{exact:true}).first()).toBeVisible();
  await page.screenshot({path:'/tmp/huoke-discovery-materials.png',fullPage:true});
});

test('管理员阈值设置与帮助说明',async({page})=>{
  await login(page);
  await page.getByRole('button',{name:'系统设置',exact:true}).click();
  await page.getByRole('button',{name:'作品推荐规则',exact:true}).click();
  await page.getByLabel('推荐最低倍数').fill('4');
  await page.getByRole('button',{name:'保存设置',exact:true}).click();
  await expect(page.getByLabel('推荐最低倍数')).toHaveValue('4');
  await page.getByRole('button',{name:'使用说明',exact:true}).click();
  await expect(page.getByRole('heading',{name:'从采集到运营'})).toBeVisible();
  await expect(page.getByText('推荐倍数 ≥ 4',{exact:true})).toBeVisible();
  await expect(page.getByText('本地 → 飞书',{exact:true})).toBeVisible();
  await expect(page.getByText('真实RPA发布尚未接通',{exact:true})).toBeVisible();
  await page.screenshot({path:'/tmp/huoke-discovery-help.png',fullPage:true});
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
});

test('评论保留树、支持排序和有效需求筛选',async({page,request})=>{
  await request.post('/api/auth/login',{data:{username:'admin',password:'admin'}});
  const team=await(await request.post('/api/teams',{data:{name:'评论规则组'}})).json();
  const ip=await(await request.post('/api/ips',{data:{name:'评论规则IP',team_id:team.id}})).json();
  const author=await(await request.post('/api/records/authors',{data:{ip_id:ip.id,title:'评论来源博主',platform:'抖音'}})).json();
  const work=await(await request.post('/api/records/materials',{data:{ip_id:ip.id,title:'评论来源作品',author_id:author.id,platform:'抖音'}})).json();
  for(const [title,body,likes] of [['低赞用户','请问多少钱',1],['高赞用户','哈哈',90]] as const)await request.post('/api/records/leads',{data:{title,body,likes,ip_id:ip.id,material_id:work.id,author_id:author.id,platform:'抖音',source_type:'platform_comment',contactable:false,comment_scope:'first_level',platform_comment_id:title,commenter_id:'reader-'+title}});
  await login(page);
  await page.getByLabel('筛选IP').selectOption(ip.id);
  await page.getByRole('button',{name:'客户线索',exact:true}).click();
  await page.getByRole('button',{name:'展开全部',exact:true}).click();
  await page.getByLabel('评论排序').selectOption('likes');
  await expect(page.locator('.lead-entry').first()).toContainText('高赞用户');
  await expect(page.getByText('类型分布',{exact:true})).toBeVisible();
  await page.getByLabel('仅看有效需求').check();
  await expect(page.locator('.lead-entry')).toHaveCount(1);
  await expect(page.locator('.lead-entry')).toContainText('低赞用户');
  await expect(page.locator('.lead-entry')).toContainText('仅一级评论');
  await expect(page.locator('.lead-entry')).toContainText('reader-低赞用户');
});

test('普通成员可看帮助但不能操作设置',async({page,request})=>{
  await request.post('/api/auth/login',{data:{username:'admin',password:'admin'}});
  const name='help-member-'+Date.now();
  const team=await(await request.post('/api/teams',{data:{name:'帮助测试组'}})).json();
  const user=await request.post('/api/users',{data:{username:name,password:'Member-123456!',name:'帮助成员',role:'member',team_id:team.id}});
  expect(user.ok()).toBeTruthy();
  await page.goto('/');
  await page.getByLabel('用户名').fill(name);
  await page.getByLabel('密码',{exact:true}).fill('Member-123456!');
  await page.getByRole('button',{name:'进入工作台'}).click();
  await page.getByRole('button',{name:'使用说明',exact:true}).click();
  await expect(page.getByRole('heading',{name:'从采集到运营'})).toBeVisible();
  await expect(page.getByRole('button',{name:'系统设置',exact:true})).toHaveCount(0);
  await expect(page.getByRole('button',{name:'打开管理员设置',exact:true})).toHaveCount(0);
});

test('异步加载文体时新建稿件保持原创口播默认值',async({page})=>{
  let release!:()=>void;
  const blocked=new Promise<void>(resolve=>{release=resolve});
  await page.route('**/api/skills',async route=>{await blocked;await route.continue()});
  await login(page);
  await page.getByRole('button',{name:'审稿',exact:true}).click();
  await page.getByRole('button',{name:'导入 / 新建稿件'}).click();
  release();
  await expect(page.getByRole('dialog').locator('select[name=skill]').locator('option')).toHaveCount(8);
  await expect(page.getByRole('dialog').locator('select[name=skill]')).toHaveValue('original-video-writer');
});
