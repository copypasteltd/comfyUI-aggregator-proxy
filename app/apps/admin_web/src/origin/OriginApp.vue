<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

type Device = { id: string; slug: string; name: string; online: boolean; enabled: boolean; busy: boolean; reservation_state: string | null; queue_running: number | null; queue_pending: number | null; url: string }
const key = ref(''), csrf = ref(''), devices = ref<Device[]>([]), loading = ref(false), signed = ref(false), error = ref(''), notice = ref(''), busy = ref(''), updated = ref('')
const online = computed(() => devices.value.filter(row => row.online).length)
let timer: ReturnType<typeof setTimeout> | undefined, read: AbortController | null = null, disposed = false
async function call(path: string, method = 'GET', body?: unknown, signal?: AbortSignal) {
  const response = await fetch('/origin/api/' + path, { method, credentials: 'same-origin', cache: 'no-store', signal, headers: { ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}), ...(method !== 'GET' ? { 'X-Origin-CSRF': csrf.value } : {}) }, body: body !== undefined ? JSON.stringify(body) : undefined })
  const value = response.status === 204 ? {} : await response.json()
  if (!response.ok) { if (response.status === 401) signed.value = false; throw new Error(value.error?.message ?? `请求失败（${response.status}）`) }
  return value
}
function schedule() { clearTimeout(timer); if (!disposed && signed.value && !document.hidden) timer = setTimeout(() => void refresh(), 15000) }
async function refresh() {
  if (disposed || document.hidden || read) return
  const own = new AbortController(); read = own; loading.value = true
  const deadline = setTimeout(() => own.abort(), 20000)
  try { const value = await call('devices', 'GET', undefined, own.signal); if (!disposed && read === own) { devices.value = value.data; updated.value = new Date().toLocaleTimeString('zh-CN', { hour12: false }); error.value = '' } }
  catch (e) { if (!disposed && read === own && !own.signal.aborted) error.value = e instanceof Error ? e.message : '读取失败，请刷新' }
  finally { clearTimeout(deadline); if (read === own) { read = null; loading.value = false }; schedule() }
}
async function login() {
  if (loading.value || !key.value.trim()) return
  loading.value = true; error.value = ''
  try { const value = await call('session', 'POST', { api_key: key.value.trim() }); key.value = ''; csrf.value = value.csrf_token; signed.value = true; history.replaceState(null, '', '/origin/list'); await refresh() }
  catch (e) { error.value = e instanceof Error ? e.message : '登录失败' }
  finally { loading.value = false }
}
async function operate(device: Device, action: 'acquire' | 'release') {
  if (busy.value) return
  busy.value = device.slug; error.value = ''; notice.value = ''
  // Open synchronously so browser popup policies do not discard the native panel.
  const panel = action === 'acquire' ? window.open('about:blank', '_blank') : null
  if (panel) panel.opener = null
  try {
    const value = await call('devices/' + encodeURIComponent(device.slug) + '/' + action, 'POST')
    if (action === 'acquire') { if (panel) panel.location.replace(value.url); else window.location.assign(value.url) }
    else notice.value = value.state === 'closed' ? '设备已安全归还。' : '已停止接收新提交，现有任务完成并核对后归还设备。'
    await refresh()
  } catch (e) { panel?.close(); error.value = e instanceof Error ? e.message : '操作失败' }
  finally { busy.value = '' }
}
async function logout() {
  if (busy.value) return
  try { await call('session', 'DELETE'); csrf.value = ''; signed.value = false; devices.value = []; clearTimeout(timer); notice.value = '已退出。尚未归还的设备将在会话空闲到期、任务完成并核对后归还。'; history.replaceState(null, '', '/origin/') }
  catch (e) { error.value = e instanceof Error ? e.message : '退出失败' }
}
function status(device: Device) { return !device.enabled ? '未启用' : !device.online ? '离线' : device.reservation_state === 'active' ? '由你独占' : device.reservation_state ? '正在核对' : device.busy ? '执行 / 维护中' : '可申请' }
function visibility() { if (document.hidden) { clearTimeout(timer); read?.abort(); read = null; loading.value = false } else if (signed.value) void refresh() }
onMounted(async () => { document.addEventListener('visibilitychange', visibility); try { const value = await call('session'); csrf.value = value.csrf_token; signed.value = true; await refresh() } catch { /* Signed-out landing is expected. */ } })
onBeforeUnmount(() => { disposed = true; clearTimeout(timer); read?.abort(); document.removeEventListener('visibilitychange', visibility) })
</script>

<template>
  <div class="origin-shell">
    <header class="origin-header"><a href="/origin/" class="brand"><span aria-hidden="true">↗</span> COMFY / ORIGINAL</a><nav><a href="/skill.md" target="_blank" rel="noopener">API 使用说明</a><button v-if="signed" class="secondary" :disabled="!!busy" @click="logout">退出登录</button></nav></header>
    <main>
      <section v-if="!signed" class="login-layout">
        <div class="intro"><p class="eyebrow">YOUR WORKFLOW. YOUR CANVAS.</p><h1>打开设备，<br>自由构建。</h1><p>在原生 ComfyUI 中编排节点、上传素材，运行你自己的工作流。</p><div class="intro-footer"><span class="dot"/> 同一入口 · 按 Key 授权设备</div></div>
        <form class="login-card" @submit.prevent="login"><p class="eyebrow">DEVICE WORKSPACE</p><h2>进入原生工作台</h2><label for="api-key">API Key</label><input id="api-key" v-model="key" type="password" autocomplete="off" spellcheck="false" placeholder="输入已开通原生权限的 Key" required maxlength="256"><p class="hint">管理员需在 Key 管理中单独开通设备访问。</p><button class="primary" type="submit" :disabled="loading || !key.trim()">{{ loading ? '正在验证…' : '验证并查看设备 ↗' }}</button><p v-if="error" role="alert" class="error">{{ error }}</p><p v-if="notice" role="status" class="notice">{{ notice }}</p><p class="login-note">Key 不会写入浏览器持久存储。登录会话可随时退出。</p></form>
      </section>
      <template v-else>
        <section class="workspace-heading"><div><p class="eyebrow">NATIVE DEVICE WORKSPACE</p><h1>选择你的画布。</h1><p>独占操作期间，该设备暂停接收 API 新派单；正在执行的任务会保留。</p></div><button class="secondary" :disabled="loading || !!busy" @click="refresh">{{ loading ? '正在刷新…' : '刷新设备' }}</button></section>
        <section class="summary" aria-label="设备统计"><div><span>获准设备</span><strong>{{ devices.length }}</strong></div><div><span>当前在线</span><strong>{{ online }}</strong></div><div class="refresh-time"><span>最近刷新</span><b>{{ updated || '等待读取' }}</b></div></section>
        <p v-if="error" role="alert" class="error">{{ error }}</p><p v-if="notice" role="status" class="notice">{{ notice }}</p>
        <section class="device-grid" aria-label="获准访问的 ComfyUI 设备"><article v-for="device in devices" :key="device.id" class="device-card"><div class="card-top"><span class="device-tag">COMFYUI</span><span class="status" :class="{ available: device.online && !device.busy }">{{ status(device) }}</span></div><h2>{{ device.name }}</h2><p class="device-slug">/origin/{{ device.slug }}/</p><div class="queue"><div><span>正在执行</span><strong>{{ device.queue_running ?? '—' }}</strong></div><div><span>源端排队</span><strong>{{ device.queue_pending ?? '—' }}</strong></div></div><div class="device-actions"><button class="primary" :disabled="!!busy || !device.online || !device.enabled || (!!device.reservation_state && device.reservation_state !== 'active') || (device.busy && device.reservation_state !== 'active')" @click="operate(device, 'acquire')">{{ busy === device.slug ? '正在处理…' : device.reservation_state === 'active' ? '继续原生操作 ↗' : '独占并打开面板 ↗' }}</button><button v-if="device.reservation_state" class="secondary" :disabled="!!busy" @click="operate(device, 'release')">归还设备</button></div></article></section>
        <section v-if="!devices.length && !loading" class="empty"><h2>此 Key 尚未获准访问设备</h2><p>请联系管理员在原生面板权限中选择设备，随后刷新。</p></section>
        <aside class="workspace-note"><strong>原生模式的文件保存在设备。</strong><p>这里提交的自定义流程独立登记，不会自动进入平台内容库或云端八小时清理。请及时下载需要的结果。关闭页面不代表任务停止，使用结束后请点击“归还设备”。</p></aside>
      </template>
    </main><footer>COMFY / ORIGINAL <span>自定义流程 · 实时预览 · 原生编辑器</span></footer>
  </div>
</template>

<style>
*{box-sizing:border-box}body{margin:0;background:var(--rc-canvas);color:var(--rc-ink);font-family:var(--rc-font-body);font-size:14px;line-height:1.6}button,input{font:inherit}button,a,input{outline-offset:4px}button:focus-visible,a:focus-visible,input:focus-visible{outline:3px solid var(--rc-ink)}a{color:inherit}button{cursor:pointer}button:disabled{opacity:.5;cursor:not-allowed}.origin-shell{max-width:1360px;margin:auto;padding:0 40px}.origin-header{display:flex;align-items:center;justify-content:space-between;padding:28px 0;border-bottom:1px solid var(--rc-line);gap:20px}.brand{font-weight:900;text-decoration:none;letter-spacing:.06em;display:flex;align-items:center;gap:12px}.brand span{background:var(--rc-ink);color:var(--rc-lime);font-size:28px;width:42px;height:42px;display:grid;place-items:center;border-radius:50%}.origin-header nav{display:flex;align-items:center;gap:24px}.origin-header nav a{text-decoration:none;font-weight:700}.primary,.secondary{border-radius:var(--rc-radius-pill);padding:13px 22px;font-weight:800;border:1px solid var(--rc-ink);transition:background var(--rc-motion)}.primary{background:var(--rc-lime);color:var(--rc-ink)}.primary:hover:not(:disabled){background:var(--rc-lime-hover)}.secondary{background:var(--rc-card);color:var(--rc-ink)}.eyebrow{font-size:11px;font-weight:800;letter-spacing:.15em}.login-layout{display:grid;grid-template-columns:1.1fr 1fr;min-height:640px;padding:64px 0;gap:48px;align-items:center}.intro{padding:44px;border-radius:var(--rc-radius-hero);background:var(--rc-lime);min-height:450px;display:flex;flex-direction:column;justify-content:center}.intro h1{font-size:clamp(42px,5vw,64px);line-height:1.2;letter-spacing:-.05em;margin:24px 0}.intro>p:not(.eyebrow){max-width:330px}.intro-footer{display:flex;align-items:center;gap:10px;margin-top:36px;font-weight:700}.dot{display:inline-block;background:var(--rc-ink);height:8px;width:8px;border-radius:50%}.login-card{background:var(--rc-card);border:1px solid var(--rc-line);padding:36px;border-radius:var(--rc-radius-panel);max-width:480px}.login-card h2{font-size:27px;margin:12px 0 30px}.login-card label{display:block;font-weight:800;margin-bottom:8px}.login-card input{width:100%;padding:15px 16px;border:1px solid var(--rc-line-strong);border-radius:var(--rc-radius-control);background:var(--rc-canvas);color:var(--rc-ink)}.hint,.login-note{font-size:12px;color:var(--rc-ink-2)}.login-card .primary{width:100%;margin:16px 0}.login-note{border-top:1px dashed var(--rc-line-strong);padding-top:20px;margin-top:18px}.workspace-heading{display:flex;justify-content:space-between;align-items:center;gap:24px;margin:44px 0 30px}.workspace-heading h1{font-size:40px;letter-spacing:-.05em;margin:8px 0}.workspace-heading p:last-child{color:var(--rc-ink-2);max-width:650px}.summary{display:grid;grid-template-columns:1fr 1fr 1.3fr;background:var(--rc-ink);color:var(--rc-card);border-radius:var(--rc-radius-card);padding:24px 32px;gap:24px}.summary>div{display:flex;justify-content:space-between;align-items:center;gap:16px}.summary strong{font-family:var(--rc-font-display);font-size:40px;font-weight:400;color:var(--rc-lime);line-height:1}.summary span{color:var(--rc-on-dark-muted)}.device-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:20px;margin:30px 0}.device-card{background:var(--rc-card);border:1px solid var(--rc-line);border-radius:var(--rc-radius-panel);padding:26px;display:flex;flex-direction:column}.card-top{display:flex;justify-content:space-between;align-items:center;gap:10px}.device-tag{font-size:10px;letter-spacing:.14em;font-weight:900}.status{font-size:12px;padding:5px 10px;border-radius:999px;background:var(--rc-canvas);font-weight:700}.status.available{background:var(--rc-lime-soft);color:var(--rc-success)}.device-card h2{font-size:22px;margin:30px 0 4px;overflow-wrap:anywhere}.device-slug{font-family:var(--rc-font-mono);font-size:12px;color:var(--rc-ink-2);overflow-wrap:anywhere}.queue{display:grid;grid-template-columns:1fr 1fr;border-top:1px dashed var(--rc-line-strong);margin:20px 0 26px;padding-top:20px;gap:20px}.queue div{display:flex;flex-direction:column}.queue span{font-size:12px;color:var(--rc-ink-2)}.queue strong{font-family:var(--rc-font-display);font-size:34px;font-weight:400;line-height:1.4}.device-actions{display:flex;flex-wrap:wrap;gap:10px;margin-top:auto}.device-actions .primary{flex:1;white-space:nowrap}.workspace-note{background:var(--rc-lime-soft);border-radius:var(--rc-radius-card);padding:22px 26px;margin:30px 0}.workspace-note p{margin-bottom:0;color:var(--rc-ink-2);max-width:1000px}.error{background:var(--rc-danger-soft);color:var(--rc-danger);padding:14px 18px;border-radius:var(--rc-radius-control);overflow-wrap:anywhere}.notice{padding:14px 18px;background:var(--rc-lime-soft);border-radius:var(--rc-radius-control)}.empty{padding:40px 24px;border:1px dashed var(--rc-line-strong);border-radius:var(--rc-radius-panel);margin:30px 0}footer{border-top:1px solid var(--rc-line);display:flex;justify-content:space-between;gap:20px;padding:24px 0;margin-top:40px;font-size:11px;font-weight:800;letter-spacing:.08em}footer span{font-weight:400;color:var(--rc-ink-2)}@media(max-width:760px){.origin-shell{padding:0 20px}.origin-header{padding:20px 0}.origin-header nav{gap:12px}.brand{font-size:11px;gap:8px}.brand span{width:32px;height:32px;font-size:23px}.origin-header nav a{display:none}.login-layout{grid-template-columns:1fr;padding:28px 0;gap:20px;min-height:auto}.intro{padding:30px;min-height:300px}.intro h1{font-size:44px;margin:16px 0}.intro-footer{margin-top:20px}.login-card{padding:26px;max-width:none}.workspace-heading{align-items:flex-start;flex-direction:column;margin-top:30px}.workspace-heading h1{font-size:34px}.summary{padding:20px;gap:20px;grid-template-columns:1fr 1fr}.summary .refresh-time{grid-column:1/-1;font-size:12px}.device-grid{grid-template-columns:1fr}.device-card{padding:24px}footer{flex-direction:column;gap:6px}}@media(prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important}}
</style>
