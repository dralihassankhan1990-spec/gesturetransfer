import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  base: '/',
  plugins: [react()],
  server: {
    port: 3000,
    open: true,
    watch: {
      // Exclude the bridge directory from being watched
      ignored: ['**/bridge/**']
    }
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true
  }
});