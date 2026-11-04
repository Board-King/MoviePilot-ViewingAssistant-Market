// Exercise the settings component's actual click, API and host event contract.
const assert = require('node:assert/strict')
const { readFileSync } = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const { test } = require('node:test')
const script = ['shell','library-picker','config'].map(name=>readFileSync(path.join(__dirname, '../frontend/'+name+'.js'), 'utf8')).join('\n')
const { makeConfig, draftIssues, vaTakeHandoff, vaLeaveBoard } = vm.runInNewContext(
  script + '\n;({makeConfig,draftIssues,vaTakeHandoff,vaLeaveBoard})',{setTimeout,clearTimeout})
const id = 'PtSubtitleFollowup', base = 'plugin/' + id, scope = base + '/fusion/execution/'
const full = {
  fusion_enabled: true, fusion_server_ref: 'Test', fusion_user_id: 'a'.repeat(32), fusion_library_mode: 'all_visible',
  fusion_discovery_enabled: true, fusion_discovery_sources: ['tmdb_popular_movie'],
  fusion_execution_enabled: true, fusion_execution_owner: 'Test', fusion_execution_sources: ['tmdb_popular_movie'],
  fusion_execution_preset: 'conservative', fusion_execution_daily_limit: 1,
  fusion_execution_legacy_isolated: true, fusion_execution_storage_verified: true, unrelated_option: 'preserve-me',
}
async function open(config, failAt, directory) {
  const calls = [], emitted = []
  let persisted = structuredClone(config), mounted, updateField, armed = false
  const api = {
    async get(url, options) {
      calls.push(['GET', url])
      if (url === 'plugin/form/' + id) return { conf: [{ kind: 'form',props:{'data-va-binding':true} }], model: persisted, render_mode: 'vue' }
      if (url === base + '/fusion/binding/users') return directory(options.params.server_ref)
      assert.equal(url, scope + 'review')
      if (failAt === 'review') throw Error()
      const grant = {method:'POST',api:scope+'authorize',params:{expected_digest:failAt==='digest'?'bad':'a'.repeat(64)}}
      return {page:[{component:'div',content:[{component:'div',props:{'data-va-execution-review-action':true},content:[
        {component:'span',text:'授权后可能搜索和下载。'},
        {component:'VBtn',props:{disabled:failAt==='blocked'},events:{click:armed?{method:'POST',api:scope+'revoke'}:grant}}
      ]}]}]}
    },
    async post(url, body) {
      calls.push(['POST', url, body])
      if (url === scope + 'preflight' && failAt === 'check') return { success: false, reason: 'INCOMPATIBLE' }
      if (url === scope+'authorize') armed = true
      if (url === scope+'revoke') armed = false
      return { success: true }
    },
    async put(url, body) {
      calls.push(['PUT', url, body])
      assert.equal(url, base)
      if (failAt === 'save') return { success: false }
      persisted = structuredClone(body)
      return { success: true }
    },
  }
  const Vue = {
    h: (type, props, children) => ({ type, props: props || {}, children: typeof children === 'function' ? children() : children?.default?children.default():children }),
    ref: value => ({ value }), computed: get => ({ get value() { return get() } }),
    onMounted: fn => { mounted = fn }, onUnmounted() {}, resolveComponent: name => name, nextTick: fn => fn(),
  }
  const view = makeConfig(Vue, (_vue, node, ctx) => {
    if (node.kind === 'form') {
      updateField = ctx.setField
      return ctx.renderAccountField()
    }
    return { type: node.kind }
  }, 'Support', id).setup({ api }, { emit: (...args) => emitted.push(args) })
  await mounted()
  function flatten(node) {
    if (!node || typeof node !== 'object') return []
    if (Array.isArray(node)) return node.flatMap(flatten)
    return [node, ...(Array.isArray(node.children) ? node.children.flatMap(flatten) : [])]
  }
  function button(label) { return flatten(view()).find(node => node.type === 'VBtn' && node.children === label) }
  return { calls, emitted, button, find: type=>flatten(view()).find(node=>node.type===type),
    edit: (key, value) => { view(); updateField(key, value) },
    external: () => { persisted = { ...persisted, unrelated_option: 'changed' } } }
}

test('all missing requirements appear together, invalid clicks cannot save and disabling still works', async () => {
  const config = { ...full, fusion_execution_legacy_isolated: false, fusion_execution_storage_verified: false }
  assert.deepEqual(Array.from(draftIssues(config), x => x.key), ['fusion_execution_legacy_isolated', 'fusion_execution_storage_verified'])
  const ui = await open(config)
  assert.equal(ui.button('保存').props.disabled, true)
  await ui.button('保存').props.onClick()
  assert.equal(ui.calls.length, 1)
  ui.edit('fusion_execution_enabled', false)
  await ui.button('保存').props.onClick()
  assert.equal(ui.emitted[0][0], 'layout')
  assert.equal(ui.emitted[0][1].maxWidth,'80rem')
  assert.equal(ui.emitted[1][0], 'save')
  assert.equal(ui.emitted[1][1].unrelated_option, 'preserve-me')
})

test('an existing binding is reused without reading users, and opening or cancelling the picker preserves it', async () => {
  const config = {...full,fusion_execution_enabled:false}
  const ui = await open(config,null,async server=>{
    assert.equal(server,'Test')
    return {success:true,users:[{name:'Test viewer',id:'a'.repeat(32)}]}
  })
  assert.deepEqual(ui.calls,[['GET','plugin/form/'+id]])
  assert.ok(ui.button('更换 Emby 账号'))
  await ui.button('更换 Emby 账号').props.onClick()
  assert.equal(ui.find('VSelect').props.modelValue,config.fusion_user_id)
  await ui.button('保存').props.onClick()
  assert.deepEqual(JSON.parse(JSON.stringify(ui.emitted.at(-1))), ['save',config])
})

test('changing server discards a pending directory, invalidates its selector and requires a new explicit scope', async () => {
  const firstId='a'.repeat(32), secondId='b'.repeat(32)
  let resolveFirst
  const oldDirectory = new Promise(resolve=>{resolveFirst=resolve})
  const ui = await open({...full,fusion_execution_enabled:false},null,
    server=>server==='Test' ? oldDirectory : Promise.resolve({success:true,users:[{name:'Second viewer',id:secondId}]}))
  const pending=ui.button('更换 Emby 账号').props.onClick()
  ui.edit('fusion_server_ref','Other')
  await ui.button('选择 Emby 账号').props.onClick()
  resolveFirst({success:true,users:[{name:'Old viewer',id:firstId}]})
  await pending
  const select=ui.find('VSelect')
  assert.equal(select.props.items.length,1)
  assert.equal(select.props.items[0].value,secondId)
  assert.equal(select.props.modelValue,null)
  select.props['onUpdate:modelValue'](secondId)
  assert.equal(ui.find('VSelect').props.modelValue,secondId)
  await ui.button('保存').props.onClick()
  const saved=ui.emitted.at(-1)[1]
  assert.equal(saved.fusion_user_id,secondId)
  assert.equal(saved.fusion_library_mode,'unselected')
  assert.equal(saved.fusion_library_ids,'')
  assert.equal(saved.unrelated_option,'preserve-me')
})

test('an inaccessible or empty directory leaves the ID intact and permits manual entry', async () => {
  for (const response of [{success:false},{success:true,users:[]}]) {
    const ui=await open({...full,fusion_execution_enabled:false},null,()=>Promise.resolve(response))
    await ui.button('更换 Emby 账号').props.onClick()
    assert.ok(ui.button('重新读取账号'))
    ui.button('手动填写 ID').props.onClick()
    const input=ui.find('VTextField')
    assert.equal(input.props.modelValue,full.fusion_user_id)
    input.props['onUpdate:modelValue']('d'.repeat(32))
    await ui.button('保存').props.onClick()
    const saved=ui.emitted.at(-1)[1]
    assert.equal(saved.fusion_user_id,'d'.repeat(32))
    assert.equal(saved.fusion_library_mode,'unselected')
    assert.equal(saved.fusion_execution_enabled,false)
  }
})

test('the tonight shortcut guides each missing binding step without enabling or granting automatically', async () => {
  vaLeaveBoard({target:'tonight'})
  const ui=await open({...full,fusion_enabled:false,fusion_server_ref:'',fusion_user_id:'',
    fusion_library_mode:'unselected',fusion_execution_enabled:false})
  assert.ok(ui.button('个人选片 · 打开观影偏好更新'))
  assert.equal(ui.button('保存并验证').props.disabled,true)
  ui.edit('fusion_enabled',true)
  assert.ok(ui.button('个人选片 · 选择 Emby 服务器'))
  ui.edit('fusion_server_ref','Test')
  assert.ok(ui.button('个人选片 · 选择 Emby 账号'))
  ui.edit('fusion_user_id','e'.repeat(32))
  assert.ok(ui.button('个人选片 · 选择授权媒体库'))
  ui.edit('fusion_library_mode','all_visible')
  assert.equal(ui.button('保存并验证').props.disabled,false)
  assert.deepEqual(ui.calls,[['GET','plugin/form/'+id]])
  await ui.button('保存并验证').props.onClick()
  assert.deepEqual(ui.calls.map(call=>call.slice(0,2)), [
    ['GET','plugin/form/'+id], ['GET','plugin/form/'+id], ['PUT',base], ['POST',base+'/fusion/validate']
  ])
  assert.equal(ui.emitted.at(-1)[0],'switch')
})

test('the ranked shortcut prepares a visible draft and does not save or grant on open', async () => {
  assert.equal(draftIssues({...full, fusion_execution_daily_limit:5}).length, 0)
  for (const daily of [0, 4, 5, '3.0', true])
    assert.ok(draftIssues({...full, fusion_execution_preset:'ranked', fusion_execution_daily_limit:daily})
      .some(issue => issue.key === 'fusion_execution_daily_limit'))
  vaLeaveBoard({target:'ranked'})
  const ui = await open({...full, fusion_execution_daily_limit:5})
  assert.deepEqual(ui.calls.map(call=>call[0]), ['GET'])
  await ui.button('保存').props.onClick()
  const update = ui.calls.find(call=>call[0] === 'PUT')
  assert.equal(update[2].fusion_execution_preset,'ranked')
  assert.equal(update[2].fusion_execution_daily_limit,3)
  assert.ok(!ui.calls.some(call=>call[1] === scope+'authorize'))
})

test('saved settings get a separately reviewable digest, and only the explicit grant posts it', async () => {
  const ui = await open(full)
  ui.edit('fusion_execution_daily_limit', 3)
  await ui.button('保存').props.onClick()
  assert.deepEqual(ui.calls.map(call => call.slice(0, 2)), [
    ['GET', 'plugin/form/' + id], ['POST', scope + 'preflight'], ['GET', 'plugin/form/' + id],
    ['PUT', base], ['GET', scope + 'review'],
  ])
  assert.equal(ui.calls[1][2].unrelated_option, undefined)
  assert.equal(ui.calls[3][2].unrelated_option, 'preserve-me')
  const digest = 'a'.repeat(64)
  const grant = () => ui.button('确认范围并授权')
  assert.equal(grant().props.disabled,true)
  await grant().props.onClick({detail:1})
  await new Promise(r=>setTimeout(r,565))
  assert.equal(grant().props.disabled,false)
  await grant().props.onClick({detail:2})
  await grant().props.onClick({repeat:true})
  assert.equal(ui.calls.length,5)
  const staleGrant=grant().props.onClick
  await grant().props.onClick({detail:1})
  assert.equal(ui.calls[5][1], scope + 'authorize')
  assert.equal(ui.calls[5][2].expected_digest, digest)
  assert.equal(ui.calls[6][1], scope + 'review')
  await staleGrant({detail:1})
  assert.equal(ui.calls.length,7)
  assert.ok(ui.button('撤销自动订阅授权'))
  assert.deepEqual(ui.emitted.map(e=>e[0]), ['layout'])
})

test('review can be declined or edited; blocked and unbound scopes have no footer grant', async () => {
  for (const fault of ['blocked','digest']) {
    const ui = await open(full,fault)
    await ui.button('保存').props.onClick()
    assert.equal(ui.button('确认范围并授权'),undefined)
    assert.ok(ui.button('完成'))
    ui.button('设置').props.onClick()
    assert.ok(ui.button('保存'))
  }
  const ui = await open(full)
  await ui.button('保存').props.onClick()
  ui.button('暂不授权').props.onClick()
  assert.deepEqual(ui.emitted.map(v=>v[0]),['layout','close'])
  assert.ok(!ui.calls.some(call=>call[1]===scope+'authorize'))
})

test('return to data restores the previous shelf only after leaving a clean draft', async () => {
  const bookmark={kind:'tv',period:'week',page:2,scroll:624}
  vaLeaveBoard(bookmark)
  const ui=await open(full)
  ui.edit('fusion_execution_daily_limit',3)
  ui.button('查看数据').props.onClick()
  assert.deepEqual(ui.emitted.map(e=>e[0]),['layout'])
  assert.equal(vaTakeHandoff('page'),null)
  ui.edit('fusion_execution_daily_limit',1)
  ui.button('查看数据').props.onClick()
  assert.equal(ui.emitted[1][0],'switch')
  assert.equal(vaTakeHandoff('page'),bookmark)
  assert.equal(vaTakeHandoff('page'),null)
})

test('failed check, changed config, failed save and unconfirmed review cannot authorize', async () => {
  for (const fault of ['check', 'conflict', 'save', 'review']) {
    const ui = await open(full, fault)
    ui.edit('fusion_execution_daily_limit', 2)
    if (fault === 'conflict') ui.external()
    await ui.button('保存').props.onClick()
    if (fault === 'conflict' || fault === 'check') assert.ok(!ui.calls.some(x => x[0] === 'PUT'))
    assert.ok(!ui.calls.some(x => x[1] === scope + 'authorize'))
    if (fault === 'review') assert.ok(ui.button('刷新授权状态'))
    else assert.ok(!ui.button('完成'))
  }
})
