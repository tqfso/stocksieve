// 本文件初始化 Vue、路由、全局状态及组件库。
import { createApp } from 'vue';
import { createPinia } from 'pinia';
import ElementPlus from 'element-plus';
import zhCn from 'element-plus/es/locale/lang/zh-cn';
import 'element-plus/dist/index.css';
import './theme.css';
import './style.css';
import App from './App.vue';
import { router } from './router.ts';
createApp(App).use(createPinia()).use(router).use(ElementPlus, { locale: zhCn }).mount('#app');
