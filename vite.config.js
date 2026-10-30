import { defineConfig } from 'vite';

// The browser only talks to its own origin; in development Vite forwards the API.
export default defineConfig({
    root: 'web',
    build: { outDir: 'dist', emptyOutDir: true, chunkSizeWarningLimit: 1500 },
    server: {
        host: '127.0.0.1',
        port: Number(process.env.PORT || 5183),
        proxy: { '/api': { target: `http://127.0.0.1:${process.env.GLBSMITH_PORT || 8021}`, changeOrigin: false } },
    },
});
