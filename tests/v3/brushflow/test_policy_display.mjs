import test from 'node:test'
import assert from 'node:assert/strict'
import { torrentStateLabel, globalDeleteRules, normalizeSettings, normalizeTask, effectiveDeleteSettings } from '../../../plugins.v3/brushflow/src/utils.js'

test('status contains only lifecycle state, never policy or deletion reason', () => {
  assert.equal(torrentStateLabel({ size: 100, downloaded: 100, hit_and_run: true }), '做种中')
  assert.equal(torrentStateLabel({ size: 100, downloaded: 30 }), '下载中 30%')
  assert.equal(torrentStateLabel({ deleted: true, delete_reason: 'long reason' }), '已删除')
})
test('full explanation covers all enabled global conditions', () => {
  const settings = {global_min_seed_hours:168,global_inactive_hours:48,global_seed_ratio:2,
    global_seed_size:10,global_download_time:24,global_seed_avgspeed:5,global_hr_seed_time:240,global_del_no_free:true}
  const text = globalDeleteRules(settings)
  for (const expected of ['168 小时','48 小时','分享率达到 2','上传量达到 10 GB','下载超过 24 小时','低于 5 KB/s','H&R','促销过期']) assert.ok(text.includes(expected), expected)
})
test('saving preserves all restored conditions and disabled blanks', () => {
  const settings = normalizeSettings({global_proxy_delete:true,global_delete_size_range:'450-550',
    global_seed_ratio:'2',global_seed_size:'',global_download_time:'24',global_seed_avgspeed:'5',
    global_hr_seed_time:'240',global_del_no_free:true,global_delete_except_tags:'MOVIEPILOT,H&R'})
  assert.equal(settings.global_seed_ratio, 2)
  assert.equal(settings.global_seed_size, null)
  assert.equal(settings.global_download_time, 24)
  assert.equal(settings.global_seed_avgspeed, 5)
  assert.equal(settings.global_hr_seed_time, 240)
  assert.equal(settings.global_del_no_free, true)
  assert.equal(settings.global_delete_except_tags, 'MOVIEPILOT,H&R')
})

test('task overrides are reflected in actual policy text and false remains explicit', () => {
  const settings = { global_min_seed_hours: 168, global_inactive_hours: 48, global_seed_ratio: 2, global_del_no_free: true }
  const task = normalizeTask({ min_seed_hours: 72, seed_inactivetime: 60, seed_ratio: 4, del_no_free: false })
  const text = globalDeleteRules(settings, task)
  for (const value of ['72 小时', '1 小时', '分享率达到 4']) assert.ok(text.includes(value), value)
  for (const value of ['168 小时', '48 小时', '分享率达到 2', '促销过期']) assert.ok(!text.includes(value), value)
  const inherited = effectiveDeleteSettings(settings, normalizeTask({ seed_ratio: '', del_no_free: null }))
  assert.equal(inherited.global_seed_ratio, 2)
  assert.equal(inherited.global_del_no_free, true)
})
