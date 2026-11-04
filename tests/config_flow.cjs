// Exercise the settings component's actual click, API and host event contract.
const assert = require('node:assert/strict')
const { readFileSync } = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const { test } = require('node:test')
const script = ['shell','config'].map(name=>readFileSync(path.join(__dirname, '../frontend/'+name+'.js'), 'utf8')).join('\n')
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
async function open(config, failAt) {
  const calls = [], emitted = []
  let persisted = structuredClone(config), mounted, updateField, armed = false
  const api = {
    async get(url) {
      calls.push(['GET', url])
      if (url === 'plugin/form/' + id) return { conf: [{ kind: 'form' }], model: persisted, render_mode: 'vue' }
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
    if (node.kind === 'form') updateField = ctx.setField
    return { type: node.kind }
  }, 'Support', id).setup({ api }, { emit: (...args) => emitted.push(args) })
  await mounted()
  function flatten(node) {
    if (!node || typeof node !== 'object') return []
    if (Array.isArray(node)) return node.flatMap(flatten)
    return [node, ...(Array.isArray(node.children) ? node.children.flatMap(flatten) : [])]
  }
  function button(label) { return flatten(view()).find(node => node.type === 'VBtn' && node.children === label) }
  return { calls, emitted, button, edit: (key, value) => { view(); updateField(key, value) },
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
