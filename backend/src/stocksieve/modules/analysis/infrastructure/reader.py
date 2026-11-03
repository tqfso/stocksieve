"""本文件提取HTML和PDF原文，保留页码与披露时间，不执行网页或文档中的代码。"""

import re
from datetime import datetime
from html.parser import HTMLParser
from io import BytesIO
from typing import Any

from pypdf import PdfReader

from stocksieve.shared.errors import MissingData


class PageText(HTMLParser):
    """抽取可见文本、标题及明确的发布时间元数据。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title: list[str] = []
        self.skip = 0
        self.in_title = False
        self.published: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {'script', 'style', 'noscript', 'svg'}:
            self.skip += 1
        if tag == 'title':
            self.in_title = True
        values = dict(attrs)
        if tag == 'meta' and (values.get('property') or values.get('name')) in {'article:published_time', 'publishdate', 'pubdate'}:
            self.published = values.get('content')

    def handle_endtag(self, tag: str) -> None:
        if tag in {'script', 'style', 'noscript', 'svg'}:
            self.skip = max(0, self.skip - 1)
        if tag == 'title':
            self.in_title = False

    def handle_data(self, data: str) -> None:
        if not self.skip and data.strip():
            self.parts.append(data.strip())
            if self.in_title:
                self.title.append(data.strip())


class DocumentReader:
    """一次提取最多160页和30万字符；扫描件明确提示无可提取文本。"""

    def parse(self, raw: bytes) -> dict[str, Any]:
        if raw.lstrip().startswith(b'%PDF'):
            try:
                reader = PdfReader(BytesIO(raw), strict=False)
                if reader.is_encrypted and not reader.decrypt(''):
                    raise MissingData('PDF已加密，无法读取。')
                pages, remaining = [], 300000
                for index, page in enumerate(reader.pages[:160]):
                    content = (page.extract_text() or '')[:remaining]
                    pages.append({'page': index + 1, 'text': content})
                    remaining -= len(content)
                    if remaining <= 0:
                        break
                title = str((reader.metadata or {}).get('/Title') or 'PDF公告原文')[:200]
                result = {'format': 'pdf', 'title': title, 'pages': pages, 'pageCount': len(reader.pages),
                          'truncated': len(pages) < len(reader.pages), 'publishedAt': None}
            except MissingData:
                raise
            except Exception as error:
                raise MissingData('PDF解析失败，未取得可核验正文。') from error
        else:
            encoding = re.search(br'charset\s*=\s*["\x27]?([a-zA-Z0-9_-]+)', raw[:4096])
            codec = encoding.group(1).decode() if encoding else 'utf-8'
            try:
                text = raw.decode(codec, errors='replace')
            except LookupError:
                text = raw.decode('utf-8', errors='replace')
            parser = PageText()
            parser.feed(text)
            content = '\n'.join(parser.parts)
            result = {'format': 'html', 'title': ''.join(parser.title)[:200],
                      'pages': [{'page': 1, 'text': content[:300000]}], 'pageCount': 1,
                      'truncated': len(content) > 300000, 'publishedAt': parser.published}
        result['text'] = '\n'.join(page['text'] for page in result['pages'])
        if not result['text'].strip():
            raise MissingData('原文未提取到文字；扫描件需人工核验，当前不推测图片内容。')
        # 元数据无法解析时保持未知，禁止把获取时间当作发布时间。
        if result['publishedAt']:
            try:
                stamp = datetime.fromisoformat(result['publishedAt'])
                result['publishedAt'] = stamp.isoformat() if stamp.tzinfo else None
            except ValueError:
                result['publishedAt'] = None
        return result
