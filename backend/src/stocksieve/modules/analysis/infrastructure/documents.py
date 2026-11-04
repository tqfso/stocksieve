"""本文件将已保存报告导出为Word或PDF，共用正文、指标和引用，不重新调用AI或联网。"""

import re
from datetime import datetime
from functools import lru_cache
from html import escape
from io import BytesIO
from threading import Lock
from typing import Any
from urllib.parse import urlsplit

from stocksieve.shared.paths import BACKEND_ROOT
from stocksieve.shared.time import SHANGHAI

FONT_LOCK = Lock()


def safe_url(value: str | None) -> str | None:
    """文件只嵌入无凭据的HTTP(S)原文链接，不执行报告中的内容。"""
    try:
        parsed = urlsplit(value or '')
        if parsed.scheme in {'http', 'https'} and parsed.hostname and not parsed.username and not parsed.password:
            return value
    except ValueError:
        pass
    return None


def display_time(value: str | None, *, day_only: bool = False) -> str:
    """文件统一使用北京时间，省略微秒和程序时间格式。"""
    if not value:
        return '未知'
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo:
        stamp = stamp.astimezone(SHANGHAI)
    return stamp.strftime('%Y-%m-%d' if day_only else '%Y-%m-%d %H:%M')


def blocks(report: dict[str, Any]) -> list[tuple[str, str, str | None]]:
    """共用精简阅读顺序；旧报告保留原文，新报告不重复铺列未知指标。"""
    overview = report['overview']
    result = [('title', report['title'], None),
        ('text', f"{report['stock']['code']} · 行情截至 {report['dataDate']} · 前复权", None),
        ('text', f"资料截至 {display_time(report['materialsAsOfAt'])}；生成于 {display_time(report['generatedAt'])}（北京时间）", None)]
    if report['simulated']:
        result.append(('text', '模拟分析：未调用真实模型或联网检索。', None))
    result.extend([('heading', '报告摘要', None),
        ('text', overview['decision']['label'] + '：' + overview['decision']['reason'], None),
        ('text', report['summary'], None),
        ('text', overview['holding']['label'].rstrip('。；;') + '：' + overview['holding']['reason'], None)])
    if overview.get('watch'):
        result.append(('text', '接下来关注：' + '；'.join(item.rstrip('。；;') for item in overview['watch']) + '。', None))
    if report.get('criticalNotes'):
        result.append(('text', '尚需核实：' + '；'.join(report['criticalNotes']), None))
    numbers = {item['id']: index + 1 for index, item in enumerate(report['sources'])}
    for index, section in enumerate(report['sections']):
        title = re.sub(r'^(?:[一二三四五六七八九十]+|[0-9]+)[、.．]\s*', '', section['title'])
        result.extend([('heading', f"{index + 1}. {title}", None),
            ('text', section['headline'], None), ('text', section['text'], None)])
        if section['id'] == 'company':
            stock = report['stock']
            industry = (stock.get('industry') or {}).get('name', '未知')
            result.append(('text', f"{stock['name']} · {stock['code']} · {industry}", None))
            shareholders = report.get('shareholders') or {}
            if shareholders.get('latestCount') is not None:
                text = f"本期股东户数：{shareholders['latestCount']:,}户（{shareholders['asOf']}，{'A股' if shareholders.get('basis') == 'A' else '普通股'}）"
                if shareholders.get('previousCount') is not None:
                    text += f"；对比期：{shareholders['previousCount']:,}户（{shareholders['previousAsOf']}）；变化{shareholders['changePercent']:.2f}%"
                result.extend([('text', text, safe_url(shareholders.get('sourceUrl'))),
                               ('text', shareholders['explanation'], None)])
        if section['sourceIds']:
            result.append(('references', '依据：' + ' '.join(f'[{numbers[key]}]' for key in section['sourceIds']), None))
    result.append(('heading', '关键指标', None))
    result.extend(('text', f"{item['label']}：{item['value']}（{item['detail']}）", None) for item in report['metrics'])
    if report.get('coverageNotes'):
        result.append(('heading', '资料范围', None))
        result.extend(('text', item, None) for item in report['coverageNotes'])
    result.append(('heading', '引用资料', None))
    if report['researchStatus'] == 'not_searched':
        result.append(('text', '历史报告未联网检索，仅引用本地资料。', None))
    elif report['researchStatus'] == 'unavailable':
        result.append(('text', '本次引用已有行情和已同步资料；公司公告尚未补齐。', None))
    for index, source in enumerate(report['sources']):
        details = []
        if source['publishedAt']:
            details.append('披露' + display_time(source['publishedAt'], day_only=True))
        if source.get('pages'):
            details.append('原文第' + '、'.join(str(page) for page in source['pages']) + '页')
        text = f"[{index + 1}] {source['title']}" + ('（' + '；'.join(details) + '）' if details else '')
        result.append(('text', text, safe_url(source['url'])))
    return result


def word_document(report: dict[str, Any]) -> bytes:
    from docx import Document
    from docx.opc.constants import RELATIONSHIP_TYPE as RT
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Pt

    document = Document()
    document.core_properties.title = report['title']
    for name in ('Normal', 'Title', 'Heading 1'):
        style = document.styles[name]
        style.font.name = 'Microsoft YaHei'
        style.element.get_or_add_rPr().get_or_add_rFonts().set(qn('w:eastAsia'), 'Microsoft YaHei')
    document.styles['Normal'].font.size = Pt(10.5)

    def link_text(paragraph: Any, text: str, url: str | None) -> None:
        """在同一段落内保留可点击引用，避免每条来源重复占据一段。"""
        if not url:
            paragraph.add_run(text)
            return
        link = OxmlElement('w:hyperlink')
        link.set(qn('r:id'), paragraph.part.relate_to(url, RT.HYPERLINK, is_external=True))
        run = OxmlElement('w:r')
        properties = OxmlElement('w:rPr')
        color = OxmlElement('w:color')
        color.set(qn('w:val'), '2563EB')
        properties.append(color)
        run.append(properties)
        value = OxmlElement('w:t')
        value.text = text
        run.append(value)
        link.append(run)
        paragraph._p.append(link)

    for kind, text, url in blocks(report):
        paragraph = document.add_paragraph(style='Title' if kind == 'title' else 'Heading 1' if kind == 'heading' else 'Normal')
        if kind == 'references':
            paragraph.add_run('依据：')
            for value in re.findall(r'\[(\d+)\]', text):
                link_text(paragraph, f'[{value}] ', safe_url(report['sources'][int(value) - 1]['url']))
        else:
            link_text(paragraph, text, url)
    output = BytesIO()
    document.save(output)
    return output.getvalue()


@lru_cache(maxsize=1)
def pdf_font() -> str:
    """内嵌可再分发的中文字体，服务器和阅读器均不依赖系统中文字体。"""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    with FONT_LOCK:
        if 'StockSieveCJK' not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont('StockSieveCJK', str(BACKEND_ROOT / 'assets/fonts/DroidSansFallbackFull.ttf')))
            pdfmetrics.registerFont(TTFont('StockSieveLatin', str(BACKEND_ROOT / 'assets/fonts/DejaVuSans.ttf')))
    return 'StockSieveCJK'


def pdf_document(report: dict[str, Any]) -> bytes:
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    font = pdf_font()
    styles = {name: ParagraphStyle(name, fontName=font, fontSize=size, leading=size * 1.7,
        wordWrap='CJK', alignment=TA_LEFT, spaceAfter=9, keepWithNext=name != 'text')
        for name, size in [('title', 20), ('heading', 13), ('text', 10)]}
    story = []
    for kind, text, url in blocks(report):
        # CJK回退字体不含完整西文，数字、日期及PE/MA等西文使用独立内嵌字体。
        content = ''.join('<font name="StockSieveLatin">' + escape(part).replace('\n', '<br/>') + '</font>'
            if re.fullmatch(r'[\x00-\u2e7f]+', part) else escape(part)
            for part in re.split(r'([\x00-\u2e7f]+)', text) if part)
        if url:
            content = f'<a href="{escape(url, quote=True)}" color="#2563eb">{content}</a>'
        if kind == 'references':
            references = []
            for number in re.findall(r'\[(\d+)\]', text):
                target = safe_url(report['sources'][int(number) - 1]['url'])
                label = f'<font name="StockSieveLatin">[{number}]</font>'
                references.append(f'<a href="{escape(target, quote=True)}" color="#2563eb">{label}</a>' if target else label)
            content = '依据：' + ' '.join(references)
        story.append(Paragraph(content, styles['text' if kind == 'references' else kind]))
        if kind == 'title':
            story.append(Spacer(1, 8))
    output = BytesIO()
    document = SimpleDocTemplate(output, pagesize=A4, rightMargin=44, leftMargin=44,
        topMargin=42, bottomMargin=42, title=report['title'], author='StockSieve')
    def footer(canvas: Any, doc: Any) -> None:
        canvas.setFont('StockSieveLatin', 8)
        canvas.drawRightString(A4[0] - 44, 22, f'StockSieve · {doc.page}')
    document.build(story, onFirstPage=footer, onLaterPages=footer)
    return output.getvalue()


def export_document(report: dict[str, Any], format: str) -> bytes:
    return word_document(report) if format == 'docx' else pdf_document(report)
