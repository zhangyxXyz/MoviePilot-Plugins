// 仅供离线 UI 验证，生产构建入口不引用本文件。
import { createApp, h } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import 'vuetify/styles'
import '@mdi/font/css/materialdesignicons.css'
import Workbench from './Workbench.vue'
import { taskDraft } from './utils'
const task = taskDraft({ id: 'demo1', name: 'CarPT · 失效种子复核', enabled: true, downloader: 'Transmission', site_ids: [3], mode: 'invalid', action: 'mark' })
const state = {
  config: { enabled: false, show_sidebar: true, revision: 'demo', sites: [], tasks: [task,
    taskDraft({ id: 'demo2', name: 'QB · 已整理任务保留', downloader: 'qBittorrent', site_ids: [3, 4] })] },
  options: { sites: [{ title: 'CarPT', value: 3 }, { title: '示例站点', value: 4 }], downloaders: [{ title: 'Transmission', value: 'Transmission' }, { title: 'qBittorrent', value: 'qBittorrent' }] },
  plans: { demo1: { id: 'plan', task_id: 'demo1', revision: 'demo', action: 'mark', created_at: Date.now()/1000, candidate_count: 1, candidate_bytes: 12884901888,
    items: [
      { hash: 'a'.repeat(40), name: '示例影片.2025.1080p.BluRay', size: 12884901888, seed_hours: 720, ratio: 2.8, site_ids: [3], eligible: true, reasons: ['满足全部筛选及保护条件'], evidence: [{ site_id: 3, reason: 'Tracker 明确不存在；详情页确认删除；观察完成' }] },
      { hash: 'b'.repeat(40), name: '示例剧集.S01E06.2160p.WEB-DL', size: 3221225472, seed_hours: 72, ratio: 0.6, site_ids: [3], eligible: false, reasons: ['累计做种时间不足', '站点 3 缺少当前有效的失效复核'], evidence: [{ site_id: 3, reason: '站点请求超时，保留任务' }] },
      { hash: 'c'.repeat(40), name: '示例纪录片.2024.2160p', size: 8589934592, seed_hours: 360, ratio: 1.2, site_ids: [3], eligible: false, reasons: ['命中保护标签'], evidence: [] },
    ] } }, runs: [], runtime: {}, bindings: [],
}
const api = {
  async get() { return { success: true, data: JSON.parse(JSON.stringify(state)) } },
  async post(path, payload) {
    if (path.endsWith('/tasks')) {
      payload.id ||= `demo${Date.now()}`
      state.config.tasks = [...state.config.tasks.filter(t => t.id !== payload.id), JSON.parse(JSON.stringify(payload))]
      return { success: true, data: { id: payload.id } }
    }
    if (path.endsWith('/settings')) { Object.assign(state.config, payload); state.plans = {} }
    return { success: true }
  },
  async delete(path) { state.config.tasks = state.config.tasks.filter(t => !path.endsWith('/'+t.id)); return { success: true } },
}
createApp({ render: () => h(components.VApp, {}, () => h(components.VMain, {}, () => h(Workbench, { api }))) })
  .use(createVuetify({ components, directives, theme: { defaultTheme: 'light', themes: { light: { colors: { primary: '#7367F0', background: '#f8f7fa' } } } } }))
  .mount('#app')
