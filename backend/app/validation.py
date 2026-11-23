"""Validate public document inputs before storing JSON used by rules and UI."""
from fastapi import HTTPException
from .discovery import number, timestamp


TEXT_FIELDS = {'title', 'body', 'reference', 'cover', 'source', 'platform', 'platform_id',
               'author', 'author_id', 'material_id', 'note', 'reason', 'owner', 'reply',
               'status', 'intent', 'content_type', 'fact_status', 'next_followup',
               'sel_topic', 'doc_name', 'product'}
COUNT_FIELDS = {'likes', 'comments_count', 'collects', 'shares', 'plays', 'followers',
                'duration', 'reply_count'}


def text_fields(data):
    for key in TEXT_FIELDS & data.keys():
        if not isinstance(data[key], str):
            raise HTTPException(422, f'{key}必须为文本，不能为null、列表或对象')
    if 'title' in data and not data['title'].strip():
        raise HTTPException(422, '标题不能为空')
    return data


def record_input(data):
    result = dict(text_fields(data))
    # Observations must come from the collector, rather than arbitrary JSON inputs.
    if 'interaction_snapshots' in data:
        raise HTTPException(422, '互动快照由采集任务维护，不允许直接填写')
    for key in COUNT_FIELDS & data.keys():
        if data[key] is not None and number(data[key]) is None:
            raise HTTPException(422, f'{key}必须为有效非负数字或null（未知）')
    for key in ('published_at',):
        if key in data and data[key] is not None:
            parsed = timestamp(data[key])
            if parsed is None or parsed < 0:
                raise HTTPException(422, f'{key}必须为有效时间戳或ISO日期')
            result[key] = parsed
    if 'keywords' in data and (not isinstance(data['keywords'], list) or any(not isinstance(k, str) for k in data['keywords'])):
        raise HTTPException(422, '关键词必须为文本列表')
    return result
