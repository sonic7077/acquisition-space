"""Resolve user supplied Feishu URLs; Wiki node tokens are not Bitable app tokens."""
import os
import re
from urllib.parse import urlparse

from .common import IntegrationError

_HOST_LABEL_RE = re.compile(r'^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$')


def default_url() -> str:
    return os.environ.get('FEISHU_DEFAULT_URL', '').strip()


def allowed_hosts() -> set[str]:
    raw = os.environ.get('FEISHU_ALLOWED_HOSTS', '')
    return {h.strip().lower() for h in raw.split(',') if h.strip()}


def _is_valid_feishu_host(hostname: str) -> bool:
    host = (hostname or '').strip().lower()
    if not host:
        return False
    labels = host.split('.')
    if any(not _HOST_LABEL_RE.fullmatch(label) for label in labels):
        return False
    explicit = allowed_hosts()
    if explicit:
        return host in explicit
    return (
        host in ('feishu.cn', 'larkoffice.com')
        or host.endswith('.feishu.cn')
        or host.endswith('.larkoffice.com')
    )


def parse_url(value):
    raw = (value or '').strip() or default_url()
    if not raw:
        raise IntegrationError('请先在系统设置或 FEISHU_DEFAULT_URL 中配置飞书链接')
    u = urlparse(raw)
    match = re.fullmatch(r'/(base|wiki|docx)/([a-zA-Z0-9]+)', u.path.rstrip('/'))
    if (
        u.scheme != 'https'
        or not _is_valid_feishu_host(u.hostname or '')
        or u.username
        or u.password
        or u.port
        or not match
    ):
        raise IntegrationError('请填写有效的飞书 HTTPS base、wiki 或 docx 链接')
    return {
        'type': {'base': 'bitable', 'wiki': 'wiki', 'docx': 'docx'}[match[1]],
        'token': match[2],
        'host': u.hostname,
        'url': raw,
    }


def resolve(api):
    target = parse_url(api.cfg.get('url'))
    if target['type'] == 'wiki':
        node = api.request('GET', '/wiki/v2/spaces/get_node', token=target['token']).get('node') or {}
        if not node.get('obj_token') or not node.get('obj_type'):
            raise IntegrationError('飞书知识空间节点缺少对象信息，请核对链接与应用授权')
        target.update(
            type=node['obj_type'],
            token=node['obj_token'],
            space_id=node.get('space_id'),
            node_token=node.get('node_token') or target['token'],
            title=node.get('title'),
        )
    return target
