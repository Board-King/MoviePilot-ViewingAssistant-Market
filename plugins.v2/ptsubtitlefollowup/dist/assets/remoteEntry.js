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
.va-page .va-scroll,.va-config .va-scroll{container-type:inline-size}
.va-shell .va-panel{min-width:0;box-shadow:none}
.va-shell .va-panel>.v-card-title{letter-spacing:normal;font-weight:600;line-height:1.5}
.va-activity-grid{display:grid;align-items:start;gap:16px;grid-template-columns:minmax(0,1fr);margin-bottom:16px}
.va-activity-grid>.va-panel{margin:0!important}
@container(min-width:48rem){.va-activity-grid:has(>:nth-child(2)){grid-template-columns:repeat(2,minmax(0,1fr))}}
.va-ex-choice,.va-sub-itemline{display:flex;align-items:baseline;flex-wrap:wrap;gap:4px 10px}
.va-ex-choice strong,.va-sub-itemline strong{font-weight:600;font-size:.9375rem;overflow-wrap:anywhere}
.va-ex-fields{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,7rem),1fr));gap:8px 16px;margin:10px 0}
.va-ex-quota{display:inline-flex;align-items:center;gap:4px;margin-inline-start:auto;line-height:1.25;font-size:.8125rem;font-variant-numeric:tabular-nums}
.va-ex-quota strong{font-size:1rem}
.va-quota-meter{display:flex;gap:3px;margin-left:4px}
.va-quota-meter>span{display:block;width:6px;height:14px;border-radius:3px;background:rgba(var(--v-theme-on-surface),.13)}
.va-quota-meter>.is-used{background:rgb(var(--v-theme-primary))}
.va-daily-budgets{display:flex;flex-wrap:wrap;gap:6px 14px;margin-inline-start:auto}
.va-daily-budgets>.va-ex-quota{margin:0}
.va-picks{list-style:none;padding:0;margin:4px 0 12px;display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,17rem),1fr));column-gap:20px}
.va-pick{min-width:0;display:flex;justify-content:space-between;align-items:center;gap:8px;padding:8px 0;border-top:1px solid rgba(var(--v-theme-on-surface),.1)}
.va-pick>div{min-width:0;overflow-wrap:anywhere}
.va-pick strong{font-size:.875rem;font-weight:600}
.va-pick-state{white-space:nowrap;font-size:.75rem;color:rgba(var(--v-theme-on-surface),.72)}
.va-pick-state[data-confirmed=true]{color:rgb(var(--v-theme-success))}
.va-sub-legend,.va-sub-track,.va-sub-pages{display:flex;align-items:center;gap:6px}
.va-sub-legend{flex-wrap:wrap}
.va-subtitles [data-kind=saved]{--va-ink:var(--v-theme-success)}
.va-subtitles [data-kind=existing]{--va-ink:var(--v-theme-primary)}
.va-subtitles [data-kind=waiting]{--va-ink:var(--v-theme-on-surface)}
.va-subtitles [data-kind=issue]{--va-ink:var(--v-theme-warning)}
.va-sub-key,.va-sub-mark,.va-sub-pages button{border:1px solid transparent;display:inline-flex;align-items:center;justify-content:center;color:inherit;cursor:pointer}
.va-sub-key{padding:4px 6px;gap:3px;border-radius:7px;font-size:.75rem;min-height:32px;white-space:nowrap}
.va-sub-key svg{color:rgb(var(--va-ink))}
.va-sub-key[aria-pressed=true]{border-color:rgba(var(--va-ink),.5);background:rgba(var(--va-ink),.09)}
.va-sub-track{isolation:isolate;position:relative;min-width:0;min-height:36px;gap:4px;margin:4px 0 10px;overflow-x:auto;padding:3px}
.va-sub-track:has(>:first-child)::before{content:'';position:absolute;z-index:-1;inset:50% 3px auto;height:2px;background:rgba(var(--v-theme-on-surface),.13)}
.va-sub-mark{flex:none;width:32px;height:32px;border-radius:50%;background:rgb(var(--v-theme-surface));color:rgb(var(--va-ink));border-color:rgba(var(--va-ink),.5)}
.va-sub-mark[aria-pressed=true]{outline:2px solid currentColor;outline-offset:1px}
.va-sub-pages{justify-content:flex-end;font-size:.75rem;font-variant-numeric:tabular-nums}
.va-sub-pages button{width:32px;height:32px;border-radius:8px;font-size:1.2rem}
.va-sub-pages button:disabled{opacity:.35;cursor:default}
.va-sub-selected{padding:8px 12px;background:rgba(var(--v-theme-on-surface),.035);border-radius:8px}
.va-more{margin-top:8px;color:rgba(var(--v-theme-on-surface),.75);font-size:.8125rem}
.va-more>summary{cursor:pointer;min-height:26px;line-height:26px}
.va-more p{margin:4px 0;overflow-wrap:anywhere}
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
  if (ctx.SubtitleRail && Array.isArray(node.subtitle_records))
    return Vue.h(ctx.SubtitleRail, {records:node.subtitle_records, enabled:node.subtitle_enabled})
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
    const eq = /^\{\{\s*(\w+)\s*(===|!==)\s*'([^']*)'\s*\}\}$/.exec(condition)
    const flag = /^\{\{\s*(\w+)\s*\}\}$/.exec(condition)
    if (eq ? (ctx.model?.[eq[1]] === eq[3]) !== (eq[2] === '===') : !flag || ctx.model?.[flag[1]] !== true) return null
  }
  if (key === 'fusion_user_id' && ctx.renderAccountField) return ctx.renderAccountField()
  if (key === 'fusion_library_ids' && ctx.libraryPicker) {
    const {fusion_server_ref:server, fusion_user_id:user} = ctx.model
    return Vue.h(ctx.libraryPicker,{key:JSON.stringify([server,user]),server,user,value:ctx.model[key],
      api:ctx.api,pluginId:ctx.pluginId,fieldProps:ctx.fieldProps(key),onChange:value=>ctx.setField(key,value)})
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
  if (props['data-va-settings'] && ctx.onSwitch) props.onClick = () => ctx.onSwitch(props['data-va-settings'])
  const children = () => [...(node.text == null ? [] : [String(node.text)]),
    ...(node.content || []).map(child => renderNode(Vue, child, ctx))]
  return Vue.h(native ? name : Vue.resolveComponent(name), props, native ? children() : {default:children})
}

// The page never starts a task on mount. Refresh/authorization need a user click.
function makePage(Vue, pluginId) {
  const Tonight = makeTonight(Vue)
  const Shelf = makeShelf(Vue)
  const SubtitleRail = makeSubtitleRail(Vue)
  const Support = makeSupport(Vue)
  return {
    name:'ViewingAssistantPage', inheritAttrs:false,
    props:{api:{required:true}, show_switch:{type:Boolean, default:true}}, emits:['switch','close'],
    setup(props, {emit}) {
      const rows = Vue.ref(null), error = Vue.ref(''), info = Vue.ref(''), busy = Vue.ref(false)
      const scroller = Vue.ref(null)
      const resume = vaTakeHandoff('page')
      const showTonight = Vue.ref(resume?.target === 'tonight')
      let selection = resume?.selection || {kind:'all',period:'all',page:1}
      let restoreScroll = resume?.top ?? null
      function settings(target) {
        if (!props.show_switch || busy.value) return
        vaLeaveBoard({selection:{...selection},top:scroller.value?.scrollTop || 0,
          target:['ranked','tonight','personal'].includes(target) ? target : undefined})
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
              : renderNode(Vue,row,{pluginId, SubtitleRail, busy:busy.value,toolbarPrimary:true,onAction:action,
                onSwitch:props.show_switch ? settings : undefined})),
            rows.value ? h('details',{class:'va-more mt-3',open:showTonight.value,
              onToggle:event=>{showTonight.value=event.currentTarget.open}},[
              h('summary','今晚看什么'),
              showTonight.value ? h(Tonight,{api:props.api,pluginId,initial:rows.value,
                onSettings:props.show_switch ? settings : undefined}) : null,
            ]) : null,
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

const vaShelfStyle = `
.va-shelf .va-reason-trigger{display:flex;flex-wrap:wrap;align-items:center;gap:2px 7px;min-height:32px;max-width:100%;text-align:start;font-variant-numeric:tabular-nums}
.va-shelf .va-match,.va-shelf .va-rating{display:inline-flex;align-items:center;gap:3px;white-space:nowrap}
.va-shelf .va-match{color:rgb(var(--v-theme-primary));font-weight:600}
.va-shelf .va-match-track{width:15px;height:4px;overflow:hidden;border-radius:4px;background:rgba(var(--v-theme-primary),.17)}
.va-shelf .va-match-track>span{display:block;height:100%;background:currentColor;border-radius:inherit}
.va-shelf .va-rating{color:rgba(var(--v-theme-on-surface),.72)}
`

function makeShelf(Vue) {
  const {h, ref, computed, resolveComponent, watch, onUnmounted} = Vue
  return {
    name:'ViewingAssistantShelf',
    props:['initial','api','pluginId','resume'], emits:['ready'],
    setup(props,{emit}) {
      const kind = ref(props.resume?.kind || 'all'), period = ref(props.resume?.period || 'all'), page = ref(props.resume?.page || 1)
      const view = ref(null), busy = ref(false), failed = ref(false), heading = ref(null), ruler = ref(null)
      const columns = ref(0)
      const size = computed(() => columns.value ? columns.value * Math.min(4, Math.floor(20 / columns.value)) : 20)
      const reason = ref(null), dialog = ref(null), dismiss = ref(null)
      let opener = null
      const grid = computed(() => props.initial.content[1].content[0])
      const total = computed(() => view.value?.total ?? Number(props.initial.props['data-total']))
      const pages = computed(() => view.value?.pages ?? Math.max(1,Math.ceil(total.value / size.value)))
      let generation = 0, observer
      const ready = () => emit('ready',{kind:kind.value,period:period.value,page:page.value,size:size.value})
      onUnmounted(() => { generation++; observer?.disconnect() })
      async function read() {
        const stamp = ++generation
        failed.value = false
        const count = Number(props.initial.props['data-total'])
        const offset = (page.value - 1) * size.value
        if (kind.value === 'all' && period.value === 'all' && offset < count &&
            grid.value.content.length >= Math.min(offset + size.value, count)) {
          view.value = {cards:grid.value.content.slice(offset, offset + size.value),total:count,
            pages:Math.max(1,Math.ceil(count / size.value))}
          busy.value = false
          ready()
          return
        }
        busy.value = true
        try {
          const result = await props.api.get('plugin/' + props.pluginId + '/fusion/candidates/browse',
            {params:{kind:kind.value, period:period.value, page:page.value, size:size.value}})
          if (stamp !== generation) return
          if (result?.success !== true || !Array.isArray(result.cards) || !Number.isInteger(result.page) ||
              !Number.isInteger(result.pages) || !Number.isInteger(result.total)) throw Error()
          view.value = result
          page.value = result.page
          ready()
        } catch { if (stamp === generation) failed.value = true }
        finally { if (stamp === generation) busy.value = false }
      }
      function measure(width) {
        if (!(width > 0)) return
        const next = Math.min(6, Math.max(width >= 210 ? 2 : 1, Math.floor((width + 14) / 174)))
        if (columns.value === next) return
        const oldSize = columns.value ? size.value : props.resume?.size || size.value
        const offset = (page.value - 1) * oldSize
        columns.value = next
        page.value = Math.floor(offset / size.value) + 1
        read()
      }
      watch(() => props.initial, () => { if (columns.value) read() })
      Vue.onMounted(() => {
        if (!ruler.value) return
        measure(ruler.value.getBoundingClientRect().width)
        observer = new ResizeObserver(entries => measure(entries[0].contentRect.width))
        observer.observe(ruler.value)
      })
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
          return h('button',{type:'button',class:'va-reason-trigger text-caption text-medium-emphasis',
            'aria-label':summary.props['aria-label'],'aria-haspopup':'dialog',onClick:e=>explain(child,e)},
            summary.content?.length ? summary.content.map(part=>renderNode(Vue,part)) : summary.text)
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
      return () => h('section',{class:'va-shelf rounded-lg border px-4 py-4 mb-4','aria-label':'候选片单'},[
        h('style',null,vaShelfStyle),
        h('div',{ref:ruler,class:'d-flex flex-wrap align-center justify-space-between ga-2 mb-3'}, [
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
        busy.value || !columns.value ? h('p',{role:'status',class:'text-medium-emphasis py-3'},'正在读取片单…') :
        failed.value ? h('div',{role:'alert'},['片单暂时无法读取。',button('重试',read)]) :
        !total.value ? h('div',{class:'py-4'},['这个范围暂时没有候选。',button('查看全部',()=>choose('all','all'))]) :
        h('div',{...grid.value.props,style:[grid.value.props.style,{gridTemplateColumns:'repeat('+columns.value+',minmax(0,1fr))'}]},
          (view.value?.cards ?? []).map(renderCard)),
        !busy.value && !failed.value && pages.value > 1 ? h('nav',{'aria-label':'候选翻页',class:'d-flex align-center justify-center ga-2 mt-4'},[
          button('上一页',()=>choose(kind.value,period.value,page.value-1),{disabled:page.value<=1}),
          h('span',{class:'text-caption text-medium-emphasis','aria-live':'polite'},page.value+' / '+pages.value),
          button('下一页',()=>choose(kind.value,period.value,page.value+1),{disabled:page.value>=pages.value})
        ]) : null, reasonDialog()
      ])
    }
  }
}

const vaTonightStyle = `
.va-tonight{padding:16px;border-radius:8px;border:1px solid rgba(var(--v-border-color),var(--v-border-opacity));background:rgb(var(--v-theme-surface));margin-bottom:16px}
.va-tonight header,.va-tonight-controls,.va-tonight-filters,.va-tonight-meta,.va-tonight-actions,.va-tonight-notice,.va-tonight-nav,.va-tonight-pages{display:flex;align-items:center;flex-wrap:wrap;gap:6px 12px}
.va-tonight header{justify-content:space-between;gap:10px}.va-tonight-controls{gap:4px}
.va-tonight-nav{justify-content:space-between;margin-top:12px;gap:4px}.va-tonight-tabs{display:inline-flex;flex-wrap:wrap;gap:3px;padding:3px;border-radius:8px;background:rgba(var(--v-theme-on-surface),.04)}
.va-tonight-filters{margin-top:12px;padding:8px 0;border-top:1px solid rgba(var(--v-theme-on-surface),.10)}
.va-tonight-filters label{display:inline-flex;align-items:center;gap:6px;min-height:36px}
.va-tonight-filters select{color:inherit;border:1px solid rgba(var(--v-theme-on-surface),.28);border-radius:6px;padding:4px 8px;background:rgb(var(--v-theme-surface));appearance:auto}
.va-tonight-filters input{accent-color:rgb(var(--v-theme-primary))}
.va-tonight-results{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,210px),1fr));gap:8px;list-style:none;margin-top:12px;padding:0}
.va-tonight-pick{display:flex;flex-direction:column;align-items:start;gap:4px;min-width:0;padding:12px;background:rgba(var(--v-theme-on-surface),.04);border-radius:8px;overflow-wrap:anywhere}
.va-tonight-pick strong{font-size:.95rem}.va-tonight-meta{gap:2px 7px;font-size:.75rem;color:rgba(var(--v-theme-on-surface),.75)}
.va-tonight-meta>span{white-space:nowrap}.va-tonight-meta>span+span:before{content:'·';margin-inline-end:7px}
.va-tonight-pick a{display:inline-flex;align-items:center;min-height:32px;margin-top:6px;padding:3px 8px;border-radius:6px;font-size:.8125rem;font-weight:500;color:rgb(var(--v-theme-primary));background:rgba(var(--v-theme-primary),.09);text-decoration:none}
.va-tonight-pick a:hover{background:rgba(var(--v-theme-primary),.16)}
.va-tonight-actions{width:100%;margin-top:auto;padding-top:6px;gap:2px}.va-tonight-actions .v-btn{padding-inline:6px;min-width:0;font-size:.75rem;letter-spacing:normal}
.va-tonight-message{margin-top:8px;font-size:.85rem}.va-tonight-notice{margin-top:10px;padding:6px 10px;border-radius:8px;background:rgba(var(--v-theme-primary),.065);font-size:.8125rem;gap:4px 8px}.va-tonight-notice:focus{outline:2px solid rgb(var(--v-theme-primary));outline-offset:2px}
.va-tonight-pages{justify-content:flex-end;margin-top:8px;font-size:.75rem}.va-tonight button:not(:disabled){transition:transform 230ms cubic-bezier(.2,1.65,.4,1),background-color 140ms ease}.va-tonight button:not(:disabled):active{transform:scale(.94);transition:transform 65ms ease-out}
@media(pointer:coarse){.va-tonight :is(button,a,select){min-height:44px}}
@media(prefers-reduced-motion:reduce){.va-tonight button:not(:disabled){transition:none!important;transform:none!important}}
`

// Emby details links come from the bound server. Keep browser links limited
// to this contract so media URLs, credentials or extra parameters never leak.
function vaTonightLink(raw, itemId) {
  if (typeof raw !== 'string' || typeof itemId !== 'string' || !/^[\w-]{1,128}$/.test(itemId)) return null
  try {
    const url = new URL(raw)
    const route = /^#!\/item\?(.+)$/.exec(url.hash)
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search ||
        !url.pathname.endsWith('/web/index.html') || !route) return null
    const query = new URLSearchParams(route[1])
    if (Array.from(query).length !== 3 || query.get('id') !== itemId || query.get('context') !== 'home' ||
        !/^[\w-]{1,128}$/.test(query.get('serverId') || '')) return null
    return url.href
  } catch { return null }
}

function makeTonight(Vue) {
  const {h, ref, watch, onUnmounted, resolveComponent} = Vue
  const messages = {
    SETUP_REQUIRED:'先绑定 Emby 用户和媒体库，就能开始选片。',
    VERIFY_REQUIRED:'验证一次已保存的 Emby 绑定，就能继续。',
    VERIFY_FAILED:'绑定未通过验证，请检查账号和媒体库。',
    SCOPE_REQUIRED:'请选择允许使用的媒体库。',
    LIBRARY_NOT_VISIBLE:'媒体库权限有变化，请重新选择。',
    FORBIDDEN:'当前账号没有查看这份片单的权限。',
    SIGN_IN:'请重新登录 MoviePilot。',
    BUSY:'片单正在读取，请稍后再试。',
    RATE_LIMITED:'Emby 暂时繁忙，请稍后再试。',
    UNAVAILABLE:'暂时读不到片单，请稍后再试。',
  }
  const choiceMessages = {
    CHOICE_CHANGED:'这部电影的选择已变化，刷新后再试。',
    STALE_CONTEXT:'这份片单已过期，刷新后再试。',
    CHOICE_LIMIT:'记录已满，请先整理留到下次或已略过的电影。',
    STORAGE_FAILED:'暂时未能保存，请重试。',
    INVALID_CHOICE:'这次选择未保存，请刷新片单。',
    UNAVAILABLE:'暂时无法保存，请稍后再试。',
  }
  return {
    name:'ViewingAssistantTonight', props:['api','pluginId','initial','onSettings'],
    setup(props) {
      const maxMinutes = ref(0), chinese = ref(false), filtersOpen = ref(false), mode = ref('discover')
      const durationInput = ref(null), noticeElement = ref(null)
      const items = ref(null), limited = ref(false), pending = ref(''), reason = ref('')
      const context = ref(''), counts = ref(null), page = ref(1), pages = ref(1), pendingChoice = ref(null)
      const notice = ref(null), choiceError = ref(''), acted = ref(false), unavailableReason = ref('')
      let generation = 0, controller
      const path = 'plugin/' + props.pluginId + '/fusion/'
      const btn = (label, attrs={}) => h(resolveComponent('VBtn'),{size:'small',...attrs},{default:()=>label})
      function reset() {
        generation++
        controller?.abort()
        controller = null
        pending.value = ''; pendingChoice.value = null; reason.value = ''; items.value = null; limited.value = false
        context.value = ''; notice.value = null; choiceError.value = ''; acted.value = false; unavailableReason.value = ''
      }
      function bindingChanged() { reset(); counts.value=null; mode.value='discover'; page.value=1; pages.value=1 }
      watch(() => props.initial, bindingChanged)
      onUnmounted(bindingChanged)
      function focusNotice() { Vue.nextTick(() => noticeElement.value?.focus()) }
      function changeFilter(field, value) {
        if (['validate','choice'].includes(pending.value) || field.value === value) return
        reset(); page.value=1; field.value = value
      }
      function toggleFilters() {
        filtersOpen.value = !filtersOpen.value
        if (filtersOpen.value) Vue.nextTick(() => durationInput.value?.focus())
      }
      function updateCounts(value) {
        if (value && ['later','hide'].every(key=>Number.isInteger(value[key]) && value[key]>=0 && value[key]<=200))
          counts.value={later:value.later,hide:value.hide}
      }
      function fail(error) {
        const status = error?.response?.status ?? error?.status
        return status === 401 ? 'SIGN_IN' : status === 403 ? 'FORBIDDEN' : 'UNAVAILABLE'
      }
      async function read(verify = false) {
        if (pending.value) return
        reset()
        const stamp = generation
        controller = new AbortController()
        try {
          if (verify) {
            pending.value = 'validate'
            const binding = await props.api.post(path + 'validate', {})
            if (stamp !== generation) return
            if (binding?.success !== true) { reason.value = 'VERIFY_FAILED'; return }
          }
          pending.value = 'read'
          const result = await props.api.get(path + 'tonight', {params:{
            max_minutes:mode.value==='discover'?maxMinutes.value:0, chinese_subtitles:mode.value==='discover'&&chinese.value, mode:mode.value, page:page.value
          }, signal:controller.signal})
          if (stamp !== generation) return
          if (result?.success !== true) {
            reason.value = Object.hasOwn(messages, result?.reason) ? result.reason : 'UNAVAILABLE'
            return
          }
          if (!Array.isArray(result.items) || result.items.length > (mode.value==='discover'?3:12) || result.items.some(
            item => !item || typeof item.id !== 'string' || !/^[\w-]{1,128}$/.test(item.id) ||
              typeof item.title !== 'string' || !item.title.trim()
          )) throw Error('invalid picks')
          items.value = result.items
          context.value = typeof result.context==='string' ? result.context : ''
          limited.value = result.limited === true
          unavailableReason.value = ['LIBRARY_NOT_VISIBLE','FORBIDDEN'].includes(result.unavailable_reason) ? result.unavailable_reason : ''
          updateCounts(result.counts)
          page.value = Number.isInteger(result.page) && result.page>0 ? result.page : page.value
          pages.value = Number.isInteger(result.pages) && result.pages>=0 ? Math.max(1,result.pages) : 1
        } catch (error) {
          if (stamp === generation) reason.value=fail(error)
        } finally {
          if (stamp === generation) { controller = null; pending.value = '' }
        }
      }
      function changeMode(next) {
        if (pending.value || mode.value===next) return
        mode.value=next; page.value=1
        return read()
      }
      async function choose(item, action, undo = false) {
        if (pending.value || !context.value || (!undo && choiceError.value)) return
        const previous = undo ? notice.value : null
        if (undo && !previous?.undo) return
        const body = undo ? previous.undo : {context:item.choiceContext || context.value,item_id:item.id,action}
        const index = undo ? previous.index : items.value.findIndex(row=>row.id===item.id)
        const stamp = generation
        pending.value='choice'; pendingChoice.value={id:item.id,action,undo}; choiceError.value=''
        try {
          const result = await props.api.post(path+'tonight/choice',body)
          if (stamp!==generation) return
          if (result?.success!==true) {
            choiceError.value=Object.hasOwn(choiceMessages,result?.reason) ? result.reason : 'UNAVAILABLE'
            focusNotice(); return
          }
          const receipt=result.undo
          if (!receipt || typeof receipt.context!=='string' || receipt.item_id!==item.id ||
              !['later','hide','clear'].includes(receipt.action)) throw Error('invalid receipt')
          updateCounts(result.counts)
          acted.value=true
          if (undo) {
            const restored={...item,choiceContext:receipt.context}
            items.value=[...items.value.filter(row=>row.id!==item.id)]
            items.value.splice(Math.min(index,items.value.length),0,restored)
            notice.value={text:'已撤销',item:null,undo:null}
          } else {
            items.value=items.value.filter(row=>row.id!==item.id)
            notice.value={text:action==='later'?'已留到下次':action==='hide'?'已略过这部电影':mode.value==='hidden'?(item.available===false?'已清除记录':'已恢复推荐'):'已移出片单',
              item,index,undo:receipt}
          }
          focusNotice()
        } catch (error) {
          if (stamp!==generation) return
          const code=fail(error)
          if (code==='SIGN_IN'||code==='FORBIDDEN') {
            items.value=null; context.value=''; counts.value=null; notice.value=null; reason.value=code
          } else choiceError.value='UNAVAILABLE'
          focusNotice()
        } finally { if (stamp===generation) {pending.value='';pendingChoice.value=null} }
      }
      function pick(item) {
        const url = item.available===false?null:vaTonightLink(item.url, item.id)
        const meta = []
        if (Number.isInteger(item.year)) meta.push(String(item.year))
        if (Number.isInteger(item.minutes) && item.minutes > 0) meta.push(item.minutes + ' 分钟')
        if (item.chinese_subtitles === true) meta.push('中文字幕')
        if (Number.isFinite(item.score) && item.score >= 0 && item.score <= 100) meta.push('口味 ' + item.score)
        const action=(label,value)=>btn(label,{variant:'text',color:value==='later'?'primary':undefined,
          disabled:!!pending.value||!!choiceError.value,
          loading:pendingChoice.value?.id===item.id && pendingChoice.value.action===value && !pendingChoice.value.undo,
          'aria-label':label+'《'+item.title+'》',
          onClick:()=>choose(item,value)})
        return h('li',{class:'va-tonight-pick',key:item.id},[
          h('strong',item.title),
          meta.length ? h('span',{class:'va-tonight-meta'},meta.map(text => h('span',text))) : null,
          item.version ? h('small','版本 · ' + item.version) : null,
          item.reason ? h('small',item.reason) : null,
          url ? h('a',{href:url,target:'_blank',rel:'noopener noreferrer',
            'aria-label':'在 Emby 打开《' + item.title + '》'},'在 Emby 打开 ↗') :
            item.available===false?null:h('small',{class:'text-medium-emphasis'},'请在 Emby 中查找此片'),
          context.value ? h('div',{class:'va-tonight-actions','aria-label':'选择《'+item.title+'》'},
            mode.value==='hidden' ? [action(item.available===false?'清除记录':'恢复推荐','clear')] : [
              mode.value==='saved'?action('移出片单','clear'):action('留到下次','later'),
              item.available===false?null:action('不感兴趣','hide')]) : null
        ])
      }
      return () => {
        const needsSetup = ['SETUP_REQUIRED','VERIFY_FAILED','SCOPE_REQUIRED','LIBRARY_NOT_VISIBLE'].includes(reason.value)
        const needsVerify = reason.value === 'VERIFY_REQUIRED'
        const setup = needsSetup && props.onSettings
        const hasFilters = mode.value==='discover' && (maxMinutes.value > 0 || chinese.value)
        const widen = items.value?.length === 0 && hasFilters && !acted.value
        const denied = reason.value === 'SIGN_IN' || reason.value === 'FORBIDDEN'
        const filterText = [maxMinutes.value ? maxMinutes.value + ' 分钟内' : '', chinese.value ? '中文字幕' : '']
          .filter(Boolean).join(' · ')
        let empty = mode.value==='saved'?'想稍后看的电影，可以在推荐卡上留到这里。':
          mode.value==='hidden'?'还没有略过的电影。':'这次没有找到可推荐的电影。'
        if (acted.value) empty=mode.value==='discover'?'这组已整理好，继续挑下一组。':'本页已整理好，可以刷新片单。'
        else if (hasFilters) empty='没有符合当前条件的电影，试试放宽筛选。'
        else if (counts.value?.[mode.value==='hidden'?'hide':'later']>0 && mode.value!=='discover') empty='这页电影目前不在可见片库中，可以查看其他页。'
        const activeTab = (key,label)=>btn(label,{variant:mode.value===key?'tonal':'text',
          color:mode.value===key?'primary':undefined,'aria-pressed':mode.value===key,
          disabled:!!pending.value,onClick:()=>changeMode(key)})
        return h('section',{class:'va-tonight','aria-label':'今晚看什么','aria-busy':!!pending.value},[
          h('style',vaTonightStyle),
          h('header',[
            h('h2',{class:'text-subtitle-1 font-weight-bold'},'今晚看什么'),
            denied ? null : h('div',{class:'va-tonight-controls'},[
              mode.value==='discover'?btn(filterText || '筛选',{variant:'text','aria-expanded':filtersOpen.value,
                disabled:['validate','choice'].includes(pending.value),
                'aria-label':filterText ? '筛选：' + filterText : '筛选',onClick:toggleFilters}):null,
              btn(setup ? '设置观影偏好' : needsVerify ? '验证绑定' : reason.value ? '重试' :
                  widen ? '不限条件再挑' : mode.value==='discover' ? acted.value?'继续挑片':items.value?'刷新片单':'帮我挑片' : '刷新片单',{
                color:'primary',variant:'flat',loading:['read','validate'].includes(pending.value),disabled:!!pending.value,
                onClick:()=>{
                  if (setup) return props.onSettings('tonight')
                  if (widen) { maxMinutes.value=0; chinese.value=false; page.value=1 }
                  return read(needsVerify)
                }})
            ])
          ]),
          denied ? null : h('nav',{class:'va-tonight-nav','aria-label':'我的选片'},[
            h('div',{class:'va-tonight-tabs'},[
              activeTab('discover','为我挑选'),
              activeTab('saved','留到下次'+(counts.value?.later?' · '+counts.value.later:''))
            ]),
            counts.value?.hide || mode.value==='hidden' ? activeTab('hidden','已略过'+(counts.value?.hide?' · '+counts.value.hide:'')) : null
          ]),
          filtersOpen.value && mode.value==='discover' && !denied ? h('div',{class:'va-tonight-filters',role:'group','aria-label':'本次选片条件'},[
            h('label',['片长', h('select',{ref:durationInput,value:maxMinutes.value,disabled:['validate','choice'].includes(pending.value),
              onChange:e=>changeFilter(maxMinutes,Number(e.target.value))},
              [0,90,100,120].map(n=>h('option',{value:n},n ? n+' 分钟以内' : '不限')))]),
            h('label',[h('input',{type:'checkbox',checked:chinese.value,disabled:['validate','choice'].includes(pending.value),
              onChange:e=>changeFilter(chinese,e.target.checked)}),'仅有中文字幕'])
          ]) : null,
          h('div',{role:'status','aria-live':'polite'},[
            reason.value ? h('p',{class:'va-tonight-message'},messages[reason.value]) : null,
            unavailableReason.value ? h('div',{class:'va-tonight-notice'},[
              h('span','片库权限有变化，暂不显示影片资料。'),
              props.onSettings?btn('检查绑定',{variant:'text',disabled:!!pending.value,onClick:()=>props.onSettings('tonight')}):null
            ]) : null,
            pending.value && pending.value!=='choice' ? h('p',{class:'va-tonight-message'},pending.value === 'validate' ? '正在验证绑定…' : '正在片库中挑选…') : null,
            notice.value || choiceError.value ? h('div',{class:'va-tonight-notice',ref:noticeElement,tabindex:-1},[
              h('span',choiceError.value?choiceMessages[choiceError.value]:notice.value.text),
              choiceError.value ? btn('刷新片单',{variant:'text',disabled:!!pending.value,onClick:()=>read()}) :
                notice.value.undo ? btn('撤销',{variant:'text',disabled:!!pending.value,loading:pendingChoice.value?.undo===true,onClick:()=>choose(notice.value.item,null,true)}) : null
            ]) : null,
            items.value ? (items.value.length ? h('ol',{class:'va-tonight-results'},items.value.map(pick)) :
              h('p',{class:'va-tonight-message'},empty)) : null,
            limited.value ? h('small',{class:'text-medium-emphasis'},'本次只核对了部分片库。') : null
          ]),
          mode.value!=='discover' && items.value && pages.value>1 ? h('nav',{class:'va-tonight-pages','aria-label':'个人片单分页'},[
            btn('上一页',{variant:'text',disabled:!!pending.value||page.value<=1,onClick:()=>{page.value--;return read()}}),
            h('span',page.value+' / '+pages.value),
            btn('下一页',{variant:'text',disabled:!!pending.value||page.value>=pages.value,onClick:()=>{page.value++;return read()}})
          ]) : null
        ])
      }
    }
  }
}

function makeSubtitleRail(Vue) {
  const {h, ref, computed, watch} = Vue
  const kinds = {
    saved:['已补齐', 'M5 12l4 4L19 6'],
    existing:['已有字幕', 'M5 8h14M5 15h14'],
    waiting:['等待中', 'M12 6v6l4 2'],
    issue:['需留意', 'M12 5v8m0 4v1'],
  }
  const states = {saved:'已补齐', existing:'已有字幕', waiting_transfer:'等入库', waiting_subtitle:'等字幕',
    retry:'待重试', expired:'已到期', unknown:'待核对'}
  const bucket = state => state === 'saved' || state === 'existing' ? state :
    state === 'waiting_transfer' || state === 'waiting_subtitle' ? 'waiting' : 'issue'
  const icon = kind => h('svg', {viewBox:'0 0 24 24',width:16,height:16,fill:'none',stroke:'currentColor',
    'stroke-width':2.3,'stroke-linecap':'round','stroke-linejoin':'round','aria-hidden':'true'}, [
    h('path',{d:kinds[kind][1]})
  ])
  return {
    name:'ViewingAssistantSubtitleRail',
    props:{records:{type:Array,required:true},enabled:Boolean},
    setup(props) {
      const choice = ref('all'), index = ref(0), rail = ref(null), size = ref(10)
      let observer
      const items = computed(() => props.records.flatMap(record =>
        record.marks.map(([state, name, note, refresh]) => ({record, state, name, note, refresh, kind:bucket(state)}))))
      const counts = computed(() => {
        const result = {saved:0, existing:0, waiting:0, issue:0}
        for (const item of items.value) result[item.kind]++
        return result
      })
      const visible = computed(() => choice.value === 'all' ? items.value : items.value.filter(i => i.kind === choice.value))
      watch(() => props.records, () => { choice.value = 'all'; index.value = 0 })
      Vue.onMounted(() => {
        if (!rail.value || typeof ResizeObserver !== 'function') return
        observer = new ResizeObserver(([entry]) => { size.value = Math.max(3, Math.min(16, Math.floor(entry.contentRect.width / 40))) })
        observer.observe(rail.value)
      })
      Vue.onUnmounted(() => observer?.disconnect())
      const toggle = kind => { choice.value = choice.value === kind ? 'all' : kind; index.value = 0 }
      const title = item => [item.record.title, item.name].filter(Boolean).join(' · ')
      const step = (direction, start) => { index.value = Math.max(0, Math.min(visible.value.length - 1, start + direction * size.value)) }
      return () => {
        const all = visible.value, start = Math.floor(index.value / size.value) * size.value
        const selected = all[index.value]
        const detail = selected && [selected.note, selected.record.note, selected.refresh].filter(Boolean)
        // Several files in a release can complete independently. Each marker uses its own saved outcome.
        return h('div', {class:'va-subtitles'}, [
          h('div', {class:'va-sub-legend',role:'group','aria-label':'按字幕状态筛选'}, Object.keys(kinds)
            .filter(kind => counts.value[kind]).map(kind => h('button', {type:'button',class:'va-sub-key',
              'data-kind':kind,'aria-pressed':choice.value === kind,onClick:()=>toggle(kind)}, [
              icon(kind), kinds[kind][0], h('strong', counts.value[kind])
            ]))),
          h('div', {ref:rail,class:'va-sub-track',role:'group','aria-label':'逐项字幕结果'}, all.slice(start, start + size.value)
            .map((item, i) => h('button',{type:'button',class:'va-sub-mark','data-kind':item.kind,
              'aria-pressed':start + i === index.value,'aria-label':title(item) + ' · ' + states[item.state],
              title:title(item) + ' · ' + states[item.state],onClick:()=>{index.value = start + i}},[icon(item.kind)]))),
          all.length > size.value ? h('nav',{class:'va-sub-pages','aria-label':'字幕记录翻页'}, [
            h('button',{type:'button',disabled:start === 0,onClick:()=>step(-1,start),'aria-label':'上一组记录'},'‹'),
            h('span',(start + 1) + '–' + Math.min(all.length, start + size.value) + ' / ' + all.length),
            h('button',{type:'button',disabled:start + size.value >= all.length,onClick:()=>step(1,start),'aria-label':'下一组记录'},'›'),
          ]) : null,
          selected ? h('article',{class:'va-sub-selected','aria-live':'polite'}, [
            h('div',{class:'va-sub-itemline'},[h('strong',title(selected)),
              h('span',{class:'text-caption text-medium-emphasis'},states[selected.state])]),
            selected.refresh.includes('Emby刷新未确认') ? h('p',{class:'text-caption text-warning'},'请在 Emby 刷新该作品。') : null,
            h('details',{key:start + ':' + index.value + ':' + choice.value,class:'va-more'},[
              h('summary','补查记录'), h('p',[selected.record.checked,selected.record.attempts + ' 次补查'].filter(Boolean).join(' · ')),
              ...Array.from(new Set(detail)).map(note => h('p',note)),
            ])
          ]) : h('div',{class:'text-body-2 text-medium-emphasis'},props.enabled ? '暂无补查记录' : '字幕补查已关闭')
        ])
      }
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

function makeLibraryPicker(Vue) {
  const {h,ref,onUnmounted,resolveComponent} = Vue
  return {
    name:'ViewingAssistantLibraryPicker',
    props:['api','pluginId','server','user','value','fieldProps'],
    emits:['change'],
    setup(props,{emit}) {
      const options = ref(null), manual = ref(false), busy = ref(false), note = ref('')
      let active = true, request = 0
      onUnmounted(()=>{ active = false })
      const selected = () => Array.isArray(props.value) ? props.value :
        typeof props.value === 'string' ? props.value.split(/[\s,]+/).filter(Boolean) : []
      async function read() {
        const server = props.server, user = vaEmbyAccountId(props.user)
        if (busy.value || !server || !user || props.fieldProps?.disabled) return
        const turn = ++request
        busy.value = true; manual.value = false; note.value = ''
        const current = () => active && turn === request && props.server === server && vaEmbyAccountId(props.user) === user
        try {
          const result = await props.api.get('plugin/' + props.pluginId + '/fusion/binding/libraries',
            {params:{server_ref:server,user_id:user}})
          if (!current()) return
          if (result?.success !== true || !Array.isArray(result.libraries) || result.libraries.some(item =>
            !item || typeof item.id !== 'string' || !/^[\w-]{1,80}$/.test(item.id) ||
            typeof item.name !== 'string' || !item.name.trim()
          )) throw Error('Invalid directory')
          options.value = result.libraries.map(item=>({title:item.name,value:item.id}))
          if (!options.value.length) note.value = '此账号没有可选的影视库。'
        } catch {
          if (current()) note.value = '读取媒体库失败，可以重试或手动填写 ID。'
        } finally { if (current()) busy.value = false }
      }
      function toggleManual() {
        if (props.fieldProps?.disabled) return
        request++; busy.value=false; note.value=''
        manual.value=!manual.value
        if (!manual.value && !options.value?.length) read()
      }
      const button = (text,onClick,props={}) => h(resolveComponent('VBtn'),
        {size:'small',variant:'text',...props,onClick},{default:()=>text})
      return () => {
        const field = props.fieldProps || {}, ids = selected()
        const list = options.value
        const server = props.server, user = vaEmbyAccountId(props.user)
        const input = manual.value ?
          h(resolveComponent('VTextarea'),{...field,label:'指定媒体库 ID',rows:2,hint:'用换行或逗号分隔',
            modelValue:Array.isArray(props.value) ? ids.join(', ') : props.value || '',
            'onUpdate:modelValue':v=>{if (active && !field.disabled && manual.value) emit('change',v)}}) :
          list?.length ? h(resolveComponent('VSelect'),{
            ...field,label:'指定媒体库',multiple:true,chips:true,closableChips:true,
            items:[...list,...ids.filter(id=>!list.some(item=>item.value===id)).map(value=>({title:'当前未列出 · '+value,value}))],
            itemTitle:'title',itemValue:'value',modelValue:ids,
            'onUpdate:modelValue':values=>{
              if (!active || props.fieldProps?.disabled || manual.value || options.value!==list ||
                  props.server!==server || vaEmbyAccountId(props.user)!==user || !Array.isArray(values)) return
              if (values.every(id=>list.some(item=>item.value===id) || selected().includes(id)))
                emit('change', [...new Set(values)].join(','))
            }
          }) : button(note.value?'重新读取媒体库':ids.length?'更改指定媒体库':'选择媒体库',read,
            {color:'primary',variant:'tonal',loading:busy.value,disabled:field.disabled || busy.value || !server || !user})
        return h('div',{'data-model-key':'fusion_library_ids'},[
          input,
          !manual.value && !list?.length && ids.length ? h('span',{class:'ms-2 text-caption'},'已选 '+ids.length+' 个') : null,
          button(manual.value?'按名称选择':'手动填写 ID',toggleManual,{size:'x-small',disabled:field.disabled}),
          note.value ? h('div',{role:'status',class:'text-caption mt-1'},note.value) : null
        ])
      }
    }
  }
}

// MoviePilot owns loading and persistence; the plugin owns when its draft may be saved.
function vaEmbyAccountId(value) {
  if (typeof value !== 'string' || !/^(?:[a-f0-9]{32}|[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12})$/i.test(value)) return ''
  return value.replace(/-/g, '').toLowerCase()
}

function vaBindingIssues(draft) {
  const issues = []
  if (draft.fusion_enabled !== true) issues.push({key:'fusion_enabled', text:'打开观影偏好更新'})
  const server = draft.fusion_server_ref
  if (typeof server !== 'string' || !server || server.trim() !== server || Array.from(server).length > 200 ||
      /[\p{C}\p{Z}]/u.test(server.replace(/ /g, '')))
    issues.push({key:'fusion_server_ref', text:'选择 Emby 服务器'})
  if (!vaEmbyAccountId(draft.fusion_user_id)) issues.push({key:'fusion_user_id', text:'选择 Emby 账号'})
  if (!['selected_ids','all_visible'].includes(draft.fusion_library_mode))
    issues.push({key:'fusion_library_mode', text:'选择授权媒体库'})
  if (draft.fusion_library_mode === 'selected_ids') {
    const raw = draft.fusion_library_ids
    const ids = Array.isArray(raw) ? raw : typeof raw === 'string' ? raw.trim().split(/[\s,]+/) : []
    if (!ids.length || ids.length > 100 || !ids.every(x => typeof x === 'string' && /^[A-Za-z0-9_-]{1,80}$/.test(x)))
      issues.push({key:'fusion_library_ids',text:'填写有效的媒体库 ID'})
  }
  return issues
}

const vaPersonalFeeds = ['tmdb_personal_movie', 'tmdb_personal_tv']
const vaMovieFeed = 'tmdb_popular_movie'

function vaSelectStrategy(draft, preset) {
  const wasPersonal = draft.fusion_execution_preset === 'personal'
  draft.fusion_execution_preset = preset
  if (wasPersonal === (preset === 'personal')) return
  const feeds = preset === 'personal' ? vaPersonalFeeds : [vaMovieFeed]
  const previous = Array.isArray(draft.fusion_discovery_sources) ? draft.fusion_discovery_sources : []
  draft.fusion_discovery_sources = [...new Set([...previous, ...feeds])]
  draft.fusion_execution_sources = [...feeds]
  draft.fusion_discovery_enabled = true
  draft.fusion_execution_legacy_isolated = false
  draft.fusion_execution_storage_verified = false
  if (preset === 'personal') {
    draft.fusion_execution_daily_limit = 3
    draft.fusion_execution_tv_daily_limit = 1
  }
}

function draftIssues(draft) {
  if (draft.fusion_execution_enabled !== true) return []
  const issues = vaBindingIssues(draft)
  const need = (ok, key, text) => { if (!ok) issues.push({ key, text }) }
  const preset = draft.fusion_execution_preset
  const personal = preset === 'personal'
  const feeds = personal ? vaPersonalFeeds : [vaMovieFeed]
  const plain = (value, limit) => typeof value === 'string' && value.trim() === value &&
    value.length > 0 && Array.from(value).length <= limit && !/[\p{C}\p{Z}]/u.test(value.replace(/ /g, ''))
  need(draft.fusion_discovery_enabled === true, 'fusion_discovery_enabled', '打开候选更新')
  need(Array.isArray(draft.fusion_discovery_sources) && feeds.every(x => draft.fusion_discovery_sources.includes(x)),
    'fusion_discovery_sources', personal ? '候选来源加入个性化电影和剧集' : '候选来源加入 TMDB 热门电影')
  need(plain(draft.fusion_execution_owner, 128), 'fusion_execution_owner', '填写 MoviePilot 订阅用户名')
  const sources = draft.fusion_execution_sources
  need(Array.isArray(sources) && sources.length === feeds.length && feeds.every(x => sources.includes(x)),
    personal ? 'fusion_execution_preset' : 'fusion_execution_sources',
    personal ? '重新选择按口味发现电影和剧集' : '选择自动订阅来源')
  need(['personal', 'ranked', 'conservative', 'balanced'].includes(preset), 'fusion_execution_preset', '选择订阅方式')
  const daily = draft.fusion_execution_daily_limit
  const max = personal || preset === 'ranked' ? 3 : 5
  need(['string', 'number'].includes(typeof daily) && /^[1-5]$/.test(String(daily)) && Number(daily) <= max,
    'fusion_execution_daily_limit', '电影每日上限需为 1–' + max + ' 部')
  const tv = draft.fusion_execution_tv_daily_limit
  if (personal) need(['string', 'number'].includes(typeof tv) && /^[1-3]$/.test(String(tv)),
    'fusion_execution_tv_daily_limit', '剧集每日上限需为 1–3 部')
  need(draft.fusion_execution_legacy_isolated === true, 'fusion_execution_legacy_isolated', '确认同来源旧订阅已停用')
  need(draft.fusion_execution_storage_verified === true, 'fusion_execution_storage_verified', '确认下载目录的存储映射')
  return issues
}

// Only a single scoped action returned by the current review may leave the read-only card.
function vaReviewScope(nodes, path) {
  let selected = null
  let verifyBinding = false
  function pick(node) {
    if (!node || typeof node !== 'object') return node
    if (node.props?.['data-va-verify-binding'] === true) verifyBinding = true
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
  return {scope,selected,verifyBinding}
}

function makeConfig(Vue, renderNode, Support, pluginId) {
  const LibraryPicker = makeLibraryPicker(Vue)
  const { h, ref, computed, onMounted, onUnmounted, nextTick, resolveComponent } = Vue
  const copy = value => JSON.parse(JSON.stringify(value))
  const resultText = {
    INCOMPATIBLE: ['fusion_execution_owner', '未能核实这个订阅用户或当前 MoviePilot 版本。请检查用户名与宿主版本。'],
    SERVER_UNAVAILABLE: ['fusion_server_ref', '所选 Emby 连接已不可用，请重新选择。'],
    SPACE_LOW: ['fusion_execution_storage_verified', '下载目录可用空间低于 100 GiB。'],
    SPACE_UNVERIFIED: ['fusion_execution_storage_verified', 'MoviePilot 无法核实下载路径与存储，请检查目录和下载器配置。'],
  }
  const validId = typeof pluginId === 'string' && /^[A-Za-z][A-Za-z0-9_]{0,127}$/.test(pluginId)
  return {
    name: 'ViewingAssistantConfig', inheritAttrs: false, props: ['api', 'initialConfig'], emits: ['save', 'close', 'switch','layout'],
    setup(props, { emit }) {
      const boardBookmark = vaTakeHandoff('config')
      let openRankedDraft = boardBookmark?.target === 'ranked'
      let openPersonalDraft = boardBookmark?.target === 'personal'
      const tonightSetup = boardBookmark?.target === 'tonight'
      const draft = ref({}), form = ref([]), loaded = ref(false), busy = ref(false)
      const message = ref(''), serverIssue = ref(null), review = ref(null), saved = ref(false), conflict = ref(false)
      const container = ref(null)
      const scopeRegion = ref(null), deliberate = ref(false)
      const accounts = ref(null), accountError = ref(''), loadingAccounts = ref(false), manualAccount = ref(false)
      const nextBinding = computed(() => tonightSetup ? vaBindingIssues(draft.value)[0] : null)
      const personalSetup = computed(() => tonightSetup && draft.value.fusion_execution_enabled !== true)
      let alive = true, original = '', guardTimer, directoryRequest = 0
      onUnmounted(() => { alive = false; clearTimeout(guardTimer) })
      const issues = computed(() => [...draftIssues(draft.value), ...(serverIssue.value ? [serverIssue.value] : [])])
      const path = 'plugin/' + pluginId
      const presented = computed(() => vaReviewScope(review.value, path))
      const textButton = (text, click, options = {}) => h(resolveComponent('VBtn'),
        { variant: 'text', size: 'small', ...options, onClick: click }, () => text)
      const setField = (key, value) => {
        if (busy.value || saved.value) return
        const serverChanged = key === 'fusion_server_ref' && value !== draft.value[key]
        const accountChanged = key === 'fusion_user_id' && vaEmbyAccountId(value) !== vaEmbyAccountId(draft.value[key])
        if (serverChanged) {
          resetDirectory()
          draft.value.fusion_user_id = ''
        }
        if (serverChanged || accountChanged) {
          draft.value.fusion_library_mode = 'unselected'
          draft.value.fusion_library_ids = ''
        }
        if (key === 'fusion_execution_preset') vaSelectStrategy(draft.value, value)
        else draft.value[key] = value
        serverIssue.value = null
        message.value = ''
      }
      function resetDirectory() {
        directoryRequest++
        accounts.value = null; accountError.value = ''; loadingAccounts.value = false; manualAccount.value = false
      }
      async function readAccounts() {
        const server = draft.value.fusion_server_ref
        if (!server || !validId || busy.value || saved.value || loadingAccounts.value) return
        const request = ++directoryRequest
        loadingAccounts.value = true; accountError.value = ''; manualAccount.value = false
        try {
          const result = await props.api.get(path + '/fusion/binding/users', {params:{server_ref:server}})
          if (!alive || request !== directoryRequest || saved.value) return
          if (result?.success !== true || !Array.isArray(result.users)) throw Error()
          accounts.value = result.users.filter(user => vaEmbyAccountId(user?.id) && typeof user.name === 'string' && user.name.trim())
            .map(user => ({title:user.name,value:vaEmbyAccountId(user.id)}))
          if (!accounts.value.length) accountError.value = '此服务器未返回可选账号。'
        } catch {
          if (alive && request === directoryRequest) accountError.value = '无法读取账号，请重试或手动填写 ID。'
        } finally {
          if (alive && request === directoryRequest) loadingAccounts.value = false
        }
      }
      function accountField() {
        const id = vaEmbyAccountId(draft.value.fusion_user_id)
        const server = draft.value.fusion_server_ref
        const manual = manualAccount.value
        const items = accounts.value || []
        const props = fieldProps('fusion_user_id')
        const input = manual ? h(resolveComponent('VTextField'), {...props, label:'Emby 用户 ID', autocomplete:'off',
          modelValue:draft.value.fusion_user_id, 'onUpdate:modelValue':v=>setField('fusion_user_id',v)}) :
          items.length ? h(resolveComponent('VSelect'), {...props, label:'Emby 账号', items,
            modelValue:items.some(item=>item.value===id) ? id : null,
            'onUpdate:modelValue':v=>{if(draft.value.fusion_server_ref===server && accounts.value===items &&
              items.some(item=>item.value===v)) setField('fusion_user_id',v)}}) :
          textButton(loadingAccounts.value?'正在读取账号':accountError.value?'重新读取账号':
            id?'更换 Emby 账号':'选择 Emby 账号', readAccounts, {variant:'tonal',color:'primary',
              disabled:busy.value || !server || loadingAccounts.value, loading:loadingAccounts.value})
        return h('div', {'data-model-key':'fusion_user_id'}, [
          input,
          !manual && id && !items.some(item=>item.value===id) ?
            h('span',{class:'text-caption ms-2'},'当前账号 · …'+id.slice(-6)) : null,
          accountError.value && !manual ? h('div',{role:'status',class:'text-caption mt-1'},accountError.value) : null,
          textButton(manual?'按账号名称选择':'手动填写 ID', () => {
            if (manual) { manualAccount.value = false; if (!accounts.value?.length) readAccounts() }
            else { resetDirectory(); manualAccount.value = true }
          }, {disabled:busy.value || !server, size:'x-small'}),
        ])
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
          resetDirectory()
          form.value = response.conf
          draft.value = copy(response.model)
          original = JSON.stringify(draft.value)
          loaded.value = true
          if (openPersonalDraft) {
            openPersonalDraft = false
            vaSelectStrategy(draft.value, 'personal')
            draft.value.fusion_execution_enabled = true
            nextTick(() => focusField(issues.value[0]?.key || 'fusion_execution_preset'))
          } else if (openRankedDraft) {
            openRankedDraft = false
            vaSelectStrategy(draft.value, 'ranked')
            if (Number(draft.value.fusion_execution_daily_limit) > 3) draft.value.fusion_execution_daily_limit = 3
            nextTick(() => focusField('fusion_execution_preset'))
          }
          if (nextBinding.value) nextTick(() => focusField(nextBinding.value?.key))
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
      async function verifySavedBinding() {
        if (busy.value || !saved.value || !presented.value.verifyBinding) return
        busy.value = true; message.value = ''
        try {
          const result = await props.api.post(path + '/fusion/validate', {})
          if (result?.success !== true) throw Error()
          if (alive) await fetchReview()
        } catch { if (alive) message.value = '无法验证 Emby 账号，请检查绑定或重试。' }
        finally { if (alive) busy.value = false }
      }
      async function persistDraft(snapshot) {
        if (JSON.stringify(snapshot) === original) return true
        const latest = await props.api.get('plugin/form/' + pluginId)
        if (!alive) return false
        if (!latest?.model || JSON.stringify(latest.model) !== original) {
          conflict.value = true
          message.value = '设置已在其他窗口更改。请重新读取后再编辑。'
          return false
        }
        const result = await props.api.put(path, snapshot)
        if (result?.success !== true) throw Error()
        if (!alive) return false
        original = JSON.stringify(snapshot)
        return true
      }
      async function save() {
        if (!alive || !loaded.value || busy.value || saved.value || conflict.value || issues.value.length ||
            (personalSetup.value && nextBinding.value)) return
        if (!personalSetup.value && draft.value.fusion_execution_enabled !== true) {
          vaClearHandoff()
          emit('save', copy(draft.value))
          return
        }
        const snapshot = copy(draft.value)
        busy.value = true
        message.value = ''
        serverIssue.value = null
        try {
          if (personalSetup.value) {
            if (!await persistDraft(snapshot)) return
            message.value = '设置已保存，正在验证 Emby 账号…'
            try {
              const result = await props.api.post(path + '/fusion/validate', {})
              if (result?.success !== true) throw Error()
            } catch {
              if (alive) message.value = '设置已保存，账号暂未验证。请核对账号和媒体库后重试。'
              return
            }
            if (alive) { vaReturnToBoard(boardBookmark); emit('switch') }
            return
          }
          // Send just the required draft fields, never private host config or API credentials.
          const allowed = ['fusion_enabled', 'fusion_server_ref', 'fusion_user_id', 'fusion_library_mode',
            'fusion_library_ids', 'fusion_discovery_enabled', 'fusion_discovery_sources']
          const params = Object.fromEntries(Object.entries(snapshot).filter(([key]) =>
            allowed.includes(key) || ['enabled', 'owner', 'sources', 'daily_limit', 'tv_daily_limit', 'preset', 'legacy_isolated',
              'storage_verified'].some(name => key === 'fusion_execution_' + name)))
          const checked = await props.api.post(path + '/fusion/execution/preflight', params)
          if (!alive) return
          if (checked?.success !== true) {
            const entry = resultText[checked?.reason]
            if (entry) serverIssue.value = { key: entry[0], text: entry[1] }
            message.value = entry ? '' : '执行条件未通过检查。请核对自动订阅设置后重试。'
            return
          }
          if (!await persistDraft(snapshot)) return
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
        return { disabled: busy.value,
          ...(key === 'fusion_execution_daily_limit' ? {max:['personal','ranked'].includes(draft.value.fusion_execution_preset) ? 3 : 5} : {}),
          ...(issue ? { 'error-messages': issue.text } : {}) }
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
        if (!saved.value) return [textButton(personalSetup.value ? '保存并验证' : '保存', save,{key:'draft-save',color:'primary',variant:'flat',
          loading:busy.value,disabled:!loaded.value || busy.value || conflict.value || issues.value.length>0 ||
            (personalSetup.value && !!nextBinding.value)})]
        const intent = presented.value.selected
        const actions = [textButton(intent?.grant?'暂不授权':'完成',close,{key:'review-done',disabled:busy.value})]
        if (presented.value.verifyBinding) actions.push(textButton('验证 Emby 账号',verifySavedBinding,
          {key:'verify-binding',color:'primary',variant:'flat',loading:busy.value,disabled:busy.value}))
        if (intent) actions.push(textButton(intent.grant?'确认范围并授权':'撤销自动订阅授权',e=>act(intent,e),{
          key:intent.grant?'review-grant':'review-revoke',color:intent.grant?'primary':'error',variant:intent.grant?'flat':'tonal',
          disabled:busy.value || (intent.grant && !deliberate.value),'aria-describedby':'va-scope-effect',
          onKeydown:e=>{if(e.repeat && (e.key==='Enter' || e.key===' ')){e.preventDefault();e.stopPropagation()}}
        }))
        return actions
      }
      return () => h('section', { ref: container, class: 'va-shell va-config' }, [
        vaHeader(Vue,personalSetup.value ? '绑定我的 Emby' : '观影助手设置',close,busy.value),
        h('div', { class: 'pa-4 va-scroll' }, [
          !loaded.value && !message.value ? h('p', { role: 'status' }, '正在读取设置…') : null,
          !loaded.value && message.value ? textButton('重试', load, { disabled: busy.value }) : null,
          conflict.value ? textButton('重新读取设置', load, { disabled: busy.value }) : null,
          ...(loaded.value && !saved.value ? form.value.filter(node => !personalSetup.value || node.props?.['data-va-binding']).map(node => renderNode(Vue, node,
            { model: draft.value, setField, fieldProps, pluginId, renderAccountField:accountField,
              libraryPicker:LibraryPicker, api:props.api })) : []),
          saved.value && review.value ? h('section',{ref:scopeRegion,tabindex:-1,'aria-label':'已保存的自动订阅范围'},
            presented.value.scope.map(node=>renderNode(Vue,node,{pluginId}))) : null,
          saved.value && !review.value && !busy.value ? textButton('刷新授权状态', readSaved) : null,
        ]),
        message.value ? h('p', { role:'status',class:'text-body-2 px-4 py-2 border-t',style:'flex:none' }, message.value) : null,
        loaded.value && !saved.value && nextBinding.value && draft.value.fusion_execution_enabled !== true ?
          h('div',{class:'px-4 py-1 border-t'},[textButton('个人选片 · '+nextBinding.value.text,
            ()=>focusField(nextBinding.value.key),{disabled:busy.value,color:'primary'})]) : null,
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
