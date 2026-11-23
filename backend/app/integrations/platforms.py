"""TikHub platform protocols, verified from official OpenAPI 2026-11-17."""
import html
import re
from . import tikhub
from .common import IntegrationError

PLATFORMS = {'douyin': '抖音', 'xiaohongshu': '小红书', 'wechat_channels': '视频号'}
DEFAULT_PUBLISH_TIMES = {'douyin': '180', 'xiaohongshu': '0', 'wechat_channels': '180'}
XHS_SEARCH = '/api/v1/xiaohongshu/app_v2/search_notes'
XHS_COMMENTS = '/api/v1/xiaohongshu/app_v2/get_note_comments'
WX_SEARCH = '/api/v1/wechat_search/v2/fetch_search_videos'
WX_DETAIL = '/api/v1/wechat_channels/v2/fetch_video_detail'
WX_COMMENTS = '/api/v1/wechat_channels/v2/fetch_video_comments'


def text(value):
    return html.unescape(re.sub(r'<[^>]*>', '', str(value or '')))


def num(value):
    if value is None or value == '':
        return None
    try:
        s=str(value).strip().replace(',', '')
        return max(0, int(float(s[:-1])*10000)) if s.endswith('万') else max(0,int(float(s)))
    except (ValueError,TypeError,OverflowError):
        return None


def timestamp(value):
    n=num(value)
    return n // 1000 if n and n > 10**12 else n


def envelope(data, fields):
    for _ in range(3):
        if not isinstance(data, dict) or data.get('success') is False or data.get('code', 0) not in (0,200):
            raise IntegrationError('平台返回错误或未知响应结构，未自动重试')
        if any(k in data for k in fields):
            return data
        data=data.get('data')
    raise IntegrationError('平台列表响应结构无效，已停止')


def listing(data, key):
    items=data.get(key)
    if not isinstance(items,list):
        raise IntegrationError('平台列表格式无效，已停止')
    return [x for x in items if isinstance(x,dict)]


def search(request, platform, keyword, opts, page, state):
    publish_time = opts['publish_time']
    if publish_time == 'platform_default':
        publish_time = DEFAULT_PUBLISH_TIMES[platform]
    if platform=='douyin':
        data=request(tikhub.SEARCH,body={'keyword':keyword,'cursor':state.get('cursor',0),'search_id':state.get('search_id',''),'backtrace':state.get('backtrace',''),'sort_type':opts['sort_type'],'publish_time':publish_time,'filter_duration':'0','content_type':'1'})
        items=[x['aweme_info'] for x in listing(data,'data') if isinstance(x.get('aweme_info'),dict) and x['aweme_info'].get('aweme_id')]
        nxt={'cursor':data.get('cursor'),'search_id':data.get('search_id') or (data.get('log_pb') or {}).get('search_id') or state.get('search_id',''),'backtrace':data.get('backtrace') or state.get('backtrace','')}
        return items,bool(data.get('has_more')),nxt
    if platform=='xiaohongshu':
        data=request(XHS_SEARCH,keyword=keyword,page=page,sort_type={'0':'general','1':'popularity_descending','2':'time_descending'}[opts['sort_type']],time_filter={'0':'不限','1':'一天内','7':'一周内','180':'半年内'}[publish_time],note_type='不限',**{k:v for k,v in state.items() if k in ('search_id','search_session_id')})
        data=envelope(data,('items','notes'))
        items=listing(data,'items' if 'items' in data else 'notes')
        return items,bool(data.get('has_more',data.get('hasMore',False))),{'search_id':data.get('search_id',state.get('search_id','')),'search_session_id':data.get('search_session_id',state.get('search_session_id','')),'cursor':page+1}
    body={'keyword':keyword,'raw':False,'duration':'all','sort':{'0':'default','1':'hot','2':'latest'}[opts['sort_type']],'publish_time':{'0':'all','1':'day','7':'week','180':'half_year'}[publish_time]}
    if state.get('cursor'):body['cursor']=state['cursor']
    data=request(WX_SEARCH,body=body)
    return listing(data,'items'),bool(data.get('continue_flag')),{'cursor':data.get('cursor')}


def normalize(request, platform, raw):
    if platform=='douyin':
        return tikhub.material(raw),tikhub.author(raw.get('author') or {})
    if platform=='xiaohongshu':
        note=raw.get('note_card') or raw.get('note') or raw
        if not isinstance(note,dict):return None,None
        key=str(note.get('note_id') or raw.get('id') or note.get('id') or '')
        # Ads / suggestion groups are not notes.
        if not key or raw.get('model_type','note') not in ('note','note_card'):return None,None
        user=note.get('user') or note.get('user_info') or {};stats=note.get('interact_info') or note
        author_id=str(user.get('user_id') or user.get('userid') or user.get('id') or '')
        cover=note.get('cover') or {};images=note.get('images_list') or []
        if not cover and images:cover=images[0]
        if not isinstance(cover,dict):cover={}
        a={'platform':'小红书','platform_id':author_id,'title':user.get('nickname') or user.get('nick_name') or '未知作者','body':'','followers':num(user.get('fans')),'avatar':user.get('avatar') or user.get('image') or '', 'source':f'https://www.xiaohongshu.com/user/profile/{author_id}' if author_id else '', 'source_type':'tikhub'}
        title=text(note.get('display_title') or note.get('title') or note.get('desc'))
        duration=note.get('duration') or (note.get('video') or {}).get('duration')
        m={'platform':'小红书','platform_id':key,'title':title or '无标题笔记','body':text(note.get('desc') or title),'author':a['title'],'source':f'https://www.xiaohongshu.com/explore/{key}','duration':num(duration),'cover':cover.get('url_default') or cover.get('url') or '', 'published_at':timestamp(note.get('time') or note.get('create_time')),'likes':num(stats.get('liked_count',stats.get('likes'))),'comments_count':num(stats.get('comment_count',stats.get('comments_count'))),'collects':num(stats.get('collected_count')),'shares':num(stats.get('share_count')),'plays':None,'source_type':'tikhub','content_type':note.get('type','note'),'unavailable':False}
        return m,a
    export=str(raw.get('exportId') or '')
    jump=raw.get('jumpInfo') or {};ext=jump.get('extInfo') or {}
    object_id=str(raw.get('object_id') or '')
    if not export and not object_id:return None,None
    body={'raw':False}
    if object_id:body['object_id']=object_id
    else:body['export_id']=export
    if ext.get('feedNonceId'):body['object_nonce_id']=str(ext['feedNonceId'])
    detail=request(WX_DETAIL,body=body)
    key=str(detail.get('id') or '')
    if not key.isdigit():raise IntegrationError('视频号详情缺少有效作品ID，已停止该作品采集')
    uid=str(detail.get('username') or '')
    a={'platform':'视频号','platform_id':uid,'title':detail.get('nickname') or jump.get('nickName') or '未知作者','body':'','followers':None,'source':'','source_type':'tikhub'}
    media=detail.get('media') or {};media=media if isinstance(media,dict) else {}
    # Do not store encrypted media URLs, tokens or decode keys as source-page URLs.
    m={'platform':'视频号','platform_id':key,'title':text(detail.get('title') or raw.get('title')) or '无标题视频','body':text(detail.get('title') or raw.get('desc') or raw.get('title')),'author':a['title'],'source':raw.get('share_url') or detail.get('share_url') or '', 'duration':num(detail.get('duration')),'cover':media.get('thumb_url') or media.get('cover_url') or '', 'published_at':timestamp(detail.get('create_time')),'likes':num(detail.get('like_count')),'comments_count':num(detail.get('comment_count')),'collects':num(detail.get('fav_count')),'shares':num(detail.get('forward_count')),'plays':num(detail.get('read_count')),'source_type':'tikhub','unavailable':False}
    return m,a


def comments(request, platform, material, state):
    key=material['platform_id']
    if platform=='douyin':
        data=request(tikhub.COMMENTS,aweme_id=key,cursor=state.get('cursor',0),count=20)
        entries=listing(data,'comments'); more=bool(data.get('has_more')); nxt={'cursor':data.get('cursor')}
        mapped=[{'id':str(c.get('cid') or ''),'text':c.get('text') or '','name':(c.get('user') or {}).get('nickname'),'user_id':str((c.get('user') or {}).get('uid') or ''),'likes':tikhub.number(c.get('digg_count')),'time':c.get('create_time')} for c in entries]
    elif platform=='xiaohongshu':
        data=request(XHS_COMMENTS,note_id=key,cursor=state.get('cursor',''),index=state.get('index',0),pageArea=state.get('pageArea','UNFOLDED'),sort_strategy='latest_v2')
        data=envelope(data,('comments',));entries=listing(data,'comments');more=bool(data.get('has_more',data.get('hasMore',False)));nxt={'cursor':data.get('cursor'),'index':data.get('index',0),'pageArea':data.get('pageArea','UNFOLDED')}
        mapped=[{'id':str(c.get('id') or c.get('comment_id') or ''),'text':c.get('content') or '', 'name':(c.get('user_info') or c.get('user') or {}).get('nickname'),'user_id':str((c.get('user_info') or c.get('user') or {}).get('user_id') or ''),'likes':num(c.get('like_count')),'time':timestamp(c.get('create_time'))} for c in entries]
    else:
        data=request(WX_COMMENTS,body={'object_id':key,'raw':False,'last_buffer':state.get('cursor','')})
        entries=listing(data,'comments');more=bool(data.get('down_continue'));nxt={'cursor':data.get('last_buffer')}
        mapped=[{'id':str(c.get('comment_id') or ''),'text':c.get('content') or '','name':c.get('nickname'),'user_id':str(c.get('username') or ''),'likes':num(c.get('like_count')),'time':timestamp(c.get('create_time'))} for c in entries]
    for mapped_comment, raw_comment in zip(mapped, entries):
        parent = raw_comment.get('reply_id') or raw_comment.get('parent_comment_id') or raw_comment.get('root_comment_id')
        if parent is not None and str(parent) not in ('', '0'):
            mapped_comment['parent_comment_id'] = str(parent)
        reply_count = raw_comment.get('reply_comment_total', raw_comment.get('sub_comment_count', raw_comment.get('reply_count')))
        if reply_count is not None:
            mapped_comment['reply_count'] = num(reply_count)
    return mapped,more,nxt
