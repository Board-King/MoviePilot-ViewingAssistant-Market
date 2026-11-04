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
