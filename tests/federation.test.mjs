import test from 'node:test'
import assert from 'node:assert/strict'
import {mkdtemp, readFile, writeFile, mkdir, rm} from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import {pathToFileURL} from 'node:url'

// Exercise the shipped bytes with the same shared-provider/factory contract
// as MP's Vite host; a clone must never address the primary install's APIs.
test('the distributed module mounts both exposes only for the reviewed installation', async () => {
  const base = await mkdtemp(path.join(os.tmpdir(),'mp-va-federation-'))
  try {
    const runtime = await readFile(new URL('../plugins.v2/ptsubtitlefollowup/dist/assets/remoteEntry.js',import.meta.url))
    await writeFile(path.join(base,'package.json'), '{"type":"module"}')
    for (const id of ['ptsubtitlefollowup', 'ptsubtitlefollowup_clone']) {
      const dir = path.join(base,'api/v1/plugin/file',id,'dist/assets')
      await mkdir(dir,{recursive:true})
      const entry = path.join(dir,'remoteEntry.js')
      await writeFile(entry,runtime)
      const remote = await import(pathToFileURL(entry).href)
      await assert.rejects(remote.get('./Page'))
      remote.init({vue:{'3.x':{get:async()=>()=>({h:()=>{ throw Error('unexpected render') }})}}})
      if (id.endsWith('_clone')) { await assert.rejects(remote.get('./Config')); continue }
      const Page = (await remote.get('./Page'))(), Config = (await remote.get('./Config'))()
      assert.equal(Page.name,'ViewingAssistantPage')
      assert.equal(Config.name,'ViewingAssistantConfig')
      await assert.rejects(remote.get('./Admin'))
    }
  } finally {
    if (path.dirname(path.resolve(base)) !== path.resolve(os.tmpdir()) || !path.basename(base).startsWith('mp-va-federation-')) throw Error('Unexpected test directory')
    await rm(base,{recursive:true,force:true})
  }
})
