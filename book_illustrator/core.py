from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time
import tempfile
import xml.etree.ElementTree as ET

FB = 'http://www.gribuser.ru/xml/fictionbook/2.0'
XLINK = 'http://www.w3.org/1999/xlink'
ET.register_namespace('', FB)
ET.register_namespace('l', XLINK)


def fingerprint(value):
    if isinstance(value, bytes):
        return hashlib.sha256(value).hexdigest()
    return fingerprint(json.dumps(value, ensure_ascii=False, sort_keys=True).encode())


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=path.parent,
                                     prefix=path.name+'.',suffix='.tmp',delete=False) as handle:
        handle.write(json.dumps(data,ensure_ascii=False,indent=2));temp=Path(handle.name)
    try:
        # Windows may deny replacement while another process briefly reads status.
        for attempt in range(30):
            try:temp.replace(path);break
            except PermissionError:
                if attempt==29:raise
                time.sleep(.02*(attempt+1))
    finally:
        if temp.exists():temp.unlink()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def local_name(element):
    return element.tag.rsplit('}', 1)[-1] if isinstance(element.tag, str) else ''


def node_text(element):
    return ''.join(element.itertext())


def heading_text(element):
    if element is None:
        return ''
    paragraphs = element.findall(f'{{{FB}}}p')
    return ' / '.join(' '.join(node_text(p).split()) for p in paragraphs) if paragraphs else ' '.join(node_text(element).split())


class Book:
    """Keep the original tree; never reconstruct book prose from model output."""
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.raw = self.path.read_bytes()
        if b'<!DOCTYPE' in self.raw.upper() or b'<!ENTITY' in self.raw.upper():
            raise ValueError('DTD/entity declarations are not supported')
        parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True, insert_pis=True))
        self.root = ET.fromstring(self.raw, parser=parser)
        if self.root.tag != f'{{{FB}}}FictionBook':
            raise ValueError('Expected a FictionBook 2.0 document with its namespace')
        self.sha = fingerprint(self.raw)
        self.paragraph_nodes = {}
        self.ids_by_node = {}
        for i, paragraph in enumerate(self.root.iter(f'{{{FB}}}p'), 1):
            pid = f'p{i:06}'
            self.paragraph_nodes[pid] = paragraph
            self.ids_by_node[paragraph] = pid
        self.parents = {child: parent for parent in self.root.iter() for child in parent}
        self.chapters = self._chapters()

    def _chapters(self):
        chapters = []
        for body in self.root.findall(f'{{{FB}}}body'):
            if body.get('name', '').lower() in {'notes', 'comments', 'footnotes'}:
                continue
            for section in body.iter(f'{{{FB}}}section'):
                # Each narrative paragraph belongs to its nearest section, not its ancestors.
                paragraphs = []
                for paragraph in section.iter(f'{{{FB}}}p'):
                    parent = self.parents.get(paragraph)
                    in_heading = False
                    while parent is not None and parent is not section:
                        if local_name(parent) in {'title', 'epigraph', 'annotation'}:
                            in_heading = True
                        if local_name(parent) == 'section':
                            break
                        parent = self.parents.get(parent)
                    if parent is section and not in_heading and node_text(paragraph).strip():
                        paragraphs.append({'id': self.ids_by_node[paragraph],
                                           'text': node_text(paragraph)})
                # Keep short sections in inventory; the CLI makes pilot selection explicit.
                if not paragraphs:
                    continue
                title = section.find(f'{{{FB}}}title')
                lineage = []
                ancestor = self.parents.get(section)
                while ancestor is not None:
                    if local_name(ancestor) == 'section':
                        heading = ancestor.find(f'{{{FB}}}title')
                        if heading is not None:
                            lineage.insert(0, heading_text(heading))
                    ancestor = self.parents.get(ancestor)
                chapters.append({'id': f'ch{len(chapters) + 1:04}',
                                 'title': heading_text(title) or (lineage[-1] if lineage else ''),
                                 'parents': lineage, 'paragraphs': paragraphs,
                                 'characters': sum(len(p['text']) for p in paragraphs)})
        return chapters

    def inventory(self):
        title = self.root.find(f'{{{FB}}}description/{{{FB}}}title-info/{{{FB}}}book-title')
        return {'schema_version': 1, 'source_sha256': self.sha,
                'source_path': str(self.path), 'title': node_text(title) if title is not None else '',
                'chapters': self.chapters}

    def export(self, illustrations, destination, normalize_publisher_headers=False):
        # Each model's book starts from the immutable source, even in one process.
        return Book(self.path)._export(illustrations, destination, normalize_publisher_headers)

    def _export(self, illustrations, destination, normalize_publisher_headers=False):
        destination = Path(destination).resolve()
        if destination == self.path:
            raise ValueError('Refusing to overwrite the source book')
        existing_ids = {e.get('id') for e in self.root.iter() if e.get('id')}
        last_inserted = {}
        for item in illustrations:
            pid = item['after_paragraph_id']
            if pid not in self.paragraph_nodes:
                raise ValueError(f'Unknown paragraph anchor: {pid}')
            image_path = Path(item['path'])
            payload = image_path.read_bytes()
            mime = image_mime(payload)
            image_id = 'bi-' + fingerprint(self.sha.encode('ascii') + payload)[:24]
            if image_id in existing_ids:
                # Only reuse a blob if its decoded bytes really match.
                blob = next((e for e in self.root.findall(f'{{{FB}}}binary')
                             if e.get('id') == image_id), None)
                if blob is None or base64.b64decode(blob.text or '', validate=True) != payload:
                    raise ValueError('Illustration ID collision')
            else:
                blob = ET.SubElement(self.root, f'{{{FB}}}binary',
                                     {'id': image_id, 'content-type': mime})
                blob.text = base64.b64encode(payload).decode('ascii')
                existing_ids.add(image_id)
            paragraph = self.paragraph_nodes[pid]
            block = paragraph
            # Images are legal section blocks, not children of cite/poem/table/title.
            while self.parents.get(block) is not None and local_name(self.parents[block]) != 'section':
                block = self.parents[block]
            section = self.parents.get(block)
            if section is None or local_name(section) != 'section':
                raise ValueError('Anchor has no containing section')
            image = ET.Element(f'{{{FB}}}image', {f'{{{XLINK}}}href': '#' + image_id})
            image.tail = '\n'
            previous = last_inserted.get(block, block)
            index = list(section).index(previous) + 1
            section.insert(index, image)
            last_inserted[block] = image
        destination.parent.mkdir(parents=True, exist_ok=True)
        if normalize_publisher_headers:
            normalize_headers(self.root)
        temp = destination.with_name(destination.name + '.tmp')
        ET.ElementTree(self.root).write(temp, encoding='utf-8', xml_declaration=True)
        try:
            report = verify_export(self.path, temp, len(illustrations), normalize_publisher_headers)
            temp.replace(destination)
        except Exception:
            temp.unlink(missing_ok=True)
            raise
        return report


def image_mime(data):
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if data.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    raise ValueError('Only PNG and JPEG are supported for portable FB2 export')


def _structure(element, generated_ids=frozenset()):
    """Semantic tree signature, ignoring formatting whitespace between block elements."""
    if generated_ids and (local_name(element) in {'image', 'binary'}):
        ident = element.get('id', '') or element.get(f'{{{XLINK}}}href', '').lstrip('#')
        if ident in generated_ids:
            return None
    text = element.text
    tail = element.tail
    if text is not None and not text.strip():
        text = None
    if tail is not None and not tail.strip():
        tail = None
    return (str(element.tag), tuple(sorted(element.attrib.items())), text, tail,
            tuple(x for child in element if (x := _structure(child, generated_ids)) is not None))


def normalize_headers(root):
    changed = {'publisher_headers': 0, 'publisher_footers': 0, 'body_titles_wrapped': 0}
    for body in root.findall(f'{{{FB}}}body'):
        seen_section = False
        for child in list(body):
            if child.tag in {f'{{{FB}}}myheader', f'{{{FB}}}myfooter'}:
                key = 'publisher_headers' if local_name(child) == 'myheader' else 'publisher_footers'
                child.tag = f'{{{FB}}}section'
                changed[key] += 1
            if child.tag == f'{{{FB}}}title' and seen_section:
                index = list(body).index(child)
                body.remove(child)
                wrapper = ET.Element(f'{{{FB}}}section')
                wrapper.append(child)
                ET.SubElement(wrapper, f'{{{FB}}}empty-line')
                body.insert(index, wrapper)
                changed['body_titles_wrapped'] += 1
            if child.tag == f'{{{FB}}}section':
                seen_section = True
    return changed


def verify_export(source_path, output_path, expected_added=None, normalize_publisher_headers=False):
    original = Book(source_path)
    result = Book(output_path)
    normalized = normalize_headers(original.root) if normalize_publisher_headers else {}
    if [node_text(p) for p in original.paragraph_nodes.values()] != [node_text(p) for p in result.paragraph_nodes.values()]:
        raise ValueError('Export changed original paragraph text or order')
    original_ids = {e.get('id') for e in original.root.iter() if e.get('id')}
    generated_ids = {e.get('id') for e in result.root.findall(f'{{{FB}}}binary')
                     if e.get('id', '').startswith('bi-') and e.get('id') not in original_ids}
    if _structure(original.root) != _structure(result.root, generated_ids):
        raise ValueError('Export changed original text, markup, metadata or links')
    ids = [e.get('id') for e in result.root.iter() if e.get('id')]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate XML IDs')
    binaries = {e.get('id'): e for e in result.root.findall(f'{{{FB}}}binary')}
    for image in result.root.iter(f'{{{FB}}}image'):
        href = image.get(f'{{{XLINK}}}href', '')
        if not href.startswith('#') or href[1:] not in binaries:
            raise ValueError(f'Unresolved embedded image: {href}')
        blob = binaries[href[1:]]
        decoded = base64.b64decode(''.join((blob.text or '').split()), validate=True)
        if href[1:] in generated_ids and blob.get('content-type') != image_mime(decoded):
            raise ValueError('Image MIME mismatch')
    added = sum(1 for image in result.root.iter(f'{{{FB}}}image')
                if image.get(f'{{{XLINK}}}href', '').lstrip('#') in generated_ids)
    if expected_added is not None and added != expected_added:
        raise ValueError('Wrong number of inserted illustrations')
    return {'original_structure_preserved': not any(normalized.values()),
            'normalized_source_structure_preserved': True,
            'text_and_original_links_preserved': True, 'added_illustrations': added,
            'declared_markup_normalization': normalized,
            'source_sha256': original.sha, 'output_sha256': result.sha,
            'validation': 'XML + semantic preservation + embedded-image integrity; not XSD validation'}


class Store:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.directory / 'jobs.sqlite', timeout=30)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('CREATE TABLE IF NOT EXISTS jobs '
                        '(key TEXT PRIMARY KEY, stage TEXT, state TEXT, artifact TEXT, '
                        'error TEXT, updated REAL)')
        self.db.commit()

    def run(self, stage, inputs, operation):
        key = fingerprint({'stage': stage, 'inputs': inputs})
        artifact = self.directory / 'artifacts' / stage / (key + '.json')
        row = self.db.execute('SELECT state, artifact FROM jobs WHERE key=?', (key,)).fetchone()
        if row and row[0] == 'done' and artifact.exists():
            return read_json(artifact)
        self.db.execute('INSERT OR REPLACE INTO jobs VALUES (?,?,?,?,?,?)',
                        (key, stage, 'running', str(artifact), None, time.time()))
        self.db.commit()
        try:
            data = operation()
            write_json(artifact, data)
            self.db.execute('UPDATE jobs SET state=?, updated=? WHERE key=?', ('done', time.time(), key))
            self.db.commit()
            return data
        except Exception as error:
            self.db.execute('UPDATE jobs SET state=?,error=?,updated=? WHERE key=?',
                            ('failed', str(error)[:1000], time.time(), key))
            self.db.commit()
            raise

    def status(self):
        return [dict(zip(('stage', 'state', 'count'), row)) for row in
                self.db.execute('SELECT stage,state,COUNT(*) FROM jobs GROUP BY stage,state')]

    def close(self):
        self.db.close()
