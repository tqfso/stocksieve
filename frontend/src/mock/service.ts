// 本文件集中提供异步模拟业务和浏览器持久化；页面不直接访问存储或网络。
import type { AnalysisKind, Report, Role, Scenario, Stock, StockQuery, SyncJob, User } from '../types.ts';
import { STOCKS } from './fixtures.ts';
import { filterStocks } from './market.ts';
interface Account extends User {
    password: string;
}
interface Database {
    accounts: Account[];
    reports: Report[];
    jobs: SyncJob[];
    initialized: boolean;
}
const DB_KEY = 'stocksieve.mock.v1';
const SESSION_KEY = 'stocksieve.session.v1';
const memory = new Map<string, string>();
let database: Database | undefined;
export let storageAvailable = true;
/** 存储不可用时允许本次会话继续使用，并由界面提示无法跨刷新保留。 */
function read(key: string): string | null {
    if (!storageAvailable)
        return memory.get(key) ?? null;
    try {
        const value = localStorage.getItem(key);
        if (value !== null)
            memory.set(key, value);
        return value;
    }
    catch {
        storageAvailable = false;
        return memory.get(key) ?? null;
    }
}
function write(key: string, value: string): void {
    memory.set(key, value);
    if (!storageAvailable)
        return;
    try {
        localStorage.setItem(key, value);
    }
    catch {
        storageAvailable = false;
    }
}
/** 首次读取演示数据并恢复中断任务，后续复用当前会话内的数据。 */
function db(): Database {
    if (database)
        return database;
    try {
        const parsed = JSON.parse(read(DB_KEY) ?? 'null') as Database | null;
        if (parsed && Array.isArray(parsed.accounts) && Array.isArray(parsed.jobs) && Array.isArray(parsed.reports) && parsed.accounts.some((account) => account.role === 'admin'))
            database = parsed;
    }
    catch { /* 损坏的演示缓存回退为初始数据。 */ }
    database ??= {
        accounts: [
            { id: 'admin', username: 'admin', name: '演示管理员', role: 'admin', password: 'demo1234', createdAt: '2026-10-28 09:00' },
            { id: 'viewer', username: 'viewer', name: '演示用户', role: 'user', password: 'demo1234', createdAt: '2026-10-28 09:00' },
        ], reports: [], jobs: [], initialized: false,
    };
    // 刷新会中断前端计时器，将未完成任务转为可重试状态。
    for (const job of database.jobs)
        if (job.status === 'running') {
            job.status = 'failed';
            job.logs.push('页面刷新中断模拟任务，请重新执行。');
        }
    save();
    return database;
}
function save(): void { if (database)
    write(DB_KEY, JSON.stringify(database)); }
function publicUser(account: Account): User { const { password: _password, ...user } = account; return user; }
export function delay(milliseconds = 450): Promise<void> { return new Promise((resolve) => setTimeout(resolve, milliseconds)); }
export function timeLabel(): string { return new Date().toLocaleString('sv-SE', { timeZone: 'Asia/Shanghai', hour12: false }); }
/** 会话只是模拟角色切换，正式接入时必须由后端鉴权替换。 */
export function currentUser(): User | null {
    const id = read(SESSION_KEY);
    const account = db().accounts.find((item) => item.id === id);
    return account ? publicUser(account) : null;
}
function requireUser(role?: Role): User {
    const user = currentUser();
    if (!user || (role && user.role !== role))
        throw new Error('当前账号没有执行此操作的权限。');
    return user;
}
/** 校验演示账号并保存仅含账号标识的模拟会话。 */
export async function login(username: string, password: string): Promise<User> {
    await delay();
    const account = db().accounts.find((item) => item.username === username.trim() && item.password === password);
    if (!account)
        throw new Error('账号或密码错误，请检查后重试。');
    write(SESSION_KEY, account.id);
    return publicUser(account);
}
export function logout(): void {
    // 主动退出时终止未完成的演示任务，避免留下无法继续的运行状态。
    for (const job of db().jobs)
        if (job.status === 'running') {
            job.status = 'failed';
            job.logs.push('账号退出，模拟任务已中断。');
        }
    save();
    write(SESSION_KEY, '');
}
/** 异步返回筛选结果，按评审开关演示空结果或请求异常。 */
export async function listStocks(query: StockQuery, scenario: Scenario): Promise<Stock[]> {
    requireUser();
    await delay(500);
    if (scenario === 'error')
        throw new Error('模拟行情列表暂时不可用，请重试。');
    if (scenario === 'empty')
        return [];
    return filterStocks(STOCKS, query);
}
export async function getStock(code: string): Promise<Stock> {
    requireUser();
    await delay(300);
    const stock = STOCKS.find((item) => item.code === code);
    if (!stock)
        throw new Error('没有找到这只股票，请从机会列表重新选择。');
    return stock;
}
export function listUsers(): User[] { requireUser('admin'); return db().accounts.map(publicUser); }
/** 创建普通用户，统一校验字段、账号唯一性及管理员角色。 */
export async function addUser(username: string, name: string, password: string): Promise<User> {
    requireUser('admin');
    await delay();
    if (!/^[a-zA-Z][a-zA-Z0-9_]{2,19}$/.test(username))
        throw new Error('账号需为 3～20 位字母、数字或下划线，并以字母开头。');
    if (!name.trim() || name.trim().length > 20)
        throw new Error('请输入 1～20 字的用户名称。');
    if (password.length < 6 || password.length > 32)
        throw new Error('密码需为 6～32 位。');
    if (db().accounts.some((item) => item.username.toLowerCase() === username.toLowerCase()))
        throw new Error('该账号已存在，请使用其他账号。');
    const account: Account = { id: crypto.randomUUID(), username, name: name.trim(), password, role: 'user', createdAt: timeLabel() };
    db().accounts.push(account);
    save();
    return publicUser(account);
}
/** 修改当前管理员的演示密码，成功后使当前模拟会话退出。 */
export async function changePassword(oldPassword: string, newPassword: string): Promise<void> {
    const user = requireUser('admin');
    await delay();
    const account = db().accounts.find((item) => item.id === user.id)!;
    if (account.password !== oldPassword)
        throw new Error('当前密码不正确。');
    if (newPassword.length < 6 || newPassword.length > 32)
        throw new Error('新密码需为 6～32 位。');
    if (newPassword === oldPassword)
        throw new Error('新密码需要与当前密码不同。');
    account.password = newPassword;
    save();
    logout();
}
/** AI 报告是固定结构的示例文本，不访问模型、财报或筹码数据。 */
export async function analyze(stock: Stock, kind: AnalysisKind, fail: boolean): Promise<Report> {
    const user = requireUser();
    await delay(1500);
    if (fail)
        throw new Error('模拟分析服务超时。本次未生成报告，可以重试。');
    const content: Record<AnalysisKind, Pick<Report, 'summary' | 'sections' | 'sources'>> = {
        fundamental: {
            summary: `${stock.name}的基本面报告示例：从行业位置、盈利质量与现金流三个角度组织资料。`,
            sections: [{ title: '行业与业务', text: `示例股票属于${stock.industry}行业，涉及${stock.concepts.join('、')}。正式报告将在发起分析时获取资料并标注出处。` }, { title: '盈利与现金流', text: '此处展示营收、利润及经营现金流的分析结构。当前未获取任何真实财务数值，不对盈利质量作判断。' }, { title: '待核实事项', text: '需进一步核对最新财务报告、业务集中度和行业需求变化，资料不足时应明确标注。' }],
            sources: ['模拟资料：公司业务概览', '模拟资料：财务报告摘要'],
        },
        chips: {
            summary: `${stock.name}的筹码报告示例：展示成本分布、成交活跃度与分析局限。`,
            sections: [{ title: '成交活跃度', text: `示例换手率为 ${stock.turnover.toFixed(2)}%，成交额为 ${stock.amount.toFixed(1)} 亿元，均为虚构数据。` }, { title: '成本分布', text: '成本分布需要依据明确的数据与估算口径。当前仅展示报告结构，不提供筹码集中度或资金流向结论。' }, { title: '观察要点', text: '可以结合量价变化复核趋势；换手率或成交额单一指标不能直接等同于资金意图。' }],
            sources: ['模拟资料：日线成交数据', '模拟资料：成本分布说明'],
        },
        trade: {
            summary: `${stock.name}的买卖条件报告示例：围绕已有规则组织观察条件和失效情形。`,
            sections: [{ title: '观察条件', text: '对照当前策略的全部入选条件，复查价格与均线关系、成交额变化，以及数据是否完整。' }, { title: '失效情形', text: '短线关注跌回突破参考价，波段关注跌破入选时的回调低点，长线关注持续跌破 MA60 或均线关系改变。' }, { title: '使用边界', text: '本报告为交互演示，不包含真实交易建议、预测价格或收益承诺。规则信号与 AI 分析分别展示。' }],
            sources: ['模拟资料：策略信号快照', '模拟资料：日线示例'],
        },
    };
    const report: Report = { id: crypto.randomUUID(), userId: user.id, code: stock.code, kind, createdAt: timeLabel(), ...content[kind] };
    db().reports.unshift(report);
    db().reports = db().reports.slice(0, 100);
    save();
    return report;
}
/** 按当前用户隔离报告历史，避免切换演示账号后混用报告。 */
export function reportsFor(code: string, kind: AnalysisKind): Report[] {
    const user = requireUser();
    return db().reports.filter((item) => item.userId === user.id && item.code === code && item.kind === kind);
}
export function syncSnapshot(): {
    initialized: boolean;
    jobs: SyncJob[];
} {
    requireUser('admin');
    return structuredClone({ initialized: db().initialized, jobs: db().jobs });
}
/** 检查初始化前置条件并创建任务，同时禁止重复启动。 */
export function startSync(kind: SyncJob['kind'], source: SyncJob['source'] = 'Sina'): SyncJob {
    requireUser('admin');
    if (db().jobs.some((job) => job.status === 'running'))
        throw new Error('已有任务正在运行，请等待完成。');
    if (kind === 'daily' && !db().initialized)
        throw new Error('请先初始化股票基础数据。');
    const job: SyncJob = { id: crypto.randomUUID(), source, kind, status: 'running', progress: 0, createdAt: timeLabel(), count: 0, logs: [`模拟任务已创建，选择 ${source} 作为示例数据源。`] };
    db().jobs.unshift(job);
    db().jobs = db().jobs.slice(0, 20);
    save();
    return structuredClone(job);
}
/** 每次推进一段模拟任务，计时器由全局状态管理，离开页面后仍能继续。 */
export function advanceSync(id: string, fail: boolean): void {
    const job = db().jobs.find((item) => item.id === id);
    if (!job || job.status !== 'running')
        return;
    job.progress = Math.min(100, job.progress + 20);
    if (fail && job.progress === 60) {
        job.status = 'failed';
        job.logs.push('模拟数据源响应超时，未写入任何真实行情。请重试。');
    }
    else if (job.progress === 100) {
        job.status = 'success';
        job.count = 5348;
        if (job.kind === 'initialize')
            db().initialized = true;
        job.logs.push('模拟校验完成，示例任务成功。机会列表继续使用固定样例。');
    }
    else
        job.logs.push(`模拟处理进度 ${job.progress}%：${job.progress < 60 ? '读取样例数据' : '校验样例记录'}。`);
    save();
}
/** 仅清理本应用的演示数据与会话，不影响其他站点存储。 */
export function resetDemo(): void {
    database = undefined;
    write(DB_KEY, '');
    write(SESSION_KEY, '');
}
