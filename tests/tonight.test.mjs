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
  return {calls,nodes,button,changed,gone}
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
  assert.deepEqual({...ui.calls[0].options.params},{max_minutes:90,chinese_subtitles:true})
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
  assert.deepEqual({...ui.calls[1].options.params},{max_minutes:0,chinese_subtitles:false})
  assert.ok(!ui.button('不限条件再挑'))
})

test('only the matching Emby details page is a link, never an API or credential URL',()=>{
  assert.equal(vaTonightLink(demo.url,demo.id),demo.url)
  for (const url of ['javascript:alert(1)',demo.url.replace('/web/index.html','/Videos/stream'),
    demo.url.replace('emby.example.test','user:password@emby.example.test'),demo.url.replace('#','?api_key=x#'),
    demo.url+'&api_key=x',demo.url.replace('context=home','context=foo'),demo.url.replace('film17','film18')])
    assert.equal(vaTonightLink(url,demo.id),null)
})
