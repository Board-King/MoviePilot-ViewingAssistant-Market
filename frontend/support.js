// Render only the trigger until requested; do not include a bearer token in any DOM URL.
function makeSupport(Vue) {
  const { h, ref, nextTick } = Vue;
  return {
    name: 'ViewingAssistantSupport',
    props: { api: { required: true }, pluginId: { type: String, required: true } },
    setup(props) {
      const dialog = ref(null), trigger = ref(null);
      const state = ref('idle'), image = ref('');
      const route = () => 'plugin/' + encodeURIComponent(props.pluginId) + '/support';
      async function load() {
        if (state.value === 'loading') return;
        state.value = 'loading';
        try {
          const response = await props.api.get(route());
          const src = response?.image ?? response?.data?.image;
          if (typeof src !== 'string' || src.length > 700000 ||
              !/^data:image\/jpeg;base64,[A-Za-z0-9+/]+={0,2}$/.test(src)) throw Error();
          image.value = src;
          state.value = 'ready';
        } catch {
          state.value = 'error';
        }
      }
      async function open() {
        await nextTick();
        if (!dialog.value?.open) dialog.value?.showModal();
        if (state.value !== 'ready') await load();
      }
      function close() { if (dialog.value?.open) dialog.value.close(); }
      const palette = 'rgba(var(--v-theme-on-surface),.68)';
      const action = (label, onClick, extra = {}) => h('button',
        { type: 'button', onClick, class: 'v-btn v-btn--variant-text text-primary', ...extra }, label);
      const heart = () => h('svg', { width: 16, height: 16, viewBox: '0 0 24 24',
        fill: 'none', stroke: 'currentColor', 'stroke-width': 1.8, 'aria-hidden': 'true' },
        [h('path', { d: 'M12 20.1l-1.3-1.2C5.5 14.3 2.5 11.6 2.5 8.3a5.2 5.2 0 0 1 9.5-2.9 5.2 5.2 0 0 1 9.5 2.9c0 3.3-3 6-8.2 10.6z' })]);
      function supportBody() {
        return [
          h('p', { style: { color: palette, margin: '0 0 14px' } }, '谢谢支持！赞赏自愿，当前全部功能免费。'),
          h('div', { style: { textAlign: 'center' } }, [
            h('img', { src: image.value, alt: '疯狂的神父的微信赞赏码', width: 1152, height: 1152,
              style: 'display:block;width:min(100%,280px);height:auto;margin:0 auto 10px;border-radius:12px',
              onError: () => { image.value = ''; state.value = 'error'; } }),
            h('div', { style: { fontWeight: 600 } }, '打开微信「扫一扫」'),
            h('a', { href: image.value, download: '疯狂的神父-微信赞赏码.jpg', class: 'text-primary',
              style: 'display:inline-block;padding:8px' }, '保存赞赏码'),
          ]),
          h('p', { style: { color: palette, margin: '8px 0 0', fontSize: '.875rem' } },
            '建议可在微信赞赏时留言，我会看，不保证采纳。'),
        ];
      }
      return () => [
        action([heart(), h('span', '赞赏')], open, { ref: trigger, 'aria-haspopup': 'dialog', 'aria-label': '赞赏与建议',
          title: '赞赏与建议', class: 'v-btn v-btn--variant-text text-medium-emphasis',
          style: 'display:inline-flex;align-items:center;gap:4px;white-space:nowrap;min-width:auto;padding-inline:8px;font-size:.8125rem;font-weight:500' }),
        h('dialog', { ref: dialog, 'aria-label': '赞赏与建议', onClose: () => trigger.value?.focus(),
          onKeydown: e => { if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(); } },
          onClick: e => { if (e.target === dialog.value) {
            const r = dialog.value.getBoundingClientRect();
            if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) close();
          } },
          style: 'position:fixed;inset:0;margin:auto;width:min(410px,calc(100% - 24px));max-height:calc(100% - 32px);overflow:auto;overscroll-behavior:contain;border:0;border-radius:18px;padding:20px;background:rgb(var(--v-theme-surface,255 255 255));color:rgb(var(--v-theme-on-surface,32 32 40));box-shadow:0 14px 65px #0004;text-align:left;line-height:1.55',
        }, [
          h('div', { style: 'display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:10px' }, [
            h('h2', { class: 'text-h6', style: 'margin:0' }, '赞赏作者'),
            action('关闭', close, { style: 'min-width:auto;padding-inline:8px' }),
          ]),
          state.value === 'ready' ? supportBody()
            : h('div', { role: 'status' }, state.value === 'error'
              ? ['赞赏码未能加载。', action('重试', load)] : '正在载入…'),
        ]),
      ];
    },
  };
}
