import test from 'node:test'
import assert from 'node:assert/strict'
import {mkdtemp, readFile, writeFile, mkdir, rm} from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import {pathToFileURL} from 'node:url'
import {runInNewContext} from 'node:vm'

test('linked and unlinked candidate cards share the same score track', async () => {
  const source = await readFile(new URL('../frontend/shelf.js',import.meta.url),'utf8')
  const {render,style} = runInNewContext(source + '\n;({render:vaRenderShelfCard,style:vaShelfStyle})', {
    renderNode:(_Vue,part)=>part,
  })
  const Vue={h:(tag,props,children)=>({tag,children:children??props,props})}
  const score={props:{'data-va-reason':'Film'},content:[{props:{'aria-label':'Score'},text:'Match'}]}
  for (const info of [[{component:'a'},{component:'small'}],[{component:'img'},{component:'strong'},{component:'small'}]]) {
    let opened
    const result = render(Vue,{props:{class:'va-candidate'},content:[...info,score]},node=>{opened=node})
    assert.equal(result.children.length,2)
    const [metadata,button]=result.children
    assert.deepEqual(Array.from(metadata.children),info)
    assert.equal(button.tag,'button')
    button.props.onClick()
    assert.equal(opened,score)
  }
  assert.match(style,/\.va-candidate\s*\{[^}]*grid-row:\s*span 2[^}]*grid-template-rows:\s*subgrid/)
})

test('press feedback on dashboard disclosures keeps the actual pointer target stationary', async () => {
  // Vue mounts shelf and tonight styles after the shell. A transform from any
  // of them can move a wide summary out from under the pointer before release.
  const styles = await Promise.all(['shell','shelf','tonight'].map(async part => {
    const source = await readFile(new URL(`../frontend/${part}.js`,import.meta.url),'utf8')
    return runInNewContext(source + '\n;va' + part[0].toUpperCase() + part.slice(1) + 'Style')
  }))
  const moving = []
  for (const style of styles) {
    for (const [,head,body] of style.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
      if (!/(?:^|;)\s*(?:transform|scale|translate)\s*:/.test(body)) continue
      for (const selector of head.split(/,(?![^()]*\))/)) {
        if (selector.includes(':active')) moving.push(selector.trim())
      }
    }
  }
  assert.ok(moving.length,'buttons still give visible press feedback')
  for (const selector of moving)
    assert.match(selector,/:active\s*(?:>|\s)\s*[^\s]/,`the pressed element itself must stay still: ${selector}`)
})

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

for (const wrapped of [false, true]) test(`${wrapped ? 'V3' : 'V2'} board loads host pages and refreshes with raw plugin actions`, async () => {
  const shell = await readFile(new URL('../frontend/shell.js',import.meta.url),'utf8')
  const page = await readFile(new URL('../frontend/page.js',import.meta.url),'utf8')
  const makePage = runInNewContext(shell + '\n' + page + '\n;makePage', {
    makeTonight:()=> 'Tonight', makeShelf:()=> 'Shelf', makeSubtitleRail:()=> 'Subtitles', makeSupport:()=> 'Support',
  })
  let mounted, reads = 0
  const calls = [], pid = 'PtSubtitleFollowup'
  const h = (type, props, children) => ({type, props:props || {}, children:children?.default ? children.default() : children})
  const Vue = {h, ref:value=>({value}), computed:fn=>({get value(){return fn()}}),
    onMounted:fn=>{mounted=fn}, onUnmounted(){}, resolveComponent:name=>name, nextTick:fn=>fn()}
  const api = {
    async get(url) {
      calls.push(['GET',url]); reads++
      assert.equal(url,'plugin/page/'+pid)
      const data = {render_mode:'vue',page:[
        {component:'strong',text:reads === 1 ? '每日发现' : '已刷新'},
        {component:'VBtn',text:'更新候选',props:{'data-va-primary':'refresh-candidates'},
          events:{click:{api:'plugin/'+pid+'/fusion/candidates/refresh',method:'POST'}}},
      ]}
      return wrapped ? {success:true,message:'',data} : data
    },
    async post(url) { calls.push(['POST',url]); return {success:true,message:'已排队'} },
  }
  const render = makePage(Vue,pid).setup({api},{emit(){}})
  const flatten = node => Array.isArray(node) ? node.flatMap(flatten) : node && typeof node === 'object' ?
    [node,...flatten(node.children)] : []
  await mounted()
  assert.equal(calls.length,1) // Mount never starts discovery or creates a subscription.
  assert.ok(flatten(render()).some(node=>node.children?.includes('每日发现')))
  const button = flatten(render()).find(node=>node.type === 'VBtn' && node.children?.includes('更新候选'))
  assert.ok(button)
  button.props.onClick()
  await new Promise(resolve=>setImmediate(resolve))
  assert.deepEqual(calls.map(row=>row[0]),['GET','POST','GET'])
  assert.ok(flatten(render()).some(node=>node.children?.includes('已刷新')))
  assert.ok(flatten(render()).some(node=>node.children === '已排队'))
})
