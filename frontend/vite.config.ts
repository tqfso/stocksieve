// 本文件配置 Vue 编译及前端构建；模拟版不代理任何后端接口。
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({ plugins: [vue()] })
