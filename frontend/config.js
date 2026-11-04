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
  const preset = draft.fusion_execution_preset
  need(['ranked', 'conservative', 'balanced'].includes(preset), 'fusion_execution_preset', '选择订阅方式')
  const daily = draft.fusion_execution_daily_limit
  const max = preset === 'ranked' ? 3 : 5
  need(['string', 'number'].includes(typeof daily) && /^[1-5]$/.test(String(daily)) && Number(daily) <= max,
    'fusion_execution_daily_limit', '每日上限需为 1–' + max + ' 部')
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
      let openRankedDraft = boardBookmark?.target === 'ranked'
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
          if (openRankedDraft) {
            openRankedDraft = false
            draft.value.fusion_execution_preset = 'ranked'
            if (Number(draft.value.fusion_execution_daily_limit) > 3) draft.value.fusion_execution_daily_limit = 3
            nextTick(() => focusField('fusion_execution_preset'))
          }
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
        return { disabled: busy.value,
          ...(key === 'fusion_execution_daily_limit' ? {max:draft.value.fusion_execution_preset === 'ranked' ? 3 : 5} : {}),
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
