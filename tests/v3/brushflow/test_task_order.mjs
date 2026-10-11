import test from 'node:test'
import assert from 'node:assert/strict'
import { orderedIds as brush } from '../../../plugins.v3/brushflow/src/utils.js'
import { orderedIds as cleanup } from '../../../plugins.v3/torrentcleanup/src/utils.js'
for (const [name, reorder] of [['brush',brush], ['cleanup',cleanup]]) {
  test(`${name}: drag insertion, arrows, invalid source and filtered positions`, () => {
    const tasks = ['a','hidden','b','c'].map(id=>({id}))
    assert.deepEqual(reorder(tasks,'a','c'),['hidden','b','c','a'])
    assert.deepEqual(reorder(tasks,'c','a'),['c','a','hidden','b'])
    assert.deepEqual(reorder(tasks,'b','c'),['a','hidden','c','b'])
    assert.deepEqual(reorder(tasks,'external','a'),tasks.map(t=>t.id))
    assert.deepEqual(reorder(tasks,'a','c',tasks.filter(t=>t.id!=='hidden')),['b','hidden','c','a'])
    assert.deepEqual(tasks.map(t=>t.id),['a','hidden','b','c'])
  })
}
