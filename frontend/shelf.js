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
