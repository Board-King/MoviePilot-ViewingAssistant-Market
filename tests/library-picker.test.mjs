import test from 'node:test'
import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import {runInNewContext} from 'node:vm'

const files=await Promise.all(['config.js','library-picker.js'].map(name=>readFile(new URL('../frontend/'+name,import.meta.url),'utf8')))
const makePicker=runInNewContext(files.join('\n')+'\nmakeLibraryPicker')
function open(get,value='') {
  let unmount
  const props={api:{get},pluginId:'Demo',server:'Server',user:'A'.repeat(32),value,fieldProps:{}}
  const changes=[]
  const Vue={ref:value=>({value}),resolveComponent:s=>s,onUnmounted:cb=>{unmount=cb},
    h:(tag,props,content)=>({tag,props,content:content?.default ? content.default() : content})}
  const render=makePicker(Vue).setup(props,{emit:(event,value)=>{changes.push([event,value]);props.value=value}})
  const find=tag=>render().content.find(n=>n?.tag===tag)
  const btn=text=>render().content.find(n=>n?.content===text)
  return {props,changes,find,btn,unmount}
}
const listed={success:true,libraries:[{id:'A',name:'电影'},{id:'B',name:'动画'}]}

test('library names load on demand; saved and temporarily missing IDs stay unless explicitly removed',async()=>{
  const calls=[]
  const ui=open((path,args)=>{calls.push([path,args.params]);return listed}, 'old, A')
  assert.equal(calls.length,0)
  await ui.btn('更改指定媒体库').props.onClick()
  assert.equal(calls[0][0],'plugin/Demo/fusion/binding/libraries')
  assert.deepEqual({...calls[0][1]},{server_ref:'Server',user_id:'a'.repeat(32)})
  assert.deepEqual(ui.changes,[])
  const select=ui.find('VSelect').props
  assert.deepEqual(Array.from(select.modelValue),['old','A'])
  assert.equal(select.items.length,3)
  select['onUpdate:modelValue'](['A','forged'])
  assert.deepEqual(ui.changes,[])
  select['onUpdate:modelValue'](['B','A'])
  assert.deepEqual(ui.changes,[['change','B,A']])
})

test('a failed directory preserves raw IDs, and manual mode never requests a directory by itself',async()=>{
  const ui=open(async()=>{throw Error('private token')},['existing'])
  await ui.btn('更改指定媒体库').props.onClick()
  assert.deepEqual(ui.changes,[])
  assert.ok(ui.btn('重新读取媒体库'))
  ui.btn('手动填写 ID').props.onClick()
  const manual=ui.find('VTextarea').props
  assert.equal(manual.modelValue,'existing')
  manual['onUpdate:modelValue']('A,old')
  assert.deepEqual(ui.changes,[['change','A,old']])
})

test('a late response cannot show or grant the previous user’s libraries',async()=>{
  let finish
  const ui=open(()=>new Promise(ok=>{finish=ok}))
  const response=ui.btn('选择媒体库').props.onClick()
  ui.unmount()
  finish(listed)
  await response
  assert.ok(!ui.find('VSelect'))
  assert.deepEqual(ui.changes,[])
})
