"""本文件将已保存报告导出为Word或PDF，共用正文、指标和引用，不重新调用AI或联网。"""

import re
from functools import lru_cache
from html import escape
from io import BytesIO
from threading import Lock
from typing import Any
from urllib.parse import urlsplit

from stocksieve.shared.paths import BACKEND_ROOT

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


def blocks(report: dict[str, Any]) -> list[tuple[str, str, str | None]]:
    """统一两种格式的阅读顺序，页面与导出使用相同的已保存文字及引用编号。"""
    overview = report['overview']
    result = [('title', report['title'], None),
        ('text', f"{report['stock']['code']} · 行情截至 {report['dataDate']} · 前复权", None),
        ('text', f"资料获取截至 {report['materialsAsOfAt']}；生成于 {report['generatedAt']}", None),
        ('text', '模拟分析：未调用真实模型或联网检索。' if report['simulated'] else '请结合引用来源及资料覆盖范围阅读。', None),
        ('heading', '报告摘要', None),
        ('text', overview['decision']['label'] + '：' + overview['decision']['reason'], None),
        ('text', overview['holding']['label'] + '：' + overview['holding']['reason'], None),
        ('text', report['summary'], None)]
    numbers = {item['id']: index + 1 for index, item in enumerate(report['sources'])}
    for index, section in enumerate(report['sections']):
        result.extend([('heading', f"{index + 1}. {section['title']}", None),
            ('text', section['headline'], None), ('text', section['text'], None)])
        if section['id'] == 'company':
            stock = report['stock']
            from stocksieve.modules.market.domain.models import BOARDS
            board = BOARDS.get(stock.get('board'), ('未知板块', ''))[0]
            industry = (stock.get('industry') or {}).get('name', '未知')
            result.append(('text', f"股票名称：{stock['name']}；代码：{stock['code']}；板块：{board}；行业：{industry}", None))
        if section['id'] == 'company' and report.get('shareholders'):
            shareholders = report['shareholders']
            def display(value: Any) -> str:
                return '待核验' if value is None else str(value)
            result.extend([('text', '股东户数与筹码集中观察', None),
                ('text', f"本期股东户数：{display(shareholders['latestCount'])}；上期股东户数：{display(shareholders['previousCount'])}；变化比例：{display(shareholders['changePercent'])}", None),
                ('text', f"本期统计日：{display(shareholders['asOf'])}；上期统计日：{display(shareholders['previousAsOf'])}；披露日：{display(shareholders['publishedAt'])}", None),
                ('text', '集中趋势：' + shareholders['trendLabel'] + '。' + shareholders['explanation'], None),
                ('text', '股东户数资料来源：' + (safe_url(shareholders['sourceUrl']) or '待接入'), safe_url(shareholders['sourceUrl']))])
        for source_id in section['sourceIds']:
            source = next(item for item in report['sources'] if item['id'] == source_id)
            result.append(('text', f"[{numbers[source_id]}] {source['title']}", safe_url(source['url'])))
    result.append(('heading', '关键指标', None))
    result.extend(('text', f"{item['label']}：{item['value']}（{item['detail']}）", None) for item in report['metrics'])
    result.append(('heading', '引用资料', None))
    if report['researchStatus'] == 'not_searched':
        result.append(('text', '当前未联网检索，暂无联网参考资料；以下仅列实际使用的本地资料。', None))
    for index, source in enumerate(report['sources']):
        url = safe_url(source['url'])
        result.extend([('text', f"[{index + 1}] {source['title']} · {source['publisher']}", url),
            ('text', f"发布：{source['publishedAt'] or '未知'}；获取：{source['fetchedAt']}", None)])
        if url:
            result.append(('text', url, url))
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
    for kind, text, url in blocks(report):
        paragraph = document.add_paragraph(style='Title' if kind == 'title' else 'Heading 1' if kind == 'heading' else 'Normal')
        if url:
            # OOXML关系保存真正可点击的外部引用，正文由XML库转义。
            link = OxmlElement('w:hyperlink')
            link.set(qn('r:id'), paragraph.part.relate_to(url, RT.HYPERLINK, is_external=True))
            run = OxmlElement('w:r')
            properties = OxmlElement('w:rPr')
            color = OxmlElement('w:color'); color.set(qn('w:val'), '2563EB')
            properties.append(color); run.append(properties)
            value = OxmlElement('w:t'); value.text = text
            run.append(value); link.append(run); paragraph._p.append(link)
        else:
            paragraph.add_run(text)
    output = BytesIO(); document.save(output)
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
        story.append(Paragraph(content, styles[kind]))
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
