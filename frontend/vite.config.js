import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const coreTarget = process.env.CORE_PROXY_TARGET || 'http://localhost:8010'
const coreHost = coreTarget.replace(/^http(s?):\/\//, '')

export default defineConfig({
  plugins: [react()],
  build: {
    // echarts 542KB 是「按需加载」的产物，不是首屏负担：
    // 实测首页 12 个 JS 里 0 个 echarts，只有进 /dashboard 才拉取并渲染图表。
    // 阈值调到 600 是为了让告警回到「有意义的异常」——超过它才说明真该拆了。
    chunkSizeWarningLimit: 600,
  },
  server: {
    port: 5173,
    host: true,
    allowedHosts: ['.monkeycode-ai.online'],
    proxy: {
      '/api': { target: coreTarget, changeOrigin: true },
      '/ws': { target: `ws://${coreHost}`, ws: true, changeOrigin: true },
    },
  },
})
