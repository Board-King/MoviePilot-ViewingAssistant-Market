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
