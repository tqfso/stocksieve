// 本文件统一上市板块的展示名称和所属交易所，供筛选与详情页面共用。
import type { Board, Stock } from './types.ts';

/** 板块标识与正式 API 使用相同枚举，主板按所属交易所区分。 */
export const BOARDS: Record<Board, { name: string; exchange: Stock['exchange'] }> = {
    SSE_MAIN: { name: '沪市主板', exchange: '上海' },
    SZSE_MAIN: { name: '深市主板', exchange: '深圳' },
    CHINEXT: { name: '创业板', exchange: '深圳' },
    STAR: { name: '科创板', exchange: '上海' },
    BSE: { name: '北交所', exchange: '北京' },
};

/** 校验网址中的板块参数，未知标识回退为不限制板块。 */
export function isBoard(value: string): value is Board {
    return Object.hasOwn(BOARDS, value);
}
