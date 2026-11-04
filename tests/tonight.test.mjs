import {test} from 'node:test'
import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import {runInNewContext} from 'node:vm'

const source = await readFile(new URL('../frontend/tonight.js',import.meta.url),'utf8')
const {makeTonight,vaTonightLink} = runInNewContext(source + '\n;({makeTonight,vaTonightLink})',
  {URL,URLSearchParams,AbortController})
const base = 'plugin/PtSubtitleFollowup/fusion/'
const demo = {id:'film17',title:'示例电影',year:2040,minutes:83,chinese_subtitles:null,score:null,
  url:'https://emby.example.test/mediacenter/web/index.html#!/item?id=film17&context=home&serverId=demo'}

function open({get,post,settings} = {}) {
  const calls = []
  let changed, gone
  const h = (tag, props, children) => {
    if (Array.isArray(props) || typeof props === 'string') { children=props; props={} }
    return {tag,props:props||{},children:children?.default ? children.default() : children}
  }
  const Vue = {h,ref:value=>({value}),resolveComponent:x=>x,nextTick:fn=>fn(),
    watch(_get,cb){changed=cb},onUnmounted(cb){gone=cb}}
  const api = {
    get:(url,options)=>{calls.push({url,options,method:'GET'}); return get(url,options)},
    post:(url,body)=>{calls.push({url,body,method:'POST'}); return post(url,body)}
  }
  const render=makeTonight(Vue).setup({api,pluginId:'PtSubtitleFollowup',initial:{},onSettings:settings})
  const flatten=node=>Array.isArray(node) ? node.flatMap(flatten) : node && typeof node==='object' ?
    [node,...flatten(node.children)] : []
  const nodes=()=>flatten(render())
  const button=label=>nodes().find(n=>n.tag==='VBtn' && n.children===label)
  const action=label=>nodes().find(n=>n.tag==='VBtn' && n.props['aria-label']===label+'《'+demo.title+'》')
  return {calls,nodes,button,action,changed,gone}
}

test('first opening and filters are local; explicit selection issues one scoped GET',async()=>{
  const ui=open({get:()=>({success:true,items:[demo],limited:false})})
  assert.deepEqual(ui.calls,[])
  ui.button('筛选').props.onClick()
  ui.nodes().find(n=>n.tag==='select').props.onChange({target:{value:'90'}})
  ui.nodes().find(n=>n.tag==='input').props.onChange({target:{checked:true}})
  assert.deepEqual(ui.calls,[])
  await ui.button('帮我挑片').props.onClick()
  assert.equal(ui.calls[0].url,base+'tonight')
  assert.deepEqual({...ui.calls[0].options.params},{max_minutes:90,chinese_subtitles:true,mode:'discover',page:1})
  assert.equal(ui.nodes().find(n=>n.tag==='a').props.href,demo.url)
  assert.equal(ui.nodes().filter(n=>typeof n.children==='string' && n.children.startsWith('口味 ')).length,0)
  assert.ok(!ui.nodes().some(n=>n.tag==='span' && n.children==='中文字幕'))
  ui.changed()
  assert.ok(!ui.nodes().some(n=>n.tag==='a'))
})

test('stale results cannot replace a new filter result or reopen a dismissed view',async()=>{
  let resolveOld
  const old = new Promise(resolve=>{resolveOld=resolve})
  const ui=open({get:(_url,{params})=>params.max_minutes ? {success:true,items:[],limited:true} : old})
  const pending=ui.button('帮我挑片').props.onClick()
  await ui.button('帮我挑片').props.onClick()
  assert.equal(ui.calls.length,1)
  ui.button('筛选').props.onClick()
  ui.nodes().find(n=>n.tag==='select').props.onChange({target:{value:'120'}})
  assert.ok(ui.calls[0].options.signal.aborted)
  await ui.button('帮我挑片').props.onClick()
  resolveOld({success:true,items:[demo]})
  await pending
  assert.ok(!ui.nodes().some(n=>n.tag==='a'))
  assert.ok(ui.nodes().some(n=>n.children==='本次只核对了部分片库。'))
  ui.gone()
  assert.ok(!ui.nodes().some(n=>n.children==='本次只核对了部分片库。'))
})

test('binding and verification actions are deliberate and retry selection only after verification',async()=>{
  let target=null, ready=false
  const ui=open({settings:value=>{target=value},get:()=>({success:false,reason:'SETUP_REQUIRED'})})
  await ui.button('帮我挑片').props.onClick()
  await ui.button('设置观影偏好').props.onClick()
  assert.equal(target,'tonight')
  assert.equal(ui.calls.length,1)
  const verify=open({
    get:()=>ready ? {success:true,items:[{...demo,chinese_subtitles:true,score:81.1}],limited:false} :
      {success:false,reason:'VERIFY_REQUIRED'},
    post:()=>{ready=true;return {success:true}}
  })
  await verify.button('帮我挑片').props.onClick()
  assert.deepEqual(verify.calls.map(call=>call.method),['GET'])
  await verify.button('验证绑定').props.onClick()
  assert.deepEqual(verify.calls.map(call=>call.url),[base+'tonight',base+'validate',base+'tonight'])
  assert.ok(verify.nodes().some(n=>n.children==='口味 81.1'))
  assert.ok(verify.nodes().some(n=>n.children==='中文字幕'))
})

test('a failure offers recovery without showing private server messages',async()=>{
  for (const response of [Object.assign(Error('private'),{response:{status:401}}),
    Object.assign(Error('private'),{response:{status:403}}),{success:false,reason:'BUSY'}]) {
    const ui=open({get:()=>{if(response instanceof Error) throw response; return response}})
    await ui.button('帮我挑片').props.onClick()
    assert.equal(!!ui.button('重试'),response.reason === 'BUSY')
    assert.ok(!ui.nodes().some(n=>String(n.children).includes('private')))
    assert.ok(!ui.nodes().some(n=>n.tag==='a'))
  }
})

test('an empty filtered result offers a one-click retry with both filters cleared',async()=>{
  const ui=open({get:()=>({success:true,items:[],limited:false})})
  ui.button('筛选').props.onClick()
  ui.nodes().find(n=>n.tag==='select').props.onChange({target:{value:'90'}})
  ui.nodes().find(n=>n.tag==='input').props.onChange({target:{checked:true}})
  await ui.button('帮我挑片').props.onClick()
  await ui.button('不限条件再挑').props.onClick()
  assert.deepEqual({...ui.calls[1].options.params},{max_minutes:0,chinese_subtitles:false,mode:'discover',page:1})
  assert.ok(!ui.button('不限条件再挑'))
})

test('only the matching Emby details page is a link, never an API or credential URL',()=>{
  assert.equal(vaTonightLink(demo.url,demo.id),demo.url)
  for (const url of ['javascript:alert(1)',demo.url.replace('/web/index.html','/Videos/stream'),
    demo.url.replace('emby.example.test','user:password@emby.example.test'),demo.url.replace('#','?api_key=x#'),
    demo.url+'&api_key=x',demo.url.replace('context=home','context=foo'),demo.url.replace('film17','film18')])
    assert.equal(vaTonightLink(url,demo.id),null)
})

const picks=(items=[demo],extra={})=>({success:true,items,context:'scoped-receipt',
  counts:{later:0,hide:0},page:1,pages:1,limited:false,...extra})
const saved=(action='clear',extra={})=>({success:true,undo:{context:'new-receipt',item_id:demo.id,action},
  counts:{later:1,hide:0},...extra})

test('save later is explicit, acknowledged before removal, and undo reuses the exact receipt',async()=>{
  let finish
  const deferred=new Promise(resolve=>{finish=resolve})
  const ui=open({get:()=>picks(),post:(_url,body)=>body.action==='later'?deferred:saved('later',{counts:{later:0,hide:0}})})
  await ui.button('帮我挑片').props.onClick()
  const pending=ui.action('留到下次').props.onClick()
  assert.equal(ui.calls[1].url,base+'tonight/choice')
  assert.deepEqual({...ui.calls[1].body},{context:'scoped-receipt',item_id:demo.id,action:'later'})
  assert.ok(ui.nodes().some(n=>n.tag==='a'),'keep the item visible until persisted')
  assert.equal(ui.action('不感兴趣').props.disabled,true)
  assert.equal(ui.action('留到下次').props.loading,true)
  assert.equal(ui.button('刷新片单').props.loading,false)
  await ui.action('留到下次').props.onClick()
  assert.equal(ui.calls.length,2,'a repeated click never writes twice')
  finish(saved())
  await pending
  assert.ok(!ui.nodes().some(n=>n.tag==='a'))
  assert.ok(ui.button('留到下次 · 1'))
  assert.ok(ui.nodes().some(n=>n.children==='已留到下次'))
  let focused=false
  ui.nodes().find(n=>n.props.class==='va-tonight-notice').props.ref.value={focus(){focused=true}}
  await ui.button('撤销').props.onClick()
  assert.deepEqual({...ui.calls[2].body},{context:'new-receipt',item_id:demo.id,action:'clear'})
  assert.ok(ui.nodes().some(n=>n.tag==='a'))
  assert.ok(focused,'the successful action returns keyboard focus to its notice')
  assert.ok(!ui.button('撤销'),'a completed undo cannot be clicked repeatedly')
  await ui.action('不感兴趣').props.onClick()
  assert.equal(ui.calls[3].body.context,'new-receipt','restored card uses its new revision')
})

test('saved and hidden lists are reachable with no new settings; preserved filters only affect discovery',async()=>{
  const ui=open({get:(_url,{params})=>picks([{...demo,choice:params.mode==='hidden'?'hide':'later'}],{counts:{later:1,hide:1}}),
    post:()=>saved('hide',{counts:{later:1,hide:0}})})
  ui.button('筛选').props.onClick()
  ui.nodes().find(n=>n.tag==='select').props.onChange({target:{value:'90'}})
  ui.nodes().find(n=>n.tag==='input').props.onChange({target:{checked:true}})
  assert.equal(ui.calls.length,0)
  await ui.button('留到下次').props.onClick()
  assert.deepEqual({...ui.calls[0].options.params},{max_minutes:0,chinese_subtitles:false,mode:'saved',page:1})
  assert.ok(ui.action('移出片单'))
  assert.ok(!ui.button('筛选'))
  await ui.button('已略过 · 1').props.onClick()
  assert.equal(ui.calls[1].options.params.mode,'hidden')
  assert.ok(ui.action('恢复推荐'))
  await ui.action('恢复推荐').props.onClick()
  assert.equal(ui.calls[2].body.action,'clear')
  assert.ok(ui.nodes().some(n=>n.children==='已恢复推荐'))
  await ui.button('为我挑选').props.onClick()
  assert.deepEqual({...ui.calls[3].options.params},{max_minutes:90,chinese_subtitles:true,mode:'discover',page:1})
})

test('rejected choices retain the card and offer a fresh read, not a fabricated success',async()=>{
  for (const code of ['STALE_CONTEXT','CHOICE_CHANGED','CHOICE_LIMIT','STORAGE_FAILED','private-key']) {
    const ui=open({get:()=>picks(),post:()=>({success:false,reason:code})})
    await ui.button('帮我挑片').props.onClick()
    await ui.action('不感兴趣').props.onClick()
    assert.ok(ui.nodes().some(n=>n.tag==='a'))
    assert.ok(!ui.nodes().some(n=>n.children==='已略过这部电影'))
    assert.ok(!ui.button('撤销'))
    assert.ok(!ui.nodes().some(n=>String(n.children).includes('private-key')))
    assert.equal(ui.action('留到下次').props.disabled,true)
    await ui.button('刷新片单').props.onClick()
    assert.equal(ui.calls.filter(call=>call.method==='GET').length,2)
    assert.equal(ui.action('留到下次').props.disabled,false)
  }
})

test('scope change while a choice is pending clears personal list, counts and the late response',async()=>{
  let finish
  const deferred=new Promise(resolve=>{finish=resolve})
  const ui=open({get:()=>picks([demo],{counts:{later:2,hide:3}}),post:()=>deferred})
  await ui.button('帮我挑片').props.onClick()
  const pending=ui.action('不感兴趣').props.onClick()
  ui.changed()
  finish(saved('clear',{counts:{later:2,hide:4}}))
  await pending
  assert.ok(!ui.nodes().some(n=>n.tag==='a'))
  assert.ok(!ui.nodes().some(n=>String(n.children).startsWith('已略过')))
  assert.ok(!ui.button('撤销'))
  assert.ok(ui.button('留到下次'))
})

test('unavailable saved entries can be removed without exposing a stale link; pagination fetches deliberately',async()=>{
  const unavailable={...demo,title:'暂时无法观看的电影',available:false,reason:'已看完或不在当前片库',choice:'later'}
  const ui=open({get:(_url,{params})=>picks([unavailable],{counts:{later:13,hide:0},page:params.page,pages:2})})
  await ui.button('留到下次').props.onClick()
  assert.ok(!ui.nodes().some(n=>n.tag==='a'))
  assert.ok(!ui.nodes().some(n=>n.children==='请在 Emby 中查找此片'))
  assert.ok(ui.button('移出片单'))
  assert.ok(!ui.button('不感兴趣'))
  assert.equal(ui.button('上一页').props.disabled,true)
  await ui.button('下一页').props.onClick()
  assert.equal(ui.calls[1].options.params.page,2)
  assert.equal(ui.button('下一页').props.disabled,true)
  assert.equal(ui.calls.filter(call=>call.method==='POST').length,0)
})

test('an expired login during a choice clears the list instead of leaving private details onscreen',async()=>{
  const ui=open({get:()=>picks(),post:()=>{throw Object.assign(Error('private credential'),{status:401})}})
  await ui.button('帮我挑片').props.onClick()
  await ui.action('不感兴趣').props.onClick()
  assert.ok(!ui.nodes().some(n=>n.tag==='a'))
  assert.ok(!ui.button('留到下次'))
  assert.ok(ui.nodes().some(n=>n.children==='请重新登录 MoviePilot。'))
  assert.ok(!ui.nodes().some(n=>String(n.children).includes('private credential')))
})

test('revoked library metadata remains hidden while local records offer a direct binding recovery',async()=>{
  let target
  const ui=open({settings:value=>{target=value},get:()=>picks([
    {id:demo.id,title:'暂时无法观看的电影',available:false,choice:'later'}
  ],{counts:{later:1,hide:0},unavailable_reason:'LIBRARY_NOT_VISIBLE'})})
  await ui.button('留到下次').props.onClick()
  assert.ok(ui.nodes().some(n=>n.children==='片库权限有变化，暂不显示影片资料。'))
  assert.ok(ui.button('移出片单'))
  assert.ok(!ui.button('不感兴趣'))
  assert.ok(!ui.nodes().some(n=>n.tag==='a'))
  await ui.button('检查绑定').props.onClick()
  assert.equal(target,'tonight')
  assert.equal(ui.calls.length,1)
})
