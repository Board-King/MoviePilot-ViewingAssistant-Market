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
