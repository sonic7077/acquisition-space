"""Import documents as inert data, never execute scripts or instructions."""
import csv
import hashlib
import io
import json
import re
import stat
import zipfile
from pathlib import PurePosixPath
from fastapi import HTTPException
from sqlalchemy import select
from .db import Record, business_today

MAX_FILE = 20 * 1024 * 1024
MAX_BATCH = 80 * 1024 * 1024
MAX_FILES = 200
EXTENSIONS = {'.md', '.txt', '.csv', '.json', '.pdf', '.docx'}


def safe_path(name):
    name = name.replace('\\', '/')
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or ':' in name or not path.name or '\x00' in name:
        raise HTTPException(422, '文件相对路径无效，禁止路径穿越')
    return str(path)


def text_from(name, content):
    ext = PurePosixPath(name).suffix.lower()
    try:
        if ext == '.pdf':
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(content))
            if len(reader.pages) > 500:
                raise ValueError('PDF页数超过500')
            text = '\n'.join(p.extract_text() or '' for p in reader.pages)
        elif ext == '.docx':
            from docx import Document
            with zipfile.ZipFile(io.BytesIO(content)) as z:
                if sum(x.file_size for x in z.infolist()) > MAX_BATCH:
                    raise ValueError('文档解压后过大')
            d = Document(io.BytesIO(content))
            text = '\n'.join([p.text for p in d.paragraphs] + [' | '.join(c.text for c in row.cells) for t in d.tables for row in t.rows])
        else:
            try:
                text = content.decode('utf-8-sig')
            except UnicodeDecodeError:
                text = content.decode('gb18030')
            if ext == '.json':
                text = json.dumps(json.loads(text), ensure_ascii=False, indent=2)
        if len(text) > 2_000_000:
            raise ValueError('文本超过200万字符')
        if not text.strip():
            raise ValueError('未提取到文本，扫描PDF请先OCR')
        return text
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(422, f'无法提取文件：{name}，请检查格式、大小或先转换为文本')


def template_text(path, text):
    return bool(re.search(r'【[^】]*(填写|填入|待补|你的)[^】]*】|\{\{[^}]+\}\}', text)) or PurePosixPath(path).name.lower() in {'readme.md', 'skill.md', '00-先看我.md'} or '模板' in PurePosixPath(path).stem


def expand_uploads(files, ignored=None):
    ignored = ignored if ignored is not None else []
    expanded = []
    total = 0
    for name, raw in files:
        path = safe_path(name)
        if '/' in path and (PurePosixPath(path).suffix.lower() not in EXTENSIONS | {'.zip'} or any(part.startswith('.') for part in PurePosixPath(path).parts)):
            ignored.append({'path': path, 'reason': '非知识文件或隐藏元数据，未导入'})
            continue
        if len(raw) > MAX_FILE:
            raise HTTPException(422, '单文件上限20MB')
        if path.lower().endswith('.zip'):
            try:
                with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                    for info in archive.infolist():
                        if info.is_dir():
                            continue
                        child = safe_path(info.filename)
                        if stat.S_ISLNK(info.external_attr >> 16):
                            raise HTTPException(422, '不接受压缩包中的符号链接')
                        if info.file_size > MAX_FILE or sum(x.file_size for x in archive.infolist()) > MAX_BATCH:
                            raise HTTPException(422, '解压大小超过限制')
                        if len(archive.infolist()) > MAX_FILES:
                            raise HTTPException(422, '每批最多200个文件')
                        if PurePosixPath(child).suffix.lower() not in EXTENSIONS or any(part.startswith('.') or part == '__MACOSX' for part in PurePosixPath(child).parts):
                            ignored.append({'path': child, 'reason': '非知识文件或隐藏元数据，未导入'})
                            continue
                        expanded.append((child, archive.read(info)))
            except zipfile.BadZipFile:
                raise HTTPException(422, 'ZIP文件无效')
        elif PurePosixPath(path).suffix.lower() in EXTENSIONS:
            expanded.append((path, raw))
        else:
            raise HTTPException(422, '支持MD/TXT/CSV/JSON/PDF/DOCX或ZIP，不接受可执行脚本')
        total += len(raw)
        if total > MAX_BATCH or len(expanded) > MAX_FILES:
            raise HTTPException(422, '批次超过80MB或200个文件')
    if not expanded:
        raise HTTPException(422, '没有可导入的知识文件')
    if sum(len(data) for _, data in expanded) > MAX_BATCH:
        raise HTTPException(422, '批次解压后超过80MB')
    result = []
    seen = set()
    for path, raw in expanded:
        digest = hashlib.sha256(raw).hexdigest()
        if (path, digest) in seen:
            continue
        seen.add((path, digest))
        text = text_from(path, raw)
        result.append({'path': path, 'title': PurePosixPath(path).stem, 'text': text, 'hash': digest, 'bytes': len(raw), 'status': 'template' if template_text(path, text) else 'pending'})
    return result


def shared_documents(db):
    return list(db.scalars(select(Record).where(Record.kind == 'knowledge', Record.ip_id.is_(None))))


def verified_document(row):
    if not row or row.kind != 'knowledge' or row.payload.get('status') != 'verified':
        return False
    valid_until = row.payload.get('valid_until')
    return not valid_until or valid_until >= business_today()


def knowledge_terms(text):
    terms = set()
    for word in re.findall(r'[a-z0-9]+|[\u4e00-\u9fff]+', text.lower()):
        if len(word) > 1 and '\u4e00' <= word[0] <= '\u9fff':
            terms.update(word[i:i+2] for i in range(len(word)-1))
        else:
            terms.add(word)
    return terms


def search_documents(documents, topic, limit=8, *, detail='', preferred_groups=(), pinned_ids=(), preferred_ids=()):
    topic_terms = knowledge_terms(topic)
    detail_terms = knowledge_terms(detail[:6000]) - topic_terms
    pinned_order = {key: order for order, key in enumerate(pinned_ids)}
    preferred_ids = set(preferred_ids)
    ranked = []
    for doc in documents:
        if not verified_document(doc):
            continue
        p = doc.payload
        text = p['text']
        heading = knowledge_terms(p['title'] + '\n' + (p.get('category_path') or ''))
        body_terms = knowledge_terms(text)
        all_terms = heading | body_terms
        score = (len(topic_terms & all_terms) * 4 + len(topic_terms & heading) * 3
                 + min(len(detail_terms & all_terms), 12) + min(len(detail_terms & heading), 8))
        if not score and doc.id not in pinned_order:
            continue
        group = (p.get('category_id') or '').partition('-')[0]
        score += 2 * (int(group in preferred_groups) + int(doc.id in preferred_ids))
        lower = text.lower()
        excerpt_terms = (topic_terms & body_terms) or (detail_terms & body_terms)
        positions = [lower.find(term) for term in excerpt_terms]
        start = max(0, min(positions, default=0) - 200)
        order = (pinned_order.get(doc.id, len(pinned_order)), -score, doc.id)
        ranked.append((order, doc, text[start:start + 5000]))
    ranked.sort(key=lambda item: item[0])
    sources, seen = [], set()
    for _, doc, excerpt in ranked:
        p = doc.payload
        identity = (p.get('category_id'), p['path'], p['hash'])
        if identity in seen:
            continue
        seen.add(identity)
        sources.append({'id': doc.id, 'title': p['title'], 'path': p['path'], 'category_id': p.get('category_id'),
                        'category_path': p.get('category_path'), 'excerpt': excerpt, 'hash': p['hash']})
        if len(sources) >= limit:
            break
    return sources
