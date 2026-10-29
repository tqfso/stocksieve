// 本文件定义前端页面与模拟角色守卫；使用 hash 路由便于静态托管。
import { createRouter, createWebHashHistory } from 'vue-router';
import { currentUser } from './mock/service.ts';
export const router = createRouter({
    history: createWebHashHistory(),
    routes: [
        { path: '/', redirect: '/opportunities' },
        { path: '/login', component: () => import('./pages/LoginPage.vue'), meta: { public: true, title: '登录' } },
        { path: '/opportunities', component: () => import('./pages/OpportunitiesPage.vue'), meta: { title: '机会筛选' } },
        { path: '/stocks/:code', component: () => import('./pages/StockPage.vue'), meta: { title: '个股详情' } },
        { path: '/admin/data', component: () => import('./pages/DataPage.vue'), meta: { admin: true, title: '数据管理' } },
        { path: '/admin/users', component: () => import('./pages/UsersPage.vue'), meta: { admin: true, title: '用户管理' } },
        { path: '/admin/password', component: () => import('./pages/PasswordPage.vue'), meta: { admin: true, title: '修改密码' } },
        { path: '/:pathMatch(.*)*', component: () => import('./pages/NotFoundPage.vue'), meta: { title: '页面未找到' } },
    ],
    scrollBehavior: () => ({ top: 0 }),
});
router.beforeEach((to) => {
    const user = currentUser();
    if (!to.meta.public && !user)
        return { path: '/login', query: { redirect: to.fullPath } };
    if (to.path === '/login' && user)
        return '/opportunities';
    if (to.meta.admin && user?.role !== 'admin')
        return '/opportunities';
});
router.afterEach((to) => { document.title = `${String(to.meta.title ?? 'StockSieve')} · StockSieve`; });
