// 本文件管理会话及评审场景，并保持模拟同步任务在页面切换期间持续运行。
import { computed, ref } from 'vue';
import { defineStore } from 'pinia';
import * as api from './mock/service.ts';
import type { Scenario, SyncJob } from './types.ts';
export const useSession = defineStore('session', () => {
    const user = ref(api.currentUser());
    const isAdmin = computed(() => user.value?.role === 'admin');
    async function login(username: string, password: string): Promise<void> { user.value = await api.login(username, password); }
    function logout(): void { api.logout(); user.value = null; }
    return { user, isAdmin, login, logout };
});
export const useReview = defineStore('review', () => {
    const listScenario = ref<Scenario>('normal');
    const aiFail = ref(false);
    const syncFail = ref(false);
    return { listScenario, aiFail, syncFail };
});
export const useSync = defineStore('sync', () => {
    const initialized = ref(false);
    const stockUpdatedAt = ref<string | null>(null);
    const catalogUpdatedAt = ref<string | null>(null);
    const jobs = ref<SyncJob[]>([]);
    const running = computed(() => jobs.value.find((job) => job.status === 'running'));
    let timer: ReturnType<typeof setInterval> | undefined;
    function refresh(): void {
        if (api.currentUser()?.role !== 'admin') {
            if (timer)
                clearInterval(timer);
            timer = undefined;
            return;
        }
        const snapshot = api.syncSnapshot();
        initialized.value = snapshot.initialized;
        stockUpdatedAt.value = snapshot.stockUpdatedAt;
        catalogUpdatedAt.value = snapshot.catalogUpdatedAt;
        jobs.value = snapshot.jobs;
    }
    function start(kind: SyncJob['kind'], fail: boolean, source?: SyncJob['source']): void {
        const job = api.startSync(kind, source);
        refresh();
        if (timer)
            clearInterval(timer);
        timer = setInterval(() => {
            api.advanceSync(job.id, fail);
            refresh();
            if (!running.value && timer) {
                clearInterval(timer);
                timer = undefined;
            }
        }, 650);
    }
    return { initialized, stockUpdatedAt, catalogUpdatedAt, jobs, running, refresh, start };
});
