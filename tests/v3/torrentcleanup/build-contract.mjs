import assert from 'node:assert/strict'
import { readFileSync, existsSync } from 'node:fs'
const base = new URL('../../../plugins.v3/torrentcleanup/dist/assets/', import.meta.url)
const entry = readFileSync(new URL('remoteEntry.js', base), 'utf8')
assert.ok(!entry.includes('${__federation_expose_'), '联邦入口存在未替换的占位符')
for (const name of ['Page', 'Config', 'AppPage']) {
  assert.ok(entry.includes(`"./${name}"`), `${name} 未导出`)
  const path = entry.match(new RegExp(`\\./__federation_expose_${name}-[^"']+\\.js`))?.[0]
  assert.ok(path && existsSync(new URL(path, base)), `${name} 的编译文件不存在`)
}
console.log('联邦构建合同通过：Page / Config / AppPage 都指向实际文件')
