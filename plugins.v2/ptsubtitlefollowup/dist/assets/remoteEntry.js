// MoviePilot Viewing Assistant | Board-King
// Shared chrome keeps navigation and the current action in the same place as
// the host replaces the data dialog with its configuration dialog.
let vaHandoff = null
function vaLeaveBoard(position) {
  vaHandoff = {next:'config',position,time:Date.now()}
}
function vaReturnToBoard(position) {
  vaHandoff = position ? {next:'page',position,time:Date.now()} : null
}
function vaClearHandoff() { vaHandoff = null }
function vaTakeHandoff(view) {
  const saved = vaHandoff
  vaClearHandoff()
  return saved?.next === view && Date.now() - saved.time < 20 * 60 * 1000 ? saved.position : null
}

const vaShellStyle = `
.va-shell{display:flex;flex-direction:column;min-height:0;height:min(54rem,calc(100dvh - 48px));max-height:calc(100dvh - 48px)}
.v-dialog--fullscreen .va-shell{height:100dvh;max-height:100dvh}
.va-toolbar,.va-header{flex:0 0 auto;display:flex;align-items:center;justify-content:space-between;gap:8px;background:rgb(var(--v-theme-surface))}
.va-header{padding:8px 16px;border-bottom:1px solid rgba(var(--v-theme-on-surface),.1);min-height:48px}
.va-shell .va-scroll{flex:1 1 auto;min-height:0;overflow:auto;overscroll-behavior:contain}
.va-toolbar{padding:8px 16px max(8px,env(safe-area-inset-bottom));border-top:1px solid rgba(var(--v-theme-on-surface),.12)}
.va-toolbar nav,.va-toolbar .va-actions{display:flex;align-items:center;gap:4px}
.va-toolbar .va-actions{justify-content:flex-end;flex-wrap:wrap}
.va-shell button:not(:disabled),.va-shell .va-reason-trigger,.va-shell summary{transition:transform 230ms cubic-bezier(.2,1.65,.4,1),background-color 140ms ease}
.va-shell button:not(:disabled):active,.va-shell summary:active{transform:scale(.94);transition:transform 65ms ease-out}
.va-shell :is(button,a,summary):focus-visible{outline:2px solid rgb(var(--v-theme-primary));outline-offset:2px}
.va-toolbar .v-btn{letter-spacing:normal;min-width:0;min-height:36px}
@media(pointer:coarse){.va-toolbar .v-btn,.va-header button{min-height:44px}}
@media(max-width:500px){.va-toolbar{padding-inline:8px;gap:3px}.va-toolbar nav,.va-toolbar .va-actions{gap:2px}.va-toolbar .v-btn{padding-inline:8px}}
@media(prefers-reduced-motion:reduce){.va-shell button:not(:disabled),.va-shell .va-reason-trigger,.va-shell summary{transition:none!important;transform:none!important}}
`

function vaHeader(Vue, title, onClose, busy) {
  const h = Vue.h
  return [h('style', null, vaShellStyle),
    h('header',{class:'va-header'},[
      h('h2',{class:'text-h6'},title),
      h('button',{type:'button',class:'v-btn v-btn--icon v-btn--variant-text',title:'关闭','aria-label':'关闭',
        disabled:!!busy,onClick:onClose,style:'width:36px;height:36px;font-size:24px;line-height:1'},'×')
    ])]
}

function vaFooter(Vue, {active,api,pluginId,Support,onData,onSettings,busy,actions = []}) {
  const h = Vue.h
  const tab = (key,label,go) => h(Vue.resolveComponent('VBtn'),{
    key, size:'small', variant:active===key?'tonal':'text',color:active===key?'primary':undefined,
    'aria-current':active===key?'page':undefined, disabled:!!busy||(!go && active!==key),
    onClick:go || undefined
  },{default:()=>label})
  return h('footer',{class:'va-toolbar'},[
    h('nav',{'aria-label':'观影助手导航'},[
      h(Support,{api,pluginId}),tab('page','查看数据',onData),tab('config','设置',onSettings)
    ]),
    h('div',{class:'va-actions'},actions)
  ])
}

// Render only this plugin's own JSON components. Media labels remain Vue text.
function renderNode(Vue, node, ctx = {}) {
  if (!node || typeof node.component !== 'string') return null
  if (ctx.toolbarPrimary && node.props?.['data-va-primary'] === 'refresh-candidates') return null
  const name = node.component
  const native = /^[a-z][a-z0-9]*$/.test(name)
  if (native && !['section','article','header','footer','div','span','small','strong','p','h2','h3','img','details','summary','a','figure','ul','ol','li','button','b','time','aside','nav','dl','dt','dd'].includes(name)) return null
  if (!native && !/^V[A-Z][A-Za-z0-9]*$/.test(name)) return null
  const props = {...(node.props || {})}
  const key = props.model
  const condition = props.show
  delete props.model
  delete props.show
  if (condition) {
    const eq = /^\{\{\s*(\w+)\s*===\s*'([^']*)'\s*\}\}$/.exec(condition)
    const flag = /^\{\{\s*(\w+)\s*\}\}$/.exec(condition)
    if (!(eq ? ctx.model?.[eq[1]] === eq[2] : flag && ctx.model?.[flag[1]] === true)) return null
  }
  if (key && ctx.setField) {
    Object.assign(props, ctx.fieldProps?.(key) || {}, {
      'data-model-key': key, modelValue: ctx.model[key],
      'onUpdate:modelValue': value => ctx.setField(key, value),
    })
  }
  if (node.events?.click && ctx.onAction) {
    props.disabled = props.disabled || ctx.busy || false
    props.onClick = () => { if (!props.disabled) ctx.onAction(node.events.click) }
  }
  if (props['data-va-settings'] && ctx.onSwitch) props.onClick = ctx.onSwitch
  const children = () => [...(node.text == null ? [] : [String(node.text)]),
    ...(node.content || []).map(child => renderNode(Vue, child, ctx))]
  return Vue.h(native ? name : Vue.resolveComponent(name), props, native ? children() : {default:children})
}

// The page never starts a task on mount. Refresh/authorization need a user click.
function makePage(Vue, pluginId) {
  const Shelf = makeShelf(Vue)
  const Support = makeSupport(Vue)
  return {
    name:'ViewingAssistantPage', inheritAttrs:false,
    props:{api:{required:true}, show_switch:{type:Boolean, default:true}}, emits:['switch','close'],
    setup(props, {emit}) {
      const rows = Vue.ref(null), error = Vue.ref(''), info = Vue.ref(''), busy = Vue.ref(false)
      const scroller = Vue.ref(null)
      const resume = vaTakeHandoff('page')
      let selection = resume?.selection || {kind:'all',period:'all',page:1}
      let restoreScroll = resume?.top ?? null
      function settings() {
        if (!props.show_switch || busy.value) return
        vaLeaveBoard({selection:{...selection},top:scroller.value?.scrollTop || 0})
        emit('switch')
      }
      function shelfReady(newSelection) {
        selection = newSelection
        if (restoreScroll !== null) {
          const top = restoreScroll
          restoreScroll = null
          Vue.nextTick(() => { if (scroller.value) scroller.value.scrollTop = top })
        }
      }
      function findRefresh(node) {
        if (node.component === 'VBtn' && node.props?.['data-va-primary'] === 'refresh-candidates') return node
        for (const child of node.content || []) {
          const found = findRefresh(child)
          if (found) return found
        }
        return null
      }
      const refreshButton = Vue.computed(() => {
        for (const node of rows.value || []) {
          const found = findRefresh(node)
          if (found) return found
        }
        return null
      })
      async function load() {
        const result = await props.api.get('plugin/page/' + pluginId)
        if (!Array.isArray(result?.page) || result.render_mode !== 'vue') throw Error('invalid page')
        rows.value = result.page
      }
      async function refresh() {
        if (busy.value) return
        busy.value = true; error.value = ''; info.value = ''
        try { await load() } catch { error.value = '看板读取失败' }
        finally { busy.value = false }
      }
      const paths = {status:'GET',validate:'POST',refresh:'POST','candidates/refresh':'POST','candidates/status':'GET',
        'execution/revoke':'POST','execution/authorize':'POST'}
      async function action(event) {
        const prefix = 'plugin/' + pluginId + '/fusion/'
        if (busy.value || !event?.api?.startsWith(prefix)) return
        const op = event.api.slice(prefix.length)
        if (!Object.hasOwn(paths, op) || paths[op] !== event.method) return
        busy.value = true; error.value = ''; info.value = ''
        try {
          const result = event.method === 'POST' ? await props.api.post(event.api, event.params || {}) : await props.api.get(event.api)
          if (!result || result.success === false) error.value = result?.message || '操作未完成，请刷新状态'
          else if (event.method === 'POST' && result.message) info.value = result.message
          await load()
        } catch { error.value = '结果未确认，请刷新状态' }
        finally { busy.value = false }
      }
      Vue.onMounted(refresh)
      return () => {
        const h = Vue.h, btn = (label, handler, attrs = {}) => h(Vue.resolveComponent('VBtn'), {
          size:'small', variant:'text', onClick:handler, ...attrs
        }, {default:()=>label})
        const primary = refreshButton.value
        return h('section', {class:'va-page va-shell', 'aria-label':'观影助手'}, [
          vaHeader(Vue,'观影助手',()=>emit('close'),false),
          h('div',{ref:scroller,class:'va-scroll pa-4'}, [
            !rows.value && !error.value ? h('div',{role:'status',class:'py-4'},'正在读取看板…') : null,
            ...(rows.value || []).map(row => row.props?.['data-va-shelf']
              ? h(Shelf,{key:'shelf', initial:row,api:props.api,pluginId,resume:resume?.selection,onReady:shelfReady})
              : renderNode(Vue,row,{pluginId, busy:busy.value,toolbarPrimary:true,onAction:action,
                onSwitch:props.show_switch ? settings : undefined})),
          ]),
          error.value ? h('div',{role:'alert',class:'d-flex align-center ga-2 text-error px-4 py-1'},[
            error.value, btn('刷新状态',refresh,{disabled:busy.value})]) : null,
          info.value ? h('div',{role:'status',class:'text-body-2 px-4 py-1'},info.value) : null,
          vaFooter(Vue,{active:'page',api:props.api,pluginId,Support,
            onSettings:props.show_switch ? settings : null,busy:busy.value,
            actions:primary ? [renderNode(Vue,{...primary,props:{...primary.props,class:'',loading:busy.value}},
              {pluginId,busy:busy.value,onAction:action})] : []})
        ])
      }
    }
  }
}

function makeShelf(Vue) {
  const {h, ref, computed, resolveComponent, watch, onUnmounted} = Vue
  return {
    name:'ViewingAssistantShelf',
    props:['initial','api','pluginId','resume'], emits:['ready'],
    setup(props,{emit}) {
      const kind = ref(props.resume?.kind || 'all'), period = ref(props.resume?.period || 'all'), page = ref(props.resume?.page || 1)
      const view = ref(null), busy = ref(false), failed = ref(false), heading = ref(null)
      const reason = ref(null), dialog = ref(null), dismiss = ref(null)
      let opener = null
      const grid = computed(() => props.initial.content[1].content[0])
      const total = computed(() => view.value?.total ?? Number(props.initial.props['data-total']))
      const pages = computed(() => view.value?.pages ?? Math.max(1,Math.ceil(total.value / props.initial.props['data-size'])))
      let generation = 0
      const ready = () => emit('ready',{kind:kind.value,period:period.value,page:page.value})
      onUnmounted(() => { generation++ })
      async function read() {
        const stamp = ++generation
        failed.value = false
        if (kind.value === 'all' && period.value === 'all' && page.value === 1) {
          view.value = null; busy.value = false
          ready()
          return
        }
        busy.value = true
        try {
          const result = await props.api.get('plugin/' + props.pluginId + '/fusion/candidates/browse',
            {params:{kind:kind.value, period:period.value, page:page.value}})
          if (stamp !== generation) return
          if (result?.success !== true || !Array.isArray(result.cards) || !Number.isInteger(result.page) ||
              !Number.isInteger(result.pages) || !Number.isInteger(result.total)) throw Error()
          view.value = result
          page.value = result.page
          ready()
        } catch { if (stamp === generation) failed.value = true }
        finally { if (stamp === generation) busy.value = false }
      }
      watch(() => props.initial, read)
      Vue.onMounted(read)
      const button = (label, action, attrs = {}) => h(resolveComponent('VBtn'), {
        size:'small', variant:'text', onClick:action, ...attrs
      }, () => label)
      async function choose(nextKind, nextPeriod, nextPage = 1) {
        const changedPage = nextPage !== page.value
        kind.value = nextKind; period.value = nextPeriod; page.value = nextPage
        await read()
        if (changedPage && !busy.value && !failed.value) {
          await Vue.nextTick()
          heading.value?.focus({preventScroll:true})
          heading.value?.scrollIntoView({block:'start'})
        }
      }
      async function explain(node, event) {
        opener = event.currentTarget
        reason.value = node
        await Vue.nextTick()
        dialog.value?.showModal()
        dismiss.value?.focus({preventScroll:true})
      }
      function closeReason() {
        if (dialog.value?.open) dialog.value.close()
      }
      function restoreFocus() {
        reason.value = null
        const target = opener?.isConnected ? opener : heading.value
        opener = null
        Vue.nextTick(() => target?.focus({preventScroll:true}))
      }
      const icon = (path, size) => h('svg', {width:size,height:size,viewBox:'0 0 24 24',fill:'none',
        stroke:'currentColor','stroke-width':1.8,'stroke-linecap':'round','stroke-linejoin':'round','aria-hidden':'true'},[h('path',{d:path})])
      function renderCard(card) {
        if (!Array.isArray(card?.content) || !card.content.some(child=>child?.props?.['data-va-reason'])) return renderNode(Vue,card)
        return h('div',card.props,card.content.map(child=>{
          if (!child?.props?.['data-va-reason']) return renderNode(Vue,child)
          const summary = child.content[0]
          return h('button',{type:'button',class:'va-reason-trigger text-caption text-primary',
            'aria-label':summary.props['aria-label'],'aria-haspopup':'dialog',onClick:e=>explain(child,e),
            style:'display:inline-flex;align-items:center;gap:4px;min-height:32px;max-width:100%;text-align:start'},[
            icon('M4 18v-4m6 4V9m6 9V5',14),
            h('span',{style:'overflow:hidden;text-overflow:ellipsis;white-space:nowrap'},summary.text)
          ])
        }))
      }
      function reasonDialog() {
        const title = reason.value?.props['data-va-reason']
        return h('dialog',{ref:dialog,class:'va-reason-dialog','aria-label':title ? title+'的推荐依据' : '推荐依据',
          onClose:restoreFocus,onCancel:e=>{e.preventDefault();e.stopPropagation();closeReason()},
          onKeydown:e=>{if(e.key==='Escape'){e.preventDefault();e.stopPropagation();closeReason()}},
          onClick:e=>{if(e.target!==dialog.value) return
            const r = dialog.value.getBoundingClientRect()
            if(e.clientX<r.left || e.clientX>r.right || e.clientY<r.top || e.clientY>r.bottom) closeReason()
          },style:'position:fixed;inset:0;margin:auto;width:min(460px,calc(100% - 24px));max-height:calc(100% - 32px);overflow:auto;overscroll-behavior:contain;border:1px solid rgba(var(--v-theme-on-surface),.10);border-radius:18px;padding:0;background:rgb(var(--v-theme-surface));color:rgb(var(--v-theme-on-surface));box-shadow:0 16px 64px #12132238;text-align:start'
        },title ? [
          h('header',{class:'d-flex align-center justify-space-between ga-3',style:'padding:14px 18px;border-bottom:1px solid rgba(var(--v-theme-on-surface),.10)'},[
            h('h2',{class:'text-h6',style:'overflow-wrap:anywhere;min-width:0'},title),
            h('button',{type:'button',ref:dismiss,onClick:closeReason,'aria-label':'关闭推荐依据',title:'关闭推荐依据',
              style:'flex:none;display:grid;place-items:center;width:40px;height:40px;border-radius:50%;background:rgba(var(--v-theme-on-surface),.055)'},[icon('M6 6l12 12M18 6 6 18',18)])
          ]),h('div',{style:'padding:6px 18px 12px'},[renderNode(Vue,reason.value.content[1])])
        ]:[])
      }
      return () => h('section',{class:'rounded-lg border px-4 py-4 mb-4','aria-label':'候选片单'},[
        h('div',{class:'d-flex flex-wrap align-center justify-space-between ga-2 mb-3'}, [
          h('h2',{ref:heading,tabindex:-1,class:'text-subtitle-1 font-weight-bold',style:'scroll-margin-block-start:64px'},'候选片单'),
          h('div',{class:'d-flex flex-wrap ga-1',role:'group','aria-label':'发现时间'},[
            button('近 7 天新发现',()=>choose(kind.value,period.value === 'week' ? 'all':'week'),{
              'aria-pressed':period.value === 'week',color:period.value === 'week' ? 'primary':undefined,
              variant:period.value === 'week'?'tonal':'outlined', title:'仅看过去 7 天首次进入当前候选池的作品'})
          ])
        ]),
        h('div',{class:'d-flex align-center flex-wrap ga-1 mb-4',role:'group','aria-label':'影片类型'},[
          ...[['all','全部'],['movie','电影'],['tv','剧集']].map(([value,label])=>button(label,()=>choose(value,period.value),{
            'aria-pressed':kind.value===value,color:kind.value===value?'primary':undefined,
            variant:kind.value===value?'tonal':'text'})),
          !busy.value && !failed.value ? h('span',{class:'ms-auto text-caption text-medium-emphasis'},total.value+' 部') : null
        ]),
        busy.value ? h('p',{role:'status',class:'text-medium-emphasis py-3'},'正在读取片单…') :
        failed.value ? h('div',{role:'alert'},['片单暂时无法读取。',button('重试',read)]) :
        !total.value ? h('div',{class:'py-4'},['这个范围暂时没有候选。',button('查看全部',()=>choose('all','all'))]) :
        h('div',grid.value.props,(view.value?.cards ?? grid.value.content).map(renderCard)),
        !busy.value && !failed.value && pages.value > 1 ? h('nav',{'aria-label':'候选翻页',class:'d-flex align-center justify-center ga-2 mt-4'},[
          button('上一页',()=>choose(kind.value,period.value,page.value-1),{disabled:page.value<=1}),
          h('span',{class:'text-caption text-medium-emphasis','aria-live':'polite'},page.value+' / '+pages.value),
          button('下一页',()=>choose(kind.value,period.value,page.value+1),{disabled:page.value>=pages.value})
        ]) : null, reasonDialog()
      ])
    }
  }
}

// Render only the trigger until requested; do not include a bearer token in any DOM URL.
function makeSupport(Vue) {
  const { h, ref, nextTick } = Vue;
  return {
    name: 'ViewingAssistantSupport',
    props: { api: { required: true }, pluginId: { type: String, required: true } },
    setup(props) {
      const dialog = ref(null), trigger = ref(null);
      const state = ref('idle'), image = ref('');
      const route = () => 'plugin/' + encodeURIComponent(props.pluginId) + '/support';
      async function load() {
        if (state.value === 'loading') return;
        state.value = 'loading';
        try {
          const response = await props.api.get(route());
          const src = response?.image ?? response?.data?.image;
          if (typeof src !== 'string' || src.length > 700000 ||
              !/^data:image\/jpeg;base64,[A-Za-z0-9+/]+={0,2}$/.test(src)) throw Error();
          image.value = src;
          state.value = 'ready';
        } catch {
          state.value = 'error';
        }
      }
      async function open() {
        await nextTick();
        if (!dialog.value?.open) dialog.value?.showModal();
        if (state.value !== 'ready') await load();
      }
      function close() { if (dialog.value?.open) dialog.value.close(); }
      const palette = 'rgba(var(--v-theme-on-surface),.68)';
      const action = (label, onClick, extra = {}) => h('button',
        { type: 'button', onClick, class: 'v-btn v-btn--variant-text text-primary', ...extra }, label);
      const heart = () => h('svg', { width: 16, height: 16, viewBox: '0 0 24 24',
        fill: 'none', stroke: 'currentColor', 'stroke-width': 1.8, 'aria-hidden': 'true' },
        [h('path', { d: 'M12 20.1l-1.3-1.2C5.5 14.3 2.5 11.6 2.5 8.3a5.2 5.2 0 0 1 9.5-2.9 5.2 5.2 0 0 1 9.5 2.9c0 3.3-3 6-8.2 10.6z' })]);
      function supportBody() {
        return [
          h('p', { style: { color: palette, margin: '0 0 14px' } }, '谢谢支持！赞赏自愿，当前全部功能免费。'),
          h('div', { style: { textAlign: 'center' } }, [
            h('img', { src: image.value, alt: '疯狂的神父的微信赞赏码', width: 1152, height: 1152,
              style: 'display:block;width:min(100%,280px);height:auto;margin:0 auto 10px;border-radius:12px',
              onError: () => { image.value = ''; state.value = 'error'; } }),
            h('div', { style: { fontWeight: 600 } }, '打开微信「扫一扫」'),
            h('a', { href: image.value, download: '疯狂的神父-微信赞赏码.jpg', class: 'text-primary',
              style: 'display:inline-block;padding:8px' }, '保存赞赏码'),
          ]),
          h('p', { style: { color: palette, margin: '8px 0 0', fontSize: '.875rem' } },
            '建议可在微信赞赏时留言，我会看，不保证采纳。'),
        ];
      }
      return () => [
        action([heart(), h('span', '赞赏')], open, { ref: trigger, 'aria-haspopup': 'dialog', 'aria-label': '赞赏与建议',
          title: '赞赏与建议', class: 'v-btn v-btn--variant-text text-medium-emphasis',
          style: 'display:inline-flex;align-items:center;gap:4px;white-space:nowrap;min-width:auto;padding-inline:8px;font-size:.8125rem;font-weight:500' }),
        h('dialog', { ref: dialog, 'aria-label': '赞赏与建议', onClose: () => trigger.value?.focus(),
          onKeydown: e => { if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(); } },
          onClick: e => { if (e.target === dialog.value) {
            const r = dialog.value.getBoundingClientRect();
            if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) close();
          } },
          style: 'position:fixed;inset:0;margin:auto;width:min(410px,calc(100% - 24px));max-height:calc(100% - 32px);overflow:auto;overscroll-behavior:contain;border:0;border-radius:18px;padding:20px;background:rgb(var(--v-theme-surface,255 255 255));color:rgb(var(--v-theme-on-surface,32 32 40));box-shadow:0 14px 65px #0004;text-align:left;line-height:1.55',
        }, [
          h('div', { style: 'display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:10px' }, [
            h('h2', { class: 'text-h6', style: 'margin:0' }, '赞赏作者'),
            action('关闭', close, { style: 'min-width:auto;padding-inline:8px' }),
          ]),
          state.value === 'ready' ? supportBody()
            : h('div', { role: 'status' }, state.value === 'error'
              ? ['赞赏码未能加载。', action('重试', load)] : '正在载入…'),
        ]),
      ];
    },
  };
}

// MoviePilot owns loading and persistence; the plugin owns when its draft may be saved.
function draftIssues(draft) {
  if (draft.fusion_execution_enabled !== true) return []
  const issues = []
  const need = (ok, key, text) => { if (!ok) issues.push({ key, text }) }
  const source = 'tmdb_popular_movie'
  const plain = (value, limit) => typeof value === 'string' && value.trim() === value &&
    value.length > 0 && Array.from(value).length <= limit && !/[\p{C}\p{Z}]/u.test(value.replace(/ /g, ''))
  need(draft.fusion_enabled === true, 'fusion_enabled', '打开观影偏好更新')
  need(plain(draft.fusion_server_ref, 200), 'fusion_server_ref', '选择 Emby 服务器')
  const id = typeof draft.fusion_user_id === 'string' ? draft.fusion_user_id.replace(/-/g, '') : ''
  need(/^[a-f0-9]{32}$/i.test(id), 'fusion_user_id', '填写 32 位 Emby 用户 ID')
  const range = draft.fusion_library_mode
  need(['selected_ids', 'all_visible'].includes(range), 'fusion_library_mode', '选择授权媒体库')
  if (range === 'selected_ids') {
    const raw = draft.fusion_library_ids
    const ids = Array.isArray(raw) ? raw : typeof raw === 'string' ? raw.trim().split(/[\s,]+/) : []
    need(ids.length > 0 && ids.length <= 100 && ids.every(x => typeof x === 'string' && /^[A-Za-z0-9_-]{1,80}$/.test(x)),
      'fusion_library_ids', '填写有效的媒体库 ID')
  }
  need(draft.fusion_discovery_enabled === true, 'fusion_discovery_enabled', '打开候选更新')
  need(Array.isArray(draft.fusion_discovery_sources) && draft.fusion_discovery_sources.includes(source),
    'fusion_discovery_sources', '候选来源加入 TMDB 热门电影')
  need(plain(draft.fusion_execution_owner, 128), 'fusion_execution_owner', '填写 MoviePilot 订阅用户名')
  const sources = draft.fusion_execution_sources
  need(Array.isArray(sources) && sources.length === 1 && sources[0] === source,
    'fusion_execution_sources', '选择自动订阅来源')
  need(['conservative', 'balanced'].includes(draft.fusion_execution_preset), 'fusion_execution_preset', '选择订阅预设')
  need(['string', 'number'].includes(typeof draft.fusion_execution_daily_limit) &&
    /^[1-5]$/.test(String(draft.fusion_execution_daily_limit)), 'fusion_execution_daily_limit', '每日上限需为 1–5 部')
  need(draft.fusion_execution_legacy_isolated === true, 'fusion_execution_legacy_isolated', '确认同来源旧订阅已停用')
  need(draft.fusion_execution_storage_verified === true, 'fusion_execution_storage_verified', '确认下载目录的存储映射')
  return issues
}

// Only a single scoped action returned by the current review may leave the read-only card.
function vaReviewScope(nodes, path) {
  let selected = null
  function pick(node) {
    if (!node || typeof node !== 'object') return node
    if (node.props?.['data-va-execution-review-action'] && Array.isArray(node.content)) {
      const buttons = node.content.filter(n => n?.component === 'VBtn')
      const b = buttons[0], event = b?.events?.click
      const grant = event?.api === path + '/fusion/execution/authorize'
      if (!selected && buttons.length === 1 && b.props?.disabled !== true && event?.method === 'POST' &&
          (grant ? typeof event.params?.expected_digest === 'string' && /^[a-f0-9]{64}$/.test(event.params.expected_digest)
                 : event.api === path + '/fusion/execution/revoke')) {
        selected = {grant, event, note:node.content.filter(n => n !== b)}
        return null
      }
    }
    return Array.isArray(node.content) ? {...node,content:node.content.map(pick).filter(Boolean)} : node
  }
  const scope = Array.isArray(nodes) ? nodes.map(pick).filter(Boolean) : []
  return {scope,selected}
}

function makeConfig(Vue, renderNode, Support, pluginId) {
  const { h, ref, computed, onMounted, onUnmounted, nextTick, resolveComponent } = Vue
  const copy = value => JSON.parse(JSON.stringify(value))
  const resultText = {
    INCOMPATIBLE: ['fusion_execution_owner', '未能核实这个订阅用户或当前 MoviePilot 版本。请检查用户名与宿主版本。'],
    SERVER_UNAVAILABLE: ['fusion_server_ref', '所选 Emby 连接已不可用，请重新选择。'],
    SPACE_LOW: ['fusion_execution_storage_verified', '电影下载目录可用空间低于 100 GiB。'],
    SPACE_UNVERIFIED: ['fusion_execution_storage_verified', 'MoviePilot 无法核实电影下载路径与存储，请检查目录和下载器配置。'],
  }
  const validId = typeof pluginId === 'string' && /^[A-Za-z][A-Za-z0-9_]{0,127}$/.test(pluginId)
  return {
    name: 'ViewingAssistantConfig', inheritAttrs: false, props: ['api', 'initialConfig'], emits: ['save', 'close', 'switch','layout'],
    setup(props, { emit }) {
      const boardBookmark = vaTakeHandoff('config')
      const draft = ref({}), form = ref([]), loaded = ref(false), busy = ref(false)
      const message = ref(''), serverIssue = ref(null), review = ref(null), saved = ref(false), conflict = ref(false)
      const container = ref(null)
      const scopeRegion = ref(null), deliberate = ref(false)
      let alive = true, original = '', guardTimer
      onUnmounted(() => { alive = false; clearTimeout(guardTimer) })
      const issues = computed(() => [...draftIssues(draft.value), ...(serverIssue.value ? [serverIssue.value] : [])])
      const path = 'plugin/' + pluginId
      const presented = computed(() => vaReviewScope(review.value, path))
      const textButton = (text, click, options = {}) => h(resolveComponent('VBtn'),
        { variant: 'text', size: 'small', ...options, onClick: click }, () => text)
      const setField = (key, value) => {
        if (busy.value || saved.value) return
        draft.value[key] = value
        serverIssue.value = null
        message.value = ''
      }
      function focusField(key) {
        const field = container.value?.querySelector('[data-model-key="' + key + '"]')
        if (!field) return
        for (let parent = field.parentElement; parent && parent !== container.value; parent = parent.parentElement)
          if (parent.tagName === 'DETAILS') parent.open = true
        nextTick(() => {
          field.scrollIntoView({ block: 'center' })
          const control = field.matches('input,textarea,button') ? field :
            field.querySelector('input:not([type=hidden]), textarea, [role=combobox], button')
          control?.focus({ preventScroll: true })
        })
      }
      async function load() {
        if (busy.value) return
        busy.value = true
        message.value = ''
        try {
          if (!validId) throw Error()
          const response = await props.api.get('plugin/form/' + pluginId)
          if (response?.render_mode !== 'vue' || !Array.isArray(response.conf) || !response.conf.length || !response.model ||
              typeof response.model !== 'object' || Array.isArray(response.model)) throw Error()
          if (!alive) return
          form.value = response.conf
          draft.value = copy(response.model)
          original = JSON.stringify(draft.value)
          loaded.value = true
          conflict.value = false
          serverIssue.value = null
        } catch { if (alive) message.value = '无法读取插件设置。' }
        finally { if (alive) busy.value = false }
      }
      onMounted(() => { emit('layout', {maxWidth:'80rem'}); return load() })
      async function fetchReview() {
        const response = await props.api.get(path + '/fusion/execution/review')
        if (!Array.isArray(response?.page) || !response.page.length) throw Error()
        if (alive) {
          review.value = response.page
          deliberate.value = false
          clearTimeout(guardTimer)
          // A double-click or a held Enter on Save must not carry over to the new action.
          guardTimer = setTimeout(() => { if (alive) deliberate.value = true },550)
          nextTick(() => {
            scopeRegion.value?.focus({preventScroll:true})
            scopeRegion.value?.scrollIntoView({block:'start'})
          })
        }
      }
      async function readSaved() {
        if (busy.value) return
        busy.value = true
        message.value = ''
        try { await fetchReview() }
        catch { if (alive) message.value = '设置已保存，暂时无法读取授权范围。' }
        finally { if (alive) busy.value = false }
      }
      async function save() {
        if (!alive || !loaded.value || busy.value || saved.value || conflict.value || issues.value.length) return
        if (draft.value.fusion_execution_enabled !== true) {
          vaClearHandoff()
          emit('save', copy(draft.value))
          return
        }
        const snapshot = copy(draft.value)
        busy.value = true
        message.value = ''
        serverIssue.value = null
        try {
          // Send just the required draft fields, never private host config or API credentials.
          const allowed = ['fusion_enabled', 'fusion_server_ref', 'fusion_user_id', 'fusion_library_mode',
            'fusion_library_ids', 'fusion_discovery_enabled', 'fusion_discovery_sources']
          const params = Object.fromEntries(Object.entries(snapshot).filter(([key]) =>
            allowed.includes(key) || ['enabled', 'owner', 'sources', 'daily_limit', 'preset', 'legacy_isolated',
              'storage_verified'].some(name => key === 'fusion_execution_' + name)))
          const checked = await props.api.post(path + '/fusion/execution/preflight', params)
          if (!alive) return
          if (checked?.success !== true) {
            const entry = resultText[checked?.reason]
            if (entry) serverIssue.value = { key: entry[0], text: entry[1] }
            message.value = entry ? '' : '执行条件未通过检查。请核对自动订阅设置后重试。'
            return
          }
          if (JSON.stringify(snapshot) !== original) {
            const latest = await props.api.get('plugin/form/' + pluginId)
            if (!alive) return
            if (!latest?.model || JSON.stringify(latest.model) !== original) {
              conflict.value = true
              message.value = '设置已在其他窗口更改。请重新读取后再编辑。'
              return
            }
            const result = await props.api.put(path, snapshot)
            if (result?.success !== true) throw Error()
            if (!alive) return
            original = JSON.stringify(snapshot)
          }
          saved.value = true
          await fetchReview()
        } catch {
          if (alive) message.value = saved.value ? '设置已保存，暂时无法读取授权范围。' :
            '未收到保存确认。请检查连接后重试。'
        } finally { if (alive) busy.value = false }
      }
      async function act(intent, click) {
        // Include the exact displayed action; a stale button cannot act on a replacement scope.
        if (!saved.value || busy.value || !intent || intent.event !== presented.value.selected?.event ||
            click?.detail > 1 || click?.repeat || (intent.grant && !deliberate.value)) return
        const action = intent.event
        const authorize = action.api === path + '/fusion/execution/authorize'
        if (!authorize && action.api !== path + '/fusion/execution/revoke') return
        const digest = action.params?.expected_digest
        if (authorize && !(typeof digest === 'string' && /^[a-f0-9]{64}$/.test(digest))) return
        busy.value = true
        review.value = null; deliberate.value = false; clearTimeout(guardTimer)
        try {
          const result = await props.api.post(action.api, authorize ? { expected_digest: digest } : {})
          if (!alive) return
          message.value = result?.success === true ? (authorize ? '自动订阅已授权。' : '已撤销自动订阅授权。') :
            '操作未获确认，请核对最新状态。'
          await fetchReview()
        } catch { if (alive) message.value = '暂时无法确认操作结果，请先刷新授权状态。' }
        finally { if (alive) busy.value = false }
      }
      const fieldProps = key => {
        const issue = issues.value.find(item => item.key === key)
        return { disabled: busy.value, ...(issue ? { 'error-messages': issue.text } : {}) }
      }
      function switchPage() {
        if (!loaded.value || busy.value) return
        if (!saved.value && JSON.stringify(draft.value) !== original) {
          message.value = '请先保存修改，再查看数据。'
          return
        }
        vaReturnToBoard(boardBookmark); emit('switch')
      }
      function close() { if (!busy.value) { vaClearHandoff(); emit('close') } }
      function edit() {
        if (busy.value) return
        saved.value = false; review.value = null; message.value = ''; deliberate.value = false; clearTimeout(guardTimer)
      }
      function footerActions() {
        if (!saved.value) return [textButton('保存', save,{key:'draft-save',color:'primary',variant:'flat',
          loading:busy.value,disabled:!loaded.value || busy.value || conflict.value || issues.value.length>0})]
        const intent = presented.value.selected
        const actions = [textButton(intent?.grant?'暂不授权':'完成',close,{key:'review-done',disabled:busy.value})]
        if (intent) actions.push(textButton(intent.grant?'确认范围并授权':'撤销自动订阅授权',e=>act(intent,e),{
          key:intent.grant?'review-grant':'review-revoke',color:intent.grant?'primary':'error',variant:intent.grant?'flat':'tonal',
          disabled:busy.value || (intent.grant && !deliberate.value),'aria-describedby':'va-scope-effect',
          onKeydown:e=>{if(e.repeat && (e.key==='Enter' || e.key===' ')){e.preventDefault();e.stopPropagation()}}
        }))
        return actions
      }
      return () => h('section', { ref: container, class: 'va-shell va-config' }, [
        vaHeader(Vue,'观影助手设置',close,busy.value),
        h('div', { class: 'pa-4 va-scroll' }, [
          !loaded.value && !message.value ? h('p', { role: 'status' }, '正在读取设置…') : null,
          !loaded.value && message.value ? textButton('重试', load, { disabled: busy.value }) : null,
          conflict.value ? textButton('重新读取设置', load, { disabled: busy.value }) : null,
          ...(loaded.value && !saved.value ? form.value.map(node => renderNode(Vue, node,
            { model: draft.value, setField, fieldProps, pluginId })) : []),
          saved.value && review.value ? h('section',{ref:scopeRegion,tabindex:-1,'aria-label':'已保存的自动订阅范围'},
            presented.value.scope.map(node=>renderNode(Vue,node,{pluginId}))) : null,
          saved.value && !review.value && !busy.value ? textButton('刷新授权状态', readSaved) : null,
        ]),
        message.value ? h('p', { role:'status',class:'text-body-2 px-4 py-2 border-t',style:'flex:none' }, message.value) : null,
        loaded.value && !saved.value && issues.value.length ? h('div', {
          class: 'px-4 py-2 border-t', style: 'flex:0 0 auto;max-height:24dvh;overflow:auto', 'aria-live': 'polite',
        }, [
          h('div', { class: 'text-subtitle-2 mb-1' }, '自动订阅还需完成 ' + issues.value.length + ' 项'),
          h('div', { class: 'd-flex flex-wrap ga-1' }, issues.value.map(item =>
            textButton(item.text, () => focusField(item.key), { color: 'primary', disabled: busy.value }))),
        ]) : null,
        saved.value && presented.value.selected ? h('div',{id:'va-scope-effect',class:'px-4 py-2 border-t',style:'flex:none'},
          presented.value.selected.note.map(node=>renderNode(Vue,node,{pluginId}))) : null,
        vaFooter(Vue,{active:'config',api:props.api,pluginId,Support,onData:loaded.value?switchPage:null,
          onSettings:saved.value?edit:null,busy:busy.value,actions:footerActions()}),
      ])
    },
  }
}

// The host supplies its own Vue instance through the Vite federation contract.
// No second Vue runtime, external chunks, source maps, credentials or CDN.
let sharedVue
export function init(scope) {
  const providers = Object.values(scope?.vue || {})
  const provider = providers.find(item => item && typeof item.get === 'function')
  if (provider) sharedVue = provider
}
export async function get(name) {
  if (!['./Page','./Config'].includes(name) || !sharedVue) throw Error('无法载入观影助手组件')
  const factory = await sharedVue.get()
  const module = await factory()
  const Vue = module?.h ? module : module?.default
  if (!Vue?.h || !/\/plugin\/file\/ptsubtitlefollowup\/dist\/assets\/remoteEntry\.js$/.test(new URL(import.meta.url).pathname)) {
    throw Error('无法确认观影助手的安装位置或 Vue 运行环境')
  }
  const pluginId = 'PtSubtitleFollowup'
  const component = name === './Config' ? makeConfig(Vue,renderNode,makeSupport(Vue),pluginId) : makePage(Vue,pluginId)
  return () => component
}
