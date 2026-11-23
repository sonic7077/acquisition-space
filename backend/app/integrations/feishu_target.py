"""Resolve user supplied Feishu URLs; Wiki node tokens are not Bitable app tokens."""
import re
from urllib.parse import urlparse
from .common import IntegrationError

WIKI_URL = 'https://wcn8zd3z0yjw.feishu.cn/wiki/Me3owpcvFirsbEkqMc9cZ7PLnAf'
DEFAULT_URL = WIKI_URL


def parse_url(value):
    u=urlparse(value or DEFAULT_URL)
    match=re.fullmatch(r'/(base|wiki|docx)/([a-zA-Z0-9]+)',u.path.rstrip('/'))
    if u.scheme!='https' or u.hostname!='wcn8zd3z0yjw.feishu.cn' or u.username or u.password or u.port or not match:
        raise IntegrationError('请填写你的飞书空间内有效的HTTPS base、wiki或docx链接')
    return {'type': {'base':'bitable','wiki':'wiki','docx':'docx'}[match[1]],'token':match[2], 'host':u.hostname,'url':value or DEFAULT_URL}


def resolve(api):
    target=parse_url(api.cfg.get('url'))
    if target['type']=='wiki':
        node=api.request('GET','/wiki/v2/spaces/get_node',token=target['token']).get('node') or {}
        if not node.get('obj_token') or not node.get('obj_type'):
            raise IntegrationError('飞书知识空间节点缺少对象信息，请核对链接与应用授权')
        target.update(type=node['obj_type'],token=node['obj_token'],space_id=node.get('space_id'),node_token=node.get('node_token') or target['token'],title=node.get('title'))
    return target
