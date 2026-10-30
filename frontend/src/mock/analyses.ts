// 本文件按受理时间恢复模拟分析阶段，生成完整或部分资料报告；不执行真实检索和模型调用。
import type { AnalysisScenario, AnalysisTask, Report, ReportContent } from '../types.ts';

/** 只在本地演示中使用，模拟独立于页面计时器的服务端任务。 */
export const ANALYSIS_DURATION = 12000;
export interface StoredAnalysis extends AnalysisTask {
    userId: string;
    idempotencyKey: string;
    scenario: AnalysisScenario;
    input: ReportContent;
}

/** 时间推进仅结算一次成功结果，刷新、退出和重复读取均不会重复创建报告。 */
export function reconcileAnalyses(tasks: StoredAnalysis[], reports: Report[], now: number): boolean {
    let changed = false;
    for (const task of tasks) {
        if (task.status === 'succeeded' || task.status === 'failed') continue;
        const previous = `${task.status}:${task.stage}:${task.progress}`;
        const elapsed = Math.max(0, now - Date.parse(task.createdAt));
        // 1. 用持久化的受理时间恢复阶段；页面只读取状态，不直接驱动任务。
        task.status = elapsed < 1800 ? 'queued' : 'running';
        task.stage = elapsed < 1800 ? 'queued' : elapsed < 6000 ? 'researching' : elapsed < 9500 ? 'analyzing' : 'writing';
        task.progress = Math.min(95, Math.floor(elapsed / ANALYSIS_DURATION * 100));
        task.startedAt = elapsed < 1800 ? null : new Date(Date.parse(task.createdAt) + 1800).toISOString();
        // 2. 必要资料不足或分析超时时保留旧报告；技术分析不以取得公告为前提。
        if (task.scenario === 'failed' && elapsed >= 6000) {
            task.status = 'failed';
            task.stage = task.kind === 'fundamental' ? 'researching' : 'analyzing';
            task.progress = 49;
            task.finishedAt = new Date(Date.parse(task.createdAt) + 6000).toISOString();
            task.error = task.kind === 'fundamental'
                ? { code: 'ANALYSIS_SOURCES_UNAVAILABLE', message: '模拟必要资料获取失败，本次未生成新报告，已有报告仍可查看。' }
                : { code: 'ANALYSIS_TIMEOUT', message: '模拟分析处理超时，本次未生成新报告，已有报告仍可查看。' };
        }
        // 3. 部分资料缺失仍可形成有限结论；成功结果与该任务一一关联。
        else if (elapsed >= ANALYSIS_DURATION) {
            task.status = 'succeeded';
            task.stage = 'completed';
            task.progress = 100;
            task.finishedAt = new Date(Date.parse(task.createdAt) + ANALYSIS_DURATION).toISOString();
            task.reportId = task.id;
            const content = structuredClone(task.input);
            content.overview ??= { decision: { action: 'wait', label: '先观察', reason: '该历史样例需重新核对。' }, opportunities: [], risks: [] };
            if (task.scenario === 'partial') {
                content.dataQuality = 'partial';
                content.missingInformation = [task.kind === 'fundamental'
                    ? '未取得可核验的股东减持计划及进展资料，无法确认减持风险。'
                    : '公告与新闻的事件风险尚未核验，不影响基于同步行情的技术分析。'];
                if (task.kind === 'fundamental') {
                    content.overview.decision = { action: 'wait', label: '暂缓买入', reason: '股东减持风险尚未核实，先等待资料补齐。' };
                    content.overview.risks[0] = content.missingInformation[0]!;
                    const reduction = content.sections.find((section) => section.title.includes('股东减持'));
                    if (reduction) reduction.text = '未取得可核验的股东减持计划和实施进展，无法确认是否存在尚未结束的减持事项。没有检索到资料不能理解为没有减持风险。已有经营指标仅供有限分析，需要补充公告后复查；该情景仍为本地模拟。';
                }
                content.summary += ' 本次仅基于已取得的资料形成有限结论，需补充下方缺失资料后复查。';
            }
            reports.unshift({ ...content, id: task.id, userId: task.userId, code: task.code, kind: task.kind, createdAt: task.finishedAt });
        }
        changed ||= previous !== `${task.status}:${task.stage}:${task.progress}`;
    }
    return changed;
}

/** 对外隐藏演示场景、用户标识和受理时冻结的报告输入。 */
export function publicAnalysis(task: StoredAnalysis): AnalysisTask {
    const { userId: _user, idempotencyKey: _key, scenario: _scenario, input: _input, ...result } = task;
    return structuredClone(result);
}
