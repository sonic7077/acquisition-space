import {test,expect} from '@playwright/test';

test('候选池排除已采用内容，按来源博主搜索并保留全部内容',async({page,request})=>{
  await request.post('/api/auth/login',{data:{username:'admin',password:'admin'}});
  const team=await(await request.post('/api/teams',{data:{name:'候选规则验收组'}})).json();
  const ip=await(await request.post('/api/ips',{data:{name:'候选规则IP',team_id:team.id}})).json();
  const author=await(await request.post('/api/records/authors',{data:{ip_id:ip.id,title:'保险候选来源博主',platform:'抖音'}})).json();
  const source={ip_id:ip.id,author_id:author.id,platform:'抖音',likes:1000,comments_count:100,published_at:Math.floor(Date.now()/1000)-3600};
  const pending=await(await request.post('/api/records/materials',{data:{...source,title:'待选保险作品'}})).json();
  const used=await(await request.post('/api/records/materials',{data:{...source,title:'已采用保险作品'}})).json();
  await request.patch('/api/records/materials/'+used.id,{data:{status:'adopted'}});
  await page.goto('/');await page.getByRole('button',{name:'进入工作台'}).click();
  await page.getByLabel('筛选IP').selectOption(ip.id);
  await page.getByRole('button',{name:'选题库',exact:true}).click();
  await page.getByLabel('搜索素材').fill('保险候选来源博主');
  await expect(page.getByText(pending.title,{exact:true})).toBeVisible();
  await expect(page.getByText(used.title,{exact:true})).toBeVisible();
  await page.getByRole('button',{name:'候选池',exact:true}).click();
  await expect(page.getByText(pending.title,{exact:true})).toBeVisible();
  await expect(page.getByText(used.title,{exact:true})).toHaveCount(0);
  await page.getByRole('button',{name:'推荐',exact:true}).click();
  await expect(page.getByText(used.title,{exact:true})).toBeVisible();
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
});
