import test from 'node:test'
import assert from 'node:assert/strict'
import {orderedIds,draft,lines} from '../../../plugins.v3/autosubscribe/src/utils.js'
test('drag task without changing task identity',()=>{const tasks=[{id:'a'},{id:'b'},{id:'c'}];assert.deepEqual(orderedIds(tasks,'c','a'),['c','a','b']);assert.deepEqual(tasks.map(t=>t.id),['a','b','c'])})
test('editing clones nested arrays and keeps id',()=>{const old={id:'a',actors:['成龙']};const d=draft(old);d.actors.push('其他');assert.equal(d.id,'a');assert.deepEqual(old.actors,['成龙'])})
test('parse actor lines and names',()=>assert.deepEqual(lines('成龙，成龙\n123'),['成龙','123']))
