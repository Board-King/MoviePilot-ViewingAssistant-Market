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
