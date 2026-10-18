import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'
import process from 'node:process'
import { fileURLToPath } from 'url'

const __filename = fileURLToPath(import.meta.url)
const __dirname = path.dirname(__filename)

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, __dirname, 'VITE_')
  const CORE_API_TARGET = process.env.VITE_CORE_API_TARGET || env.VITE_CORE_API_TARGET || 'http://127.0.0.1:8000'

  return {
    base: './',
    plugins: [react()],
    optimizeDeps: {
      include: [
        'react',
        'react-dom',
        'framer-motion',
        'three',
        'three/webgpu',
        'three/tsl',
        '@react-three/fiber',
        '@react-three/drei',
        '@react-three/xr'
      ]
    },
    resolve: {
      alias: {
        '@': path.resolve(__dirname, './src')
      },
      dedupe: ['react', 'react-dom', 'framer-motion', 'three']
    },
    server: {
      host: '0.0.0.0',
      port: 5173,
      strictPort: true,
      allowedHosts: true,
      cors: true,
      hmr: true,
      proxy: {
        '/api': {
          target: CORE_API_TARGET,
          changeOrigin: true,
          timeout: 60000,
          proxyTimeout: 60000,
          rewrite: (path) => path.replace(/^\/api/, '')
        },
        ...Object.fromEntries(
          [
            '/ghost_sync', '/health', '/world', '/agents', '/bio', '/iot',
            '/resonance', '/system_metrics', '/system_diagnostic', '/action',
            '/tools', '/memory', '/auth', '/reason', '/jarvis', '/orchestrator',
            '/vision', '/computer_use', '/stream', '/ws'
          ].map(route => [
            route,
            {
              target: CORE_API_TARGET,
              changeOrigin: true,
              ws: route === '/ws',
              timeout: 60000
            }
          ])
        )
      }
    }
  }
})
