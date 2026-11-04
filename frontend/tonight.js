const vaTonightStyle = `
.va-tonight{padding:16px;border-radius:8px;border:1px solid rgba(var(--v-border-color),var(--v-border-opacity));background:rgb(var(--v-theme-surface));margin-bottom:16px}
.va-tonight header,.va-tonight-controls,.va-tonight-filters,.va-tonight-meta{display:flex;align-items:center;flex-wrap:wrap;gap:6px 12px}
.va-tonight header{justify-content:space-between;gap:10px}
.va-tonight-controls{gap:4px}
.va-tonight-filters{margin-top:12px;padding:8px 0;border-top:1px solid rgba(var(--v-theme-on-surface),.10)}
.va-tonight-filters label{display:inline-flex;align-items:center;gap:6px;min-height:36px}
.va-tonight-filters select{color:inherit;border:1px solid rgba(var(--v-theme-on-surface),.28);border-radius:6px;padding:4px 8px;background:rgb(var(--v-theme-surface));appearance:auto}
.va-tonight-filters input{accent-color:rgb(var(--v-theme-primary))}
.va-tonight-results{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,190px),1fr));gap:8px;list-style:none;margin-top:12px;padding:0}
.va-tonight-pick{display:flex;flex-direction:column;align-items:start;gap:4px;min-width:0;padding:10px 12px;background:rgba(var(--v-theme-on-surface),.04);border-radius:8px;overflow-wrap:anywhere}
.va-tonight-pick strong{font-size:.95rem}.va-tonight-meta{gap:2px 7px;font-size:.75rem;color:rgba(var(--v-theme-on-surface),.75)}
.va-tonight-meta>span{white-space:nowrap}.va-tonight-meta>span+span:before{content:'·';margin-inline-end:7px}
.va-tonight-pick a{display:inline-flex;align-items:center;min-height:32px;margin-top:4px;padding:3px 8px;border-radius:6px;font-size:.8125rem;font-weight:500;color:rgb(var(--v-theme-primary));background:rgba(var(--v-theme-primary),.09);text-decoration:none}
.va-tonight-pick a:hover{background:rgba(var(--v-theme-primary),.16)}
.va-tonight-message{margin-top:8px;font-size:.85rem}
@media(pointer:coarse){.va-tonight :is(button,a,select){min-height:44px}}
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
    SETUP_REQUIRED:'先在观影偏好中绑定 Emby 用户并选择媒体库。',
    VERIFY_REQUIRED:'已保存的 Emby 用户需要验证后才能选片。',
    VERIFY_FAILED:'绑定验证未通过，请检查 Emby 用户和可见媒体库。',
    SCOPE_REQUIRED:'请在观影偏好中选择允许使用的媒体库。',
    LIBRARY_NOT_VISIBLE:'已选媒体库的权限发生变化，请重新选择。',
    FORBIDDEN:'当前账号没有查看这份个人片单的权限。',
    SIGN_IN:'请重新登录 MoviePilot，再打开个人片单。',
    BUSY:'片单正在读取，请稍后再试。',
    RATE_LIMITED:'Emby 暂时限流，请稍后再试。',
    UNAVAILABLE:'暂时读不到片单，请稍后再试。',
  }
  return {
    name:'ViewingAssistantTonight', props:['api','pluginId','initial','onSettings'],
    setup(props) {
      const maxMinutes = ref(0), chinese = ref(false), filtersOpen = ref(false)
      const durationInput = ref(null)
      const items = ref(null), limited = ref(false), pending = ref(''), reason = ref('')
      let generation = 0, controller
      function reset() {
        generation++
        controller?.abort()
        controller = null
        pending.value = ''; reason.value = ''; items.value = null; limited.value = false
      }
      watch(() => props.initial, reset)
      onUnmounted(reset)
      function changeFilter(field, value) {
        if (pending.value === 'validate' || field.value === value) return
        reset()
        field.value = value
      }
      function toggleFilters() {
        filtersOpen.value = !filtersOpen.value
        if (filtersOpen.value) Vue.nextTick(() => durationInput.value?.focus())
      }
      async function read(verify = false) {
        if (pending.value) return
        reset()
        const stamp = generation, path = 'plugin/' + props.pluginId + '/fusion/'
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
            max_minutes:maxMinutes.value, chinese_subtitles:chinese.value
          }, signal:controller.signal})
          if (stamp !== generation) return
          if (result?.success !== true) {
            reason.value = Object.hasOwn(messages, result?.reason) ? result.reason : 'UNAVAILABLE'
            return
          }
          if (!Array.isArray(result.items) || result.items.length > 3 || result.items.some(
            item => !item || typeof item.id !== 'string' || typeof item.title !== 'string' || !item.title.trim()
          )) throw Error('invalid picks')
          items.value = result.items
          limited.value = result.limited === true
        } catch (error) {
          if (stamp !== generation) return
          const status = error?.response?.status ?? error?.status
          reason.value = status === 401 ? 'SIGN_IN' : status === 403 ? 'FORBIDDEN' : 'UNAVAILABLE'
        } finally {
          if (stamp === generation) { controller = null; pending.value = '' }
        }
      }
      function pick(item) {
        const url = vaTonightLink(item.url, item.id)
        const meta = []
        if (Number.isInteger(item.year)) meta.push(String(item.year))
        if (Number.isInteger(item.minutes) && item.minutes > 0) meta.push(item.minutes + ' 分钟')
        if (item.chinese_subtitles === true) meta.push('中文字幕')
        if (Number.isFinite(item.score) && item.score >= 0 && item.score <= 100) meta.push('口味 ' + item.score)
        return h('li',{class:'va-tonight-pick',key:item.id},[
          h('strong',item.title),
          meta.length ? h('span',{class:'va-tonight-meta'},meta.map(text => h('span',text))) : null,
          item.version ? h('small','版本 · ' + item.version) : null,
          item.reason ? h('small',item.reason) : null,
          url ? h('a',{href:url,target:'_blank',rel:'noopener noreferrer',
            'aria-label':'在 Emby 打开《' + item.title + '》'},'在 Emby 打开 ↗') :
            h('small',{class:'text-medium-emphasis'},'请在 Emby 中查找此片')
        ])
      }
      return () => {
        const needsSetup = ['SETUP_REQUIRED','VERIFY_FAILED','SCOPE_REQUIRED','LIBRARY_NOT_VISIBLE'].includes(reason.value)
        const needsVerify = reason.value === 'VERIFY_REQUIRED'
        const setup = needsSetup && props.onSettings
        const hasFilters = maxMinutes.value > 0 || chinese.value
        const widen = items.value?.length === 0 && hasFilters
        const denied = reason.value === 'SIGN_IN' || reason.value === 'FORBIDDEN'
        const filterText = [maxMinutes.value ? maxMinutes.value + ' 分钟内' : '', chinese.value ? '中文字幕' : '']
          .filter(Boolean).join(' · ')
        const btn = (label, attrs) => h(resolveComponent('VBtn'),{size:'small',...attrs},{default:()=>label})
        const empty = hasFilters ? '没有找到符合这些条件的电影，可以放宽筛选。' : '这次没有找到可推荐的电影。'
        return h('section',{class:'va-tonight','aria-label':'今晚看什么'},[
          h('style',vaTonightStyle),
          h('header',[
            h('div',[h('h2',{class:'text-subtitle-1 font-weight-bold'},'今晚看什么'),
              h('p',{class:'text-caption text-medium-emphasis'},'从我的 Emby 片库挑选，最多三部。')]),
            denied ? null : h('div',{class:'va-tonight-controls'},[
              btn(filterText || '筛选',{variant:'text','aria-expanded':filtersOpen.value,
                'aria-label':filterText ? '筛选：' + filterText : '筛选',onClick:toggleFilters}),
              btn(setup ? '设置观影偏好' : needsVerify ? '验证绑定' : reason.value ? '重试' :
                  widen ? '不限条件再挑' : items.value ? '刷新片单' : '帮我挑片',{
                color:'primary',variant:'flat',loading:!!pending.value,disabled:!!pending.value,
                onClick:()=>{
                  if (setup) return props.onSettings('tonight')
                  if (widen) { maxMinutes.value=0; chinese.value=false }
                  return read(needsVerify)
                }})
            ])
          ]),
          filtersOpen.value && !denied ? h('div',{class:'va-tonight-filters',role:'group','aria-label':'本次选片条件'},[
            h('label',['片长', h('select',{ref:durationInput,value:maxMinutes.value,disabled:pending.value==='validate',
              onChange:e=>changeFilter(maxMinutes,Number(e.target.value))},
              [0,90,100,120].map(n=>h('option',{value:n},n ? n+' 分钟以内' : '不限')))]),
            h('label',[h('input',{type:'checkbox',checked:chinese.value,disabled:pending.value==='validate',
              onChange:e=>changeFilter(chinese,e.target.checked)}),'仅有中文字幕'])
          ]) : null,
          h('div',{role:'status','aria-live':'polite'},[
            reason.value ? h('p',{class:'va-tonight-message'},messages[reason.value]) : null,
            pending.value ? h('p',{class:'va-tonight-message'},pending.value === 'validate' ? '正在验证绑定…' : '正在片库中挑选…') : null,
            items.value ? (items.value.length ? h('ol',{class:'va-tonight-results'},items.value.map(pick)) :
              h('p',{class:'va-tonight-message'},empty)) : null,
            limited.value ? h('small',{class:'text-medium-emphasis'},'本次只核对了部分片库。') : null
          ])
        ])
      }
    }
  }
}
