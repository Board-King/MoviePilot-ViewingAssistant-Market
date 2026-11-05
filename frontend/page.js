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
