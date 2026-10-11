import test from 'node:test'
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
const { reactive } = createRequire(new URL('../../../plugins.v3/torrentcleanup/package.json', import.meta.url))('vue')
import { taskDraft, conditionDraft, normalizeTask, canExecute, unwrap, errorText, tags } from '../../../plugins.v3/torrentcleanup/src/utils.js'

test('标签支持组合分隔并保留全部匹配语义，旧任务转为相同约束', () => {
  assert.deepEqual(tags(['刷流,已整理', '保留，H&R\n已整理']), ['刷流','已整理','保留','H&R'])
  const retained = conditionDraft({id:'a',mode:'retention'})
  assert.equal(retained.completion, 'completed')
  assert.equal(retained.filter_seed, true)
  assert.equal(retained.require_healthy, true)
  const error = conditionDraft({id:'b',mode:'download_error'})
  assert.equal(error.completion, 'incomplete')
  assert.equal(error.observe_state, true)
  assert.equal(error.require_idle_download, true)
  assert.deepEqual(error.states, ['error','missingFiles'])
})

test('显示宿主返回的具体参数错误', () => {
  assert.equal(errorText({ response: { data: { message: '请求参数不正确', data: [{ message: '请输入本站相对路径' }] } } }), '请输入本站相对路径')
})

test('编辑 Vue 响应式任务时复制数组，不改变原配置', () => {
  const original = reactive({ name: 'CarPT', site_ids: [3], protect_tags: ['保留'] })
  const draft = taskDraft(original)
  draft.site_ids.push(4)
  assert.deepEqual([...original.site_ids], [3])
  assert.equal(normalizeTask(reactive(draft)).name, 'CarPT')
})
test('失效预览、停用开关、仅预览动作均不能执行', () => {
  const config = { enabled: true, revision: 'r' }, task = { enabled: true, action: 'delete' }
  const plan = { candidate_count: 1, action: 'delete', revision: 'r', created_at: 1000 }
  assert.ok(canExecute(config, task, plan, false, 1100))
  assert.ok(!canExecute(config, task, plan, false, 1901))
  assert.ok(!canExecute({ ...config, enabled: false }, task, plan, false, 1100))
  assert.ok(!canExecute(config, { ...task, enabled: false }, plan, false, 1100))
  assert.ok(!canExecute(config, task, { ...plan, revision: 'old' }, false, 1100))
  assert.ok(!canExecute(config, task, plan, true, 1100))
})
test('接口错误保留为错误，不误报成功', () => {
  assert.throws(() => unwrap({ data: { success: false, message: '保存失败' } }), /保存失败/)
  assert.deepEqual(unwrap({ success: true, data: { id: '1' } }), { id: '1' })
})
