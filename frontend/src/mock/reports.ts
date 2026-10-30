// 本文件生成接近成品阅读效果的 AI 报告样例；财务、估值和筹码数据均为虚构。
import type { AnalysisKind, ReportContent, Stock, Strategy } from '../types.ts';
import { DATA_DATE, STRATEGIES, distanceToUpper, isWaitingStrategy, selectionReason, strategyMetric } from './fixtures.ts';
import { createChipDistribution } from './chips.ts';

export const REPORT_SAMPLE_VERSION = 3;

/** 市值与滚动归母净利润必须同币种同单位；亏损、零利润或缺失时不显示有效 PE。 */
export function calculatePeTtm(marketCap: number | null, netProfitTtm: number | null): number | null {
    if (marketCap === null || netProfitTtm === null || !Number.isFinite(marketCap) || !Number.isFinite(netProfitTtm) || marketCap <= 0 || netProfitTtm <= 0) return null;
    return marketCap / netProfitTtm;
}

/** 行业情景只用于组织模拟报告正文，不代表已核实的公司经营资料。 */
const INDUSTRY_CONTEXT: Record<string, { business: string; driver: string; risk: string }> = {
    电力设备: { business: '设备、材料与配套服务', driver: '订单交付和产品结构升级共同支撑收入增长', risk: '终端需求变化、产品价格竞争和扩产后的产能利用率' },
    电子: { business: '芯片、零部件及技术服务', driver: '高附加值产品占比提升，对利润增长形成支撑', risk: '下游采购节奏、研发投入回收和库存周转变化' },
    计算机: { business: '软件产品、订阅及企业服务', driver: '订阅续费与企业客户扩展带来持续收入', risk: '续费率、获客成本及新产品商业化进度' },
    汽车: { business: '整车销售及相关服务', driver: '交付规模增长带动收入提升，产品组合改善利润率', risk: '价格竞争、渠道库存和单车盈利能力变化' },
    食品饮料: { business: '品牌产品销售与渠道服务', driver: '产品结构和渠道效率改善推动盈利释放', risk: '终端动销、经销商库存和渠道回款节奏' },
    家用电器: { business: '家电产品及配套服务', driver: '产品升级与渠道调整共同推动利润改善', risk: '原材料成本、海外需求与渠道库存变化' },
    有色金属: { business: '资源产品及加工业务', driver: '产销规模与产品价格共同影响经营表现', risk: '资源价格波动、生产成本和资本开支变化' },
    公用事业: { business: '能源运营与公共服务', driver: '运营资产形成较稳定收入，经营现金流是主要观察指标', risk: '运营成本、资本开支及项目回收周期' },
    医药生物: { business: '医药产品及研发服务', driver: '产品放量带动收入增长，研发投入影响利润释放节奏', risk: '产品销售进度、研发结果及商业化投入' },
    美容护理: { business: '护理产品及相关服务', driver: '产品结构升级与渠道拓展拉动收入', risk: '产品集中度、推广费用和渠道可持续性' },
    非银金融: { business: '金融服务及相关产品', driver: '业务规模与市场活跃度共同影响收入', risk: '市场成交变化、业务费率和收入波动' },
    机械设备: { business: '工程机械、轨交设备及配套服务', driver: '设备交付与服务需求共同影响收入确认', risk: '订单执行、下游投资节奏和应收账款回收' },
};

/** 将同一股票的样例指标固定下来，预览、生成及历史正文保持一致。 */
export function createReportContent(stock: Stock, kind: AnalysisKind, strategy: Strategy = stock.strategies[0]!): ReportContent {
    const seed = [...stock.code].reduce((sum, digit) => sum + Number(digit), 0);
    const base = { sampleVersion: REPORT_SAMPLE_VERSION, title: `${stock.name} · ${kind === 'fundamental' ? '基本面分析' : kind === 'chips' ? '筹码分析' : `${STRATEGIES[strategy].name}买卖条件`}`, dataDate: DATA_DATE, strategy } as const;
    const number = (value: number): string => value.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    const signed = (value: number): string => `${value >= 0 ? '+' : ''}${value.toFixed(1)}%`;

    if (kind === 'fundamental') {
        const revenueGrowth = +(8.6 + seed % 11 * 1.2).toFixed(1);
        const profitGrowth = +(revenueGrowth + (seed % 5 - 1) * 1.6).toFixed(1);
        const roe = +(8 + seed % 9 * 1.1).toFixed(1);
        const cashRatio = +(0.82 + seed % 7 * 0.065).toFixed(2);
        const bank = stock.industry === '银行';
        const modelPe = bank ? 7 + seed % 5 * 0.8 : 18 + seed % 15 * 1.3;
        const margin = 12 + seed % 12 * 0.9;
        const profit = +((stock.totalMarketCap ?? stock.amount * 30) / modelPe).toFixed(2);
        const revenue = +(profit / (margin / 100)).toFixed(2);
        const pe = calculatePeTtm(stock.totalMarketCap, profit);
        const peDescription = pe === null ? '当前无法计算有效的PE（TTM），不能据此作出估值高低判断。' : `PE（TTM）约${pe.toFixed(1)}倍。结合${profitGrowth.toFixed(1)}%的归母净利润增速，估值消化依赖后续增长延续；增长放缓可能增加估值调整压力。`;
        const interestMargin = +(1.55 + seed % 7 * 0.06).toFixed(2);
        const badLoanRatio = +(0.72 + seed % 8 * 0.05).toFixed(2);
        const context = INDUSTRY_CONTEXT[stock.industry] ?? { business: '零售及对公金融服务', driver: '客户经营与业务结构共同影响盈利表现', risk: '净息差变化、资产质量和信用成本' };
        return {
            ...base,
            summary: `${stock.name}营业收入（TTM）${number(revenue)}亿元，归母净利润（TTM）${number(profit)}亿元，同比${signed(profitGrowth)}。${pe === null ? '当前缺少有效的PE（TTM）。' : `PE（TTM）为${pe.toFixed(1)}倍。`}${bank ? '盈利稳定性需要结合净息差与资产质量持续观察。' : cashRatio >= 1 ? '经营现金流能够覆盖净利润，盈利兑现情况较好。' : '经营现金流暂未完全覆盖净利润，回款与营运资金占用仍需跟踪。'}`,
            metrics: [
                { label: '营业收入（TTM）', value: `${number(revenue)}亿元`, detail: '滚动最近四个季度' },
                { label: '归母净利润（TTM）', value: `${number(profit)}亿元`, detail: 'PE的盈利分母' },
                { label: 'PE（TTM）', value: pe === null ? '—' : `${pe.toFixed(1)}倍`, detail: '总市值／归母净利润（TTM）' },
                { label: '总市值', value: stock.totalMarketCap === null ? '—' : `${number(stock.totalMarketCap)}亿元`, detail: `截至${DATA_DATE}` },
                { label: '营收同比', value: signed(revenueGrowth), detail: '近12个月' },
                { label: '归母净利润同比', value: signed(profitGrowth), detail: '近12个月' },
                { label: bank ? '净息差' : '净资产收益率', value: `${bank ? interestMargin.toFixed(2) : roe.toFixed(1)}%`, detail: bank ? '利差水平' : 'ROE' },
                { label: bank ? '不良贷款率' : '现金流／净利润', value: bank ? `${badLoanRatio.toFixed(2)}%` : `${cashRatio.toFixed(2)}倍`, detail: bank ? '资产质量' : '经营现金流覆盖情况' },
            ],
            sections: [
                { title: '业务与经营表现', text: `公司收入由${context.business}构成，${context.driver}。营业收入（TTM）${number(revenue)}亿元，归母净利润（TTM）${number(profit)}亿元。TTM表示最近四个季度的滚动12个月。${profitGrowth > revenueGrowth ? '利润增速高于收入增速，盈利弹性有所体现。' : '利润增长未显著快于收入，需要关注成本和费用端的变化。'}` },
                { title: bank ? '盈利稳定性与资产质量' : '盈利质量与现金流', text: bank ? `净息差为${interestMargin.toFixed(2)}%，不良贷款率为${badLoanRatio.toFixed(2)}%。盈利增长与资产质量应同时评估；若息差继续收窄或信用成本上升，利润增长的持续性需要重新判断。` : `净资产收益率为${roe.toFixed(1)}%，经营现金流／净利润为${cashRatio.toFixed(2)}倍。${cashRatio >= 1 ? '利润已形成相应现金流，经营质量具备一定支撑。后续重点检查这一覆盖水平能否保持。' : '利润尚未完全转化为经营现金流，后续应重点检查应收账款和存货变化，不能仅凭利润增速判断经营质量。'}` },
                { title: '估值与业绩匹配', text: `总市值为${stock.totalMarketCap === null ? '暂无数据' : `${number(stock.totalMarketCap)}亿元`}，归母净利润（TTM）为${number(profit)}亿元。${peDescription}PE采用总市值除以同口径的滚动归母净利润；分母缺失、为零或为负时不显示有效倍数。` },
                { title: '风险与后续观察', text: `重点跟踪${context.risk}。下一次财务更新需要验证收入与利润能否同步增长，以及${bank ? '净息差和资产质量是否保持稳定' : '现金流改善是否跟上利润增长'}；如这些条件转弱，应下调对经营趋势的判断。` },
            ],
            sources: [`近12个月财务快照 · 截至${DATA_DATE}（虚构）`, '公司业务与行业情景（模拟）', '估值与现金流计算样例（模拟）'],
        };
    }

    if (kind === 'chips') {
        const distribution = createChipDistribution(stock.price, seed);
        const { averageCost, profitableRatio, support, resistance } = distribution;
        const [low70, high70] = distribution.cost70;
        const [low90, high90] = distribution.cost90;
        const premium = (stock.price / averageCost - 1) * 100;
        return {
            ...base,
            chipDistribution: distribution,
            summary: `收盘价${stock.price.toFixed(2)}元，较估算平均成本${averageCost.toFixed(2)}元${premium >= 0 ? '高' : '低'}约${Math.abs(premium).toFixed(1)}%。下方${support.lower.toFixed(2)}～${support.upper.toFixed(2)}元为主要筹码支撑参考区，上方${resistance.lower.toFixed(2)}～${resistance.upper.toFixed(2)}元为压力参考区，需要结合后续价格与成交变化验证。`,
            metrics: [
                { label: '估算平均成本', value: `${averageCost.toFixed(2)}元`, detail: '与当前报价同口径' },
                { label: '收盘价及以下筹码', value: `${profitableRatio.toFixed(1)}%`, detail: '估算分布占比' },
                { label: '中央70%成本区间', value: `${low70.toFixed(2)}～${high70.toFixed(2)}`, detail: '单位：元' },
                { label: '当日换手率', value: `${stock.turnover.toFixed(2)}%`, detail: '成交活跃度' },
            ],
            sections: [
                { title: '成本分布与价格位置', text: `中央70%筹码成本位于${low70.toFixed(2)}～${high70.toFixed(2)}元，中央90%位于${low90.toFixed(2)}～${high90.toFixed(2)}元。有${profitableRatio.toFixed(2)}%的估算筹码成本位于收盘价及以下，平均成本为${averageCost.toFixed(2)}元。横向柱形图展示各价格区间的占比，不等同于该价位当日的成交量或真实股东持股数量。` },
                { title: '成交与筹码交换', text: `当日成交额${stock.amount.toFixed(1)}亿元，换手率${stock.turnover.toFixed(2)}%，成交额为此前5日均值的${stock.volumeRatio.toFixed(2)}倍。${stock.volumeRatio >= 1.5 ? '价格变化伴随成交放大，筹码交换较活跃，后续需要观察放量后价格能否保持。' : '成交放大幅度有限，尚不足以仅凭单日量能判断新一轮趋势已经形成。'}` },
                { title: '支撑与压力参考', text: `支撑参考价${support.price.toFixed(2)}元，对应${support.lower.toFixed(2)}～${support.upper.toFixed(2)}元区间，占估算筹码${support.ratio.toFixed(2)}%；压力参考价${resistance.price.toFixed(2)}元，对应${resistance.lower.toFixed(2)}～${resistance.upper.toFixed(2)}元区间，占${resistance.ratio.toFixed(2)}%。两处均取自现价两侧的主要筹码档位，需由后续收盘和成交表现验证。` },
                { title: '风险与判断边界', text: `如果后续放量跌回${support.lower.toFixed(2)}元以下，当前成本支撑的解释需要重新评估。支撑、压力仅为估算成本分布的参考，不能保证价格一定反弹或受阻；也无法据此确认机构、游资或散户的真实持仓。` },
            ],
            sources: [`成本分布样例 · ${DATA_DATE}（虚构）`, '成交额与换手率快照（模拟）', '估算成本区间与价格位置（模拟）'],
        };
    }

    // 买卖条件沿用详情页的规则指标，避免报告与信号快照使用不同参考价。
    const ma20 = stock.price / (1 + stock.ma20Distance / 100);
    const ma60 = stock.price / (1 + stock.ma60Distance / 100);
    const definition = STRATEGIES[strategy];
    // 等待类报告使用同一整理区间，分别说明趋势延续与低位修复的观察条件。
    if (isWaitingStrategy(strategy) && stock.waiting) {
        const { lower, upper, amountRatio, position120 } = stock.waiting;
        const trend = strategy === 'trend_wait';
        return {
            ...base,
            summary: `${stock.name}当前处于${definition.name}的等待阶段，收盘价${stock.price.toFixed(2)}元，仍在${lower.toFixed(2)}～${upper.toFixed(2)}元区间内。${trend ? `MA20约${ma20.toFixed(2)}元，高于MA60约${ma60.toFixed(2)}元，距区间上沿${distanceToUpper(stock).toFixed(1)}%。` : `MA20约${ma20.toFixed(2)}元，低于MA60约${ma60.toFixed(2)}元，当前位于120日区间的${position120.toFixed(0)}%位置。`}后续需要观察区间突破与成交变化。`,
            metrics: [
                { label: '区间下沿P', value: `${lower.toFixed(2)}元`, detail: '入选时固定的失效参考价' },
                { label: '区间上沿B', value: `${upper.toFixed(2)}元`, detail: '入选时固定的突破观察价' },
                { label: '近5日／近20日均额', value: `${(amountRatio * 100).toFixed(0)}%`, detail: '近期成交额比例' },
                { label: definition.metricName, value: strategyMetric(stock, strategy), detail: trend ? '距离上沿的幅度' : '0%为120日低点，100%为高点' },
            ],
            sections: [
                { title: '当前等待形态', text: `${selectionReason(stock, strategy)}区间宽度约${((upper / lower - 1) * 100).toFixed(1)}%，当前收盘仍在上下沿之间。${trend ? '均线关系符合趋势中整理的观察方向，整理期间仍需持续检查MA60是否保持平稳或上行。' : '近期停止创新低，但MA20仍低于MA60，尚不能据此判断中期上升趋势已经建立。'}` },
                { title: '突破与后续观察', text: `本次固定突破观察价为${upper.toFixed(2)}元。后续收盘超过该价位时，结束当前区间内等待，再检查此前20日高点、当日成交额是否达到此前5日均值的1.5倍，以及价格相对MA20的偏离。只有重新满足短线全部条件，才形成短线入选信号。` },
                { title: '失效与名单变化', text: `${definition.invalid}本次区间下沿P固定为${lower.toFixed(2)}元，后续收盘跌破该价位需要重新评估。${trend ? '趋势条件转弱时移出当日趋势埋伏名单；仍符合其他策略的信号单独保留。' : 'MA20升至MA60及以上时，重新检查趋势埋伏条件；不自动认定趋势修复成功。'}复查参考价时应保持相同复权口径。` },
                { title: '主要风险与复查重点', text: `${trend ? '窄幅整理可能向上突破，也可能向下破位，当前的缩量不能单独证明后续方向。' : '历史低位可能持续较长时间，也可能再次下跌，停止创新低只是当前窗口内的观察结果。'}近5日均额为近20日的${(amountRatio * 100).toFixed(0)}%，需要结合后续收盘、成交和经营资料复查。当前样例用于${definition.period}的观察情景演示。` },
            ],
            sources: [`${definition.name}策略信号快照 · ${DATA_DATE}（模拟）`, '固定区间上下沿、均线及成交额比例（虚构）', `策略规则：${definition.subtitle}`],
        };
    }
    const reference = strategy === 'long' ? ma60 : stock.reference;
    const observation = strategy === 'short'
        ? `突破参考价为${reference.toFixed(2)}元。后续若回踩${reference.toFixed(2)}～${(reference * 1.015).toFixed(2)}元附近、成交缩减且收盘守住参考价，可继续观察突破的有效性；若直接上行，则需要结合量能验证，避免仅依据单日突破作判断。`
        : strategy === 'swing'
            ? `MA20约为${ma20.toFixed(2)}元，回调参考低点P为${reference.toFixed(2)}元。后续重点观察${(ma20 * 0.99).toFixed(2)}～${(ma20 * 1.01).toFixed(2)}元附近能否缩量企稳，以及收盘能否继续保持在MA20上方。`
            : `MA60当前约为${ma60.toFixed(2)}元，收盘价高于MA60 ${stock.ma60Distance.toFixed(1)}%。重点观察中期均线持续上行与价格偏离变化；靠近均线时，需要同时复查长期趋势条件和经营表现，不能只根据价格接近均线就认定机会成立。`;
    return {
        ...base,
        summary: `${stock.name}当前符合${definition.name}“${definition.subtitle}”条件。${selectionReason(stock, strategy)}下一步重点观察信号延续与${strategy === 'long' ? '中期均线关系' : `${reference.toFixed(2)}元参考位置`}，观察周期为${definition.period}。`,
        metrics: [
            { label: '收盘价', value: `${stock.price.toFixed(2)}元`, detail: DATA_DATE },
            { label: strategy === 'long' ? '距MA60' : '距MA20', value: `+${(strategy === 'long' ? stock.ma60Distance : stock.ma20Distance).toFixed(1)}%`, detail: definition.subtitle },
            { label: strategy === 'short' ? '突破参考价B' : strategy === 'swing' ? '回调参考低点P' : '当前MA60', value: `${reference.toFixed(2)}元`, detail: strategy === 'long' ? '均线随行情变化' : '入选时的固定参考价' },
            { label: '观察周期', value: definition.period, detail: `${definition.name}策略` },
        ],
        sections: [
            { title: '当前信号', text: `${selectionReason(stock, strategy)}当前收盘价为${stock.price.toFixed(2)}元，MA20约为${ma20.toFixed(2)}元。${strategy === 'short' ? `成交额放大${stock.volumeRatio.toFixed(2)}倍，价格与成交同步变化。` : strategy === 'swing' ? `回调成交额比例为${(stock.pullbackRatio * 100).toFixed(0)}%，此前回调的成交活跃度低于前期。` : `MA60约为${ma60.toFixed(2)}元，中长期均线关系仍是主要观察依据。`}` },
            { title: '后续观察情景', text: observation },
            { title: '失效与退出观察条件', text: `${definition.invalid}${strategy === 'long' ? 'MA60需按后续每日数据更新，不能一直使用本次报告的固定数值。' : `本次参考价为${reference.toFixed(2)}元，后续应按同一价格口径复查。`}` },
            { title: '主要风险', text: `信号可能受到跳空、成交变化或突发消息影响。当前结果用于${definition.period}范围内的持续观察；若价格偏离均线进一步扩大，或原入选条件转弱，应重新评估信号质量。` },
        ],
        sources: [`${definition.name}策略信号快照 · ${DATA_DATE}（模拟）`, '收盘价、成交额与均线指标（模拟）', `策略规则：${definition.subtitle}`],
    };
}
