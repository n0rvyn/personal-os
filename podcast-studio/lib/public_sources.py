"""Exact, hash-pinned public-source scope for the existing opinion runner."""
import hashlib
import json
from pathlib import Path
from datetime import date


class PublicSources:
    def __init__(self, path, expected_sha256=None):
        self.path = Path(path).expanduser().resolve()
        raw = self.path.read_bytes()
        self.digest = hashlib.sha256(raw).hexdigest()
        if expected_sha256 is not None and self.digest != expected_sha256:
            raise ValueError('Public source manifest differs from approved configuration hash')
        self.data = json.loads(raw)
        if self.data.get('version') != 1 or not isinstance(self.data.get('files'), list) or not self.data['files']:
            raise ValueError('Public source manifest requires version 1 and explicit files')
        self.files = {}
        for item in self.data['files']:
            if not isinstance(item.get('title'), str) or not item['title'].strip():
                raise ValueError('Public source requires a nonempty title')
            p = Path(item['path']).expanduser()
            if not p.is_absolute() or p.resolve() != p or p.suffix != '.md' or any(x.startswith('.') for x in p.parts):
                raise ValueError('Public sources must be explicit non-hidden, non-symlink Markdown files')
            if item.get('kind') not in {'news', 'archive'} or not item.get('url', '').startswith('https://'):
                raise ValueError('Public source must identify its original URL and news/archive role')
            date.fromisoformat(item['published'])
            if p in self.files:
                raise ValueError('Duplicate public source path')
            self.files[p] = item
        self.candidates = self.data.get('candidates')
        if not isinstance(self.candidates, list) or not self.candidates:
            raise ValueError('Public source manifest requires news candidates')
        for c in self.candidates:
            source = self.files.get(Path(c['source_path']))
            if not source or source['kind'] != 'news' or c.get('domain') not in {'tech','market','science','geo','culture'} or not c.get('topic_tag'):
                raise ValueError('Every candidate must bind to a selected public news source')
        context = self.data.get('archive_context', {})
        self.context_path = Path(context.get('source_path', ''))
        if self.context_path not in self.files or self.files[self.context_path]['kind'] != 'archive' or not isinstance(context.get('question'), str) or not context['question'].strip():
            raise ValueError('Public archive context and an editorial question are required')
        self.verify()
        context_text = self.read(self.context_path)
        if context_text.startswith('---'):
            parts = context_text.split('---', 2)
            context_text = parts[2] if len(parts) == 3 else ''
        if not context_text.strip():
            raise ValueError('Public archive context requires usable source text')

    def verify(self):
        if hashlib.sha256(self.path.read_bytes()).hexdigest() != self.digest:
            raise ValueError('Public source manifest changed; review before continuing')
        for p, item in self.files.items():
            if p.resolve() != p or not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest() != item.get('sha256'):
                raise ValueError('Selected public source changed; review before continuing')

    def read(self, path):
        p = Path(path)
        self.verify()
        data = p.read_bytes()
        if p.resolve() != p or hashlib.sha256(data).hexdigest() != self.files[p]['sha256']:
            raise ValueError('Selected public source changed during read')
        return data.decode('utf-8')

    def archive_context(self):
        self.verify()
        item = self.files[self.context_path]
        return {'id': str(self.context_path), 'title': item['title'], 'url': item['url'],
                'published': item['published'], 'excerpt': self.read(self.context_path)[:4000],
                'question': self.data['archive_context']['question']}

    def inventory(self):
        return [{'path': str(p), 'url': i['url'], 'published': i['published'], 'kind': i['kind']}
                for p, i in self.files.items()]
