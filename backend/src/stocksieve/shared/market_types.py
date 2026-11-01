"""本文件定义跨模块共用的市场与板块标识，供领域规则、配置及渠道契约一致引用。"""

BOARDS = {
    'SSE_MAIN': ('沪市主板', 'SSE'), 'SZSE_MAIN': ('深市主板', 'SZSE'),
    'CHINEXT': ('创业板', 'SZSE'), 'STAR': ('科创板', 'SSE'), 'BSE': ('北交所', 'BSE'),
}
