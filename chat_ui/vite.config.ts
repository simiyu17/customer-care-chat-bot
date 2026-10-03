import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vitejs.dev
export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 3000, // Matches your docker container exposed port mapping
  }
})
