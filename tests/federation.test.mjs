import test from 'node:test'
import assert from 'node:assert/strict'
import {mkdtemp, readFile, writeFile, mkdir, rm} from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import {pathToFileURL} from 'node:url'
import {runInNewContext} from 'node:vm'

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

test('the subtitle rail lets a user inspect and filter independent results in a mixed series', async () => {
  const source = await readFile(new URL('../frontend/subtitles.js',import.meta.url),'utf8')
  const makeRail = runInNewContext(source + '\n;makeSubtitleRail')
  const h = (tag, props, content) => typeof props === 'object' && !Array.isArray(props) ?
    {tag,props:props ?? {},content} : {tag,props:{},content:props}
  const Vue = {h,ref:value=>({value}),computed:fn=>({get value(){return fn()}}),
    watch(){},onMounted(){},onUnmounted(){}}
  const marks = [['saved','S02E01','',''],['existing','S02E02','',''],['retry','S02E03','','']]
  const render = makeRail(Vue).setup({records:[{title:'Test',marks,attempts:3,checked:'',note:''}],enabled:true})
  const nodes = node => Array.isArray(node) ? node.flatMap(nodes) : node && typeof node === 'object' ?
    [node,...nodes(node.content)] : []
  const byClass = cls => nodes(render()).filter(x=>x.props.class===cls)
  const results = () => byClass('va-sub-mark')
  assert.equal(results().length,3)
  results()[1].props.onClick()
  assert.ok(nodes(render()).some(x => x.tag === 'strong' && x.content === 'Test · S02E02'))
  const filter = byClass('va-sub-key').find(x=>x.props['data-kind']==='saved')
  filter.props.onClick()
  assert.equal(results().length,1)
  assert.match(results()[0].props['aria-label'],/S02E01.*已补齐/)
  filter.props.onClick()
  assert.equal(results().length,3)
})
