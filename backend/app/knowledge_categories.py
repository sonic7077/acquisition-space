"""Knowledge filing taxonomy derived from the user's screenshots and source scaffold."""
from pathlib import PurePosixPath
from fastapi import HTTPException


def node(key,name,children=None):
    return {'id':key,'name':name,'children':children or []}


TREE=[
    node('recordings','0-录音处理',[node('recordings-pending','待处理'),node('recordings-processed','已处理')]),
    node('owner','1-老板说明书',[node('owner-manual','老板说明书')]),
    node('products','2-产品库',[
        node('product-savings','储蓄险'),node('product-medical','带病可投的医疗险'),
        node('product-tax-refund','个人所得税退税'),node('product-pension','个人养老金'),
        node('product-tax-health','税优健康险'),node('product-critical-illness','重疾险')]),
    node('customer-needs','3-客户需求库',[
        node('needs-faq','常问问题'),node('needs-feedback','成交反馈'),
        node('needs-quotes','客户原话集'),node('needs-lost','未成交记录')]),
    node('cases','4-素材案例库',[
        node('cases-pitfalls','踩坑经验'),node('cases-customers','客户案例'),
        node('cases-stories','我的故事'),node('cases-quotes','行业金句')]),
    node('methods','5-方法论库',[
        node('methods-service','服务交付'),node('methods-content','内容创作'),
        node('methods-sales','营销与销售'),node('methods-judgment','专业判断')]),
    node('production','6-内容生产',[
        node('production-finished','成稿'),node('production-topics','选题'),node('production-notes','随手记')]),
]


def flatten(nodes=TREE,parent=''):
    result=[]
    for n in nodes:
        path=parent+'/'+n['name'] if parent else n['name']
        result.append({**n,'path':path,'leaf':not n['children']})
        result.extend(flatten(n['children'],path))
    return result


CATEGORIES={n['id']:n for n in flatten()}


def leaf(key):
    category=CATEGORIES.get(key) if isinstance(key,str) else None
    if not category or not category['leaf']:
        raise HTTPException(422,'请指定有效的末级知识目录，主目录不可作为导入位置')
    return {'category_id':category['id'],'category_path':category['path']}


def infer(path):
    parts=list(PurePosixPath(str(path).replace('\\','/')).parts)
    if parts:parts[-1]=PurePosixPath(parts[-1]).stem
    matches=[]
    for c in CATEGORIES.values():
        if not c['leaf']:continue
        wanted=c['path'].split('/')
        # Require contiguous root and leaf names; never infer from a topic word alone.
        if any(parts[i:i+len(wanted)]==wanted for i in range(len(parts))):matches.append(c)
    return leaf(matches[0]['id']) if len(matches)==1 else {'category_id':None,'category_path':None}


def migrate(db, Record):
    from sqlalchemy import select, update
    # Keep document and batch IDs so existing draft and import references remain valid.
    db.execute(update(Record).where(
        Record.kind.in_(('knowledge', 'import_batch')), Record.ip_id.is_not(None),
    ).values(ip_id=None))
    for row in db.scalars(select(Record).where(Record.kind == 'knowledge')):
        if 'category_id' not in row.payload:
            row.payload = {**row.payload, **infer(row.payload.get('path', ''))}
