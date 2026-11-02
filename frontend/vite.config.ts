// 本文件配置Vue构建及本机开发代理；生产构建由后端以同源方式提供。
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({ plugins: [vue()], server: { proxy: { '/api': { target: process.env.VITE_API_TARGET ?? 'http://127.0.0.1:24048', changeOrigin: false } } } })
