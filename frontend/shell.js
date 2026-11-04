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
