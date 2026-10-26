import tailwindcss from '@tailwindcss/vite';
import vue from '@vitejs/plugin-vue';
import path from 'path';
import { fileURLToPath } from 'url';
import { defineConfig, loadEnv } from 'vite';
import { createApiMiddleware } from './src/server/apiMiddleware.ts';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  const externalBackend = env.VITE_BACKEND_URL;

  return {
    plugins: [
      vue(),
      tailwindcss(),
      {
        name: 'apartment-association-openapi-backend',
        configureServer(server) {
          if (!externalBackend) {
            server.middlewares.use(createApiMiddleware());
          }
        },
      },
    ],
    resolve: {
      alias: {
        '@': path.resolve(__dirname, './src'),
      },
    },
    server: {
      hmr: process.env.DISABLE_HMR !== 'true',
      watch: process.env.DISABLE_HMR === 'true' ? null : {},
      proxy: externalBackend
        ? {
            '/api': {
              target: externalBackend,
              changeOrigin: true,
            },
          }
        : undefined,
    },
  };
});
