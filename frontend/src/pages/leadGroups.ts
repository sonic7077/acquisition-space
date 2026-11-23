import type { Row } from '../api';
export type LeadWork={key:string;title:string;material?:Row;rows:Row[]};
export type LeadAuthor={key:string;id:string;title:string;platform:string;ipId:string|null;accountId?:string;works:LeadWork[]};
export function groupLeads(rows:Row[],authors:Row[],materials:Row[],q:string,type:string,intent:string,contentType="all"):LeadAuthor[] {
  const authorMap=new Map(authors.map(a=>[a.id,a])),materialMap=new Map(materials.map(m=>[m.id,m]));
  const groups=new Map<string,LeadAuthor>(),query=q.trim().toLowerCase();
  for(const row of rows) {
    const material=materialMap.get(row.material_id),authorId=row.author_id||material?.author_id||'',author=authorMap.get(authorId);
    const platform=row.platform||material?.platform||author?.platform||'平台未记录';
    const ipId=row.ip_id||null,scope=JSON.stringify([ipId,platform]);
    const isComment=row.source_type==='platform_comment';
    const authorKey=authorId?scope+':author:'+authorId:scope+(isComment?':unknown-author':':manual');
    const title=author?.title||(authorId?'来源博主待补充':isComment?'来源博主未记录':'手工登记 / 无来源博主');
    const workTitle=material?.title||row.work_title||(row.material_id?'来源作品待补充':isComment?'来源作品未记录':'未关联作品');
    const search=[row.title,row.body,row.source,row.work_title,row.platform_comment_id,row.commenter_id,row.owner,row.intent,title,workTitle,platform,authorId,author?.platform_id].join(' ').toLowerCase();
    if(query&&!search.includes(query))continue;
    if(type==='research'&&row.contactable!==false||type==='contactable'&&row.contactable===false)continue;
    if(contentType!=='all'&&(row.content_type||'待核实')!==contentType)continue;
    if(intent!=='all'&&row.intent!==intent)continue;
    let group=groups.get(authorKey);
    if(!group){group={key:authorKey,id:authorId,title,platform,ipId,accountId:author?.platform_id?String(author.platform_id):undefined,works:[]};groups.set(authorKey,group);}
    // Missing material records retain their ID so unrelated works are not silently merged.
    const workKey=row.material_id||'unlinked';
    let work=group.works.find(w=>w.key===workKey);
    if(!work){work={key:workKey,title:workTitle,material,rows:[]};group.works.push(work);}
    work.rows.push(row);
  }
  return Array.from(groups.values());
}
