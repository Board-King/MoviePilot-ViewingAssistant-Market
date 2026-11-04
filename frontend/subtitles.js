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
