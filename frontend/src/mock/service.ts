// 本文件集中提供异步模拟业务和浏览器持久化；页面不直接访问存储或网络。
import type { AnalysisKind, AnalysisScenario, AnalysisTask, Report, Role, Scenario, Stock, StockQuery, Strategy, SyncJob, User } from '../types.ts';
import { DATA_DATE, STOCKS } from './fixtures.ts';
import { filterStocks } from './market.ts';
import { createReportContent, REPORT_SAMPLE_VERSION } from './reports.ts';
import { publicAnalysis, reconcileAnalyses } from './analyses.ts';
import type { StoredAnalysis } from './analyses.ts';
interface Account extends User {
    password: string;
}
interface Database {
    accounts: Account[];
    reports: Report[];
    analyses: StoredAnalysis[];
    jobs: SyncJob[];
    initialized: boolean;
    /** 两类基础数据分别保留最近成功时间，不随任务历史淘汰或失败而清空。 */
    stockUpdatedAt: string | null;
    catalogUpdatedAt: string | null;
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
    if (database) {
        if (reconcileAnalyses(database.analyses, database.reports, Date.now())) save();
        return database;
    }
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
        ], reports: [], analyses: [], jobs: [], initialized: false, stockUpdatedAt: null, catalogUpdatedAt: null,
    };
    // 兼容拆分前的本地缓存，保留账号、报告及股票初始化状态；分类由独立任务同步。
    database.stockUpdatedAt ??= null;
    database.catalogUpdatedAt ??= null;
    database.analyses ??= [];
    reconcileAnalyses(database.analyses, database.reports, Date.now());
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
/** 受理时固定输入和演示场景；重复提交返回已有任务，模拟前端处理幂等与进行中冲突。 */
export function startAnalysis(stock: Stock, kind: AnalysisKind, options: {
    strategy?: Strategy;
    scenario?: AnalysisScenario;
    idempotencyKey?: string;
} = {}): AnalysisTask {
    const user = requireUser();
    const strategy = kind === 'trade' ? options.strategy ?? stock.strategies[0]! : null;
    if (!STOCKS.some((item) => item.code === stock.code) || (strategy && !stock.strategies.includes(strategy)))
        throw new Error('当前股票或策略不支持指定分析。');
    const idempotencyKey = options.idempotencyKey ?? crypto.randomUUID();
    if (idempotencyKey.length < 8 || idempotencyKey.length > 128)
        throw new Error('提交标识需为 8～128 位。');
    const data = db();
    // 1. 超时重发使用原 key；同 key 改变请求内容时拒绝。
    const replay = data.analyses.find((task) => task.userId === user.id && task.code === stock.code && task.idempotencyKey === idempotencyKey && Date.now() - Date.parse(task.createdAt) < 86400000);
    if (replay) {
        if (replay.kind !== kind || replay.strategy !== strategy) throw new Error('相同提交标识对应的分析内容不同。');
        return publicAnalysis(replay);
    }
    // 2. 不同 key 的重复点击也恢复原任务，避免重复生成。
    const active = data.analyses.find((task) => task.userId === user.id && task.code === stock.code && task.kind === kind && task.strategy === strategy && (task.status === 'queued' || task.status === 'running'));
    if (active) return publicAnalysis(active);
    const task: StoredAnalysis = {
        id: crypto.randomUUID(), userId: user.id, code: stock.code, kind, strategy,
        snapshotId: `snapshot_demo_${DATA_DATE}`, dataDate: DATA_DATE,
        status: 'queued', stage: 'queued', progress: 0, createdAt: new Date().toISOString(),
        startedAt: null, finishedAt: null, error: null, reportId: null,
        idempotencyKey, scenario: options.scenario ?? 'normal', input: createReportContent(stock, kind, strategy ?? stock.strategies[0]!),
    };
    data.analyses.unshift(task);
    save();
    return publicAnalysis(task);
}
/** 读取本人任务并恢复已受理状态，不因页面刷新或账号退出创建新任务。 */
export function analysesFor(code: string, kind: AnalysisKind, strategy?: Strategy): AnalysisTask[] {
    const user = requireUser();
    return db().analyses.filter((task) => task.userId === user.id && task.code === code && task.kind === kind && (kind !== 'trade' || !strategy || task.strategy === strategy)).map(publicAnalysis);
}
export function getAnalysis(id: string): AnalysisTask {
    const user = requireUser();
    const task = db().analyses.find((item) => item.id === id && item.userId === user.id);
    if (!task) throw new Error('分析任务不存在。');
    return publicAnalysis(task);
}
/** 按当前用户隔离报告历史，避免切换演示账号后混用报告。 */
export function reportsFor(code: string, kind: AnalysisKind, strategy?: Strategy): Report[] {
    const user = requireUser();
    return structuredClone(db().reports.filter((item) => item.sampleVersion === REPORT_SAMPLE_VERSION && item.userId === user.id && item.code === code && item.kind === kind && (kind !== 'trade' || !strategy || item.strategy === strategy)));
}
export function syncSnapshot(): {
    initialized: boolean;
    stockUpdatedAt: string | null;
    catalogUpdatedAt: string | null;
    jobs: SyncJob[];
} {
    requireUser('admin');
    const data = db();
    return structuredClone({ initialized: data.initialized, stockUpdatedAt: data.stockUpdatedAt, catalogUpdatedAt: data.catalogUpdatedAt, jobs: data.jobs });
}
/** 检查初始化前置条件并创建任务，同时禁止重复启动。 */
export function startSync(kind: SyncJob['kind'], source?: SyncJob['source']): SyncJob {
    requireUser('admin');
    if (db().jobs.some((job) => job.status === 'running'))
        throw new Error('已有任务正在运行，请等待完成。');
    if (kind !== 'initialize' && !db().initialized)
        throw new Error('请先初始化股票基础数据。');
    const selectedSource = source ?? (kind === 'catalog' ? '分类样例' : 'Sina');
    if ((kind === 'catalog') !== (selectedSource === '分类样例'))
        throw new Error('行业与题材任务请使用分类样例源，股票基础信息和行情任务请选择 Sina 或 Baostock。');
    // 固定各类任务的更新范围，分类与股票基础信息分别维护。
    const scopes: Record<SyncJob['kind'], string> = {
        initialize: '任务范围：仅同步股票代码、名称、交易所、上市板块等基础信息，保留已有行业和题材归属。',
        catalog: '任务范围：仅同步行业、题材字典及股票归属，不刷新股票基础信息或收盘行情。',
        daily: '任务范围：模拟同步收盘行情与策略结果，复用已有分类关系，不请求行业、题材数据源。',
    };
    const job: SyncJob = { id: crypto.randomUUID(), source: selectedSource, kind, status: 'running', progress: 0, createdAt: timeLabel(), count: 0, logs: [`模拟任务已创建，选择 ${selectedSource} 作为示例数据源。`, scopes[kind]] };
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
        job.logs.push('模拟数据源响应超时，保留各类数据最近成功的状态与更新时间，请重试。');
    }
    else if (job.progress === 100) {
        job.status = 'success';
        job.count = 5348;
        if (job.kind === 'initialize') {
            db().initialized = true;
            db().stockUpdatedAt = timeLabel();
        }
        if (job.kind === 'catalog') db().catalogUpdatedAt = timeLabel();
        const results: Record<SyncJob['kind'], string> = {
            initialize: '模拟股票基础信息同步成功，行业和题材归属保持原样。',
            catalog: '模拟行业、题材及股票归属同步成功，股票基础信息和行情保持原样。',
            daily: '模拟行情校验与策略计算完成，复用已有分类；未同步的分类标记为未知。',
        };
        job.logs.push(`${results[job.kind]}机会列表继续使用固定样例。`);
    }
    else {
        // 三类任务分别展示股票基础信息、分类关系和行情策略的处理阶段。
        const stages: Record<SyncJob['kind'], [string, string]> = {
            initialize: ['读取股票代码、名称、交易所与上市板块样例', '校验股票基础信息'],
            catalog: ['读取行业、题材字典及成份股样例', '匹配股票代码并校验分类归属'],
            daily: ['读取收盘行情样例，沿用已保存分类', '校验行情并计算策略样例'],
        };
        const stage = stages[job.kind][job.progress < 60 ? 0 : 1];
        job.logs.push(`模拟处理进度 ${job.progress}%：${stage}。`);
    }
    save();
}
/** 仅清理本应用的演示数据与会话，不影响其他站点存储。 */
export function resetDemo(): void {
    database = undefined;
    write(DB_KEY, '');
    write(SESSION_KEY, '');
}
