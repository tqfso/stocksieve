// 本文件保存服务器会话、行情状态和后台任务列表，页面切换不会中断已受理任务。
import { randomId } from './ids.ts';
import { computed, ref } from 'vue';
import { defineStore } from 'pinia';
import * as api from './api.ts';
import type { MarketStatus, SyncJob } from './api.ts';

export const useSession = defineStore('session', () => {
    const user = ref(api.currentUser());
    const isAdmin = computed(() => user.value?.role === 'admin');
    async function login(username: string, password: string): Promise<void> { user.value = await api.login(username, password); }
    async function restore(): Promise<void> { user.value = await api.restoreSession(); }
    async function logout(): Promise<void> { try { await api.logout(); } finally { user.value = null; } }
    if (typeof window !== 'undefined') window.addEventListener('stocksieve:session-expired', () => { user.value = null; });
    return { user, isAdmin, login, restore, logout };
});

export const useMarket = defineStore('market', () => {
    const status = ref<MarketStatus | null>(null);
    const error = ref('');
    async function refresh(): Promise<void> { try { status.value = await api.marketStatus(); error.value = ''; } catch (cause) { error.value = (cause as Error).message; throw cause; } }
    return { status, error, refresh };
});

export const useSync = defineStore('sync', () => {
    const market = useMarket();
    const jobs = ref<SyncJob[]>([]);
    const error = ref('');
    const loading = ref(false);
    const active = computed(() => jobs.value.filter(job => job.status === 'queued' || job.status === 'running'));
    const busy = computed(() => active.value.some(job => !job.historyBackfill));
    const running = computed(() => active.value.find(job => !job.historyBackfill) ?? active.value[0]);
    const initialized = computed(() => market.status?.initialized ?? false);
    const stockUpdatedAt = computed(() => market.status?.stockUpdatedAt ?? null);
    const catalogUpdatedAt = computed(() => market.status?.catalogUpdatedAt ?? null);
    let timer: ReturnType<typeof setTimeout> | undefined;
    let refreshing: Promise<void> | undefined;
    async function refresh(refreshMarket = true): Promise<void> {
        if (refreshing) return refreshing;
        refreshing = (async () => {
            try {
                const statusRequest = refreshMarket || !market.status ? market.refresh() : undefined;
                const [items] = await Promise.all([api.syncJobs(), statusRequest]);
                const completed = items.some(job => job.status === 'succeeded' && !jobs.value.some(previous => previous.id === job.id && previous.status === 'succeeded'));
                jobs.value = items; error.value = '';
                // 轮询只更新任务行；任务成功后再刷新基础信息和已发布快照。
                if (!statusRequest && completed) await market.refresh();
            }
            catch (cause) { error.value = (cause as Error).message; }
            finally {
                clearTimeout(timer); refreshing = undefined;
                if (api.currentUser()?.role === 'admin' && running.value) timer = setTimeout(() => { void refresh(false); }, 2500);
            }
        })();
        return refreshing;
    }
    async function start(kind: SyncJob['kind'], source: string, dataDate?: string, key: string = randomId()): Promise<SyncJob> {
        loading.value = true;
        try { const job = await api.startSync(kind, source, dataDate, key); await refresh(); return job; }
        finally { loading.value = false; }
    }
    async function retry(id: string, key: string): Promise<void> {
        loading.value = true;
        try { await api.retrySync(id, key); await refresh(); }
        finally { loading.value = false; }
    }
    if (typeof window !== 'undefined') window.addEventListener('stocksieve:session-expired', () => { clearTimeout(timer); jobs.value = []; market.status = null; });
    return { initialized, stockUpdatedAt, catalogUpdatedAt, jobs, running, busy, loading, error, refresh, start, retry };
});
