import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import federation from '@originjs/vite-plugin-federation'

export default defineConfig({
  plugins: [vue(), federation({
    name: 'TorrentCleanup', filename: 'remoteEntry.js',
    exposes: {
      './Page': './src/Page.vue', './Config': './src/Config.vue', './AppPage': './src/AppPage.vue',
    },
    shared: {
      vue: { requiredVersion: false, generate: false, singleton: true },
      vuetify: { requiredVersion: false, generate: false, singleton: true },
    },
  })],
  build: { target: 'esnext', cssCodeSplit: true },
})
