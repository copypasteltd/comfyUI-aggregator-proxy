<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh } from '@element-plus/icons-vue'
import { api, readableError, revisionHeaders, time, unwrap } from '../api'
import type { components } from '../generated/api'
import { useSession } from '../session'

type Node = components['schemas']['WorkerNodeView']
type Detail = components['schemas']['WorkerNodeDetail']
type Mutation = components['schemas']['WorkerNodeMutation']
type Action = components['schemas']['WorkerNodeAction']['action']
const session = useSession(), canManage = computed(() => session.allows('nodes:manage'))
const rows = ref<Node[]>([]), page = ref(1), total = ref(0), hasMore = ref(false), snapshot = ref(0)
const q = ref(''), state = ref<'' | 'enabled' | 'disabled' | 'archived'>(''), error = ref(''), loading = ref(false), saving = ref(false)
const applied = ref({ q: '', state: state.value })
const editOpen = ref(false), editing = ref<Node | null>(null), name = ref(''), formError = ref('')
const inspecting = ref<Node | null>(null)
const detailOpen = ref(false), detail = ref<Detail | null>(null), detailError = ref(''), detailBusy = ref(false)
const secretOpen = ref(false), secret = ref(''), secretNode = ref('')
let listRequest: AbortController | undefined, detailRequest: AbortController | undefined, timer: number | undefined
const mutations = new Set<AbortController>()
// Bounded retry slots retain an idempotency key for an unchanged failed request.
const pending: Partial<Record<'create' | 'rename' | 'action', { signature: string; token: string }>> = {}
function retryToken(slot: 'create' | 'rename' | 'action', body: unknown) {
  const signature = JSON.stringify(body)
  if (pending[slot]?.signature !== signature) pending[slot] = { signature, token: crypto.randomUUID() }
  return pending[slot]!.token
}
function status(row: Node) {
  if (row.archived_at) return '已归档'
  if (!row.enabled) return '已停用'
  if (!row.boot_registered || !row.observed_at) return '等待 Worker 登记'
  return snapshot.value - row.observed_at <= 15 ? '在线' : '观测已过期'
}
function statusType(row: Node) { return status(row) === '在线' ? 'success' : row.enabled && !row.archived_at ? 'warning' : 'info' }
async function refresh() {
  if (document.hidden) return
  listRequest?.abort(); const request = new AbortController(); listRequest = request; loading.value = true
  try {
    const result = unwrap(await api.GET('/admin/api/v1/worker-nodes', { signal: request.signal, params: { query: { page: page.value, q: applied.value.q, state: applied.value.state || undefined } } }))
    if (!request.signal.aborted) { rows.value = result.data; total.value = result.total; hasMore.value = result.has_more; snapshot.value = result.snapshot_at; error.value = '' }
  } catch (e) { if (!request.signal.aborted) error.value = readableError(e) }
  finally { if (listRequest === request) loading.value = false }
}
async function filter() { applied.value = { q: q.value.trim(), state: state.value }; page.value = 1; await refresh() }
async function goPage(delta: number) { page.value += delta; await refresh() }
function openEdit(row?: Node) { editing.value = row ?? null; name.value = row?.name ?? ''; formError.value = ''; editOpen.value = true }
function present(result: Mutation) {
  if (result.secret_available && result.secret && !document.hidden) {
    secret.value = result.secret; secretNode.value = result.node.id; secretOpen.value = true
  } else if (result.secret_available === false) {
    ElMessage.info('操作结果已确认。如未保存首次凭证，可在节点空闲时轮换凭证。')
  }
}
async function save() {
  if (saving.value) return
  if (!name.value.trim()) { formError.value = '请填写 Windows 节点名称'; return }
  const row = editing.value, body = row ? { name: name.value.trim(), expected_revision: row.revision } : { name: name.value.trim() }
  const slot = row ? 'rename' : 'create', token = retryToken(slot, { id: row?.id, body })
  const request = new AbortController(); mutations.add(request); saving.value = true; formError.value = ''
  try {
    const result = row
      ? unwrap(await api.PATCH('/admin/api/v1/worker-nodes/{identifier}', { signal: request.signal, params: { path: { identifier: row.id } }, headers: { ...revisionHeaders(row.revision), 'Idempotency-Key': token }, body: { name: body.name, expected_revision: row.revision } }))
      : unwrap(await api.POST('/admin/api/v1/worker-nodes', { signal: request.signal, headers: { 'Idempotency-Key': token }, body: { name: body.name } }))
    if (request.signal.aborted) return
    delete pending[slot]; editOpen.value = false
    if (!row) present(result)
    else ElMessage.success('节点名称已保存')
    await refresh()
  } catch (e) { if (!request.signal.aborted) formError.value = readableError(e) }
  finally { mutations.delete(request); saving.value = false }
}
const descriptions: Record<Action, string> = {
  enable: '恢复接入后请重启 Worker，以新的启动标识登记硬件，再验证渠道和 Workflow。',
  disable: '节点空闲时才能停用。停用后 Worker 需要重新启动登记才能恢复接入。',
  rotate_credential: '仅允许空闲节点轮换。旧凭证和启动标识将失效，新凭证只显示一次；请更新 Windows 凭证文件并重启 Worker。',
  archive: '请先停用节点并删除全部渠道。归档后保留历史记录，无法恢复。',
}
const labels: Record<Action, string> = { enable: '恢复接入', disable: '停用节点', rotate_credential: '轮换凭证', archive: '归档节点' }
async function act(row: Node, kind: Action) {
  if (saving.value) return
  try { await ElMessageBox.confirm(descriptions[kind], labels[kind] + ' · ' + row.name, { type: 'warning', confirmButtonText: labels[kind], cancelButtonText: '取消' }) }
  catch { return }
  if (document.hidden || saving.value) return
  const body = { action: kind, expected_revision: row.revision }, token = retryToken('action', { id: row.id, body })
  const request = new AbortController(); mutations.add(request); saving.value = true; error.value = ''
  try {
    const result = unwrap(await api.POST('/admin/api/v1/worker-nodes/{identifier}/actions', { signal: request.signal, params: { path: { identifier: row.id } }, headers: { ...revisionHeaders(row.revision), 'Idempotency-Key': token }, body }))
    if (request.signal.aborted) return
    delete pending.action
    if (kind === 'rotate_credential') present(result)
    else ElMessage.success(labels[kind] + '已完成')
    await refresh()
    if (detailOpen.value && detail.value?.id === row.id) await inspect(row)
  } catch (e) { if (!request.signal.aborted) error.value = readableError(e) }
  finally { mutations.delete(request); saving.value = false }
}
async function inspect(row: Node) {
  detailRequest?.abort(); const request = new AbortController(); detailRequest = request
  inspecting.value = row; detail.value = null; detailOpen.value = true; detailBusy.value = true; detailError.value = ''
  try {
    const result = unwrap(await api.GET('/admin/api/v1/worker-nodes/{identifier}', { signal: request.signal, params: { path: { identifier: row.id } } }))
    if (!request.signal.aborted) detail.value = result
  } catch (e) { if (!request.signal.aborted) detailError.value = readableError(e) }
  finally { if (detailRequest === request) detailBusy.value = false }
}
function clearSecret() { secret.value = ''; secretNode.value = ''; secretOpen.value = false }
async function copySecret() {
  if (!secret.value) return
  try { await navigator.clipboard.writeText(JSON.stringify({ node_id: secretNode.value, token: secret.value }, null, 2)); ElMessage.success('凭证 JSON 已复制，请以 UTF-8 保存到 Windows 私密文件') }
  catch { ElMessage.error('复制失败，请手动保存凭证') }
}
function stop() {
  window.clearInterval(timer); timer = undefined; listRequest?.abort(); detailRequest?.abort()
  for (const request of mutations) request.abort()
  clearSecret()
}
function resume() {
  stop()
  if (!document.hidden) { void refresh(); if (detailOpen.value && inspecting.value) void inspect(inspecting.value); timer = window.setInterval(() => { if (!saving.value && !loading.value) void refresh() }, 5000) }
}
watch(secretOpen, open => { if (!open) { secret.value = ''; secretNode.value = '' } })
watch(detailOpen, open => { if (!open) { detailRequest?.abort(); detail.value = null; inspecting.value = null } })
onMounted(() => { document.addEventListener('visibilitychange', resume); window.addEventListener('session-expired', stop); resume() })
onUnmounted(() => { document.removeEventListener('visibilitychange', resume); window.removeEventListener('session-expired', stop); stop() })
</script>

<template>
  <div class="page-heading"><div><p class="eyebrow">INFRASTRUCTURE / WINDOWS NODES</p><h1>Windows 节点</h1><p class="muted">登记服务器、查看硬件与占用，管理 Worker 接入凭证。</p></div><div class="toolbar"><el-button :icon="Refresh" :loading="loading" @click="refresh">刷新</el-button><el-button v-if="canManage" type="primary" :icon="Plus" :disabled="saving" @click="openEdit()">登记节点</el-button></div></div>
  <section class="node-setup" aria-label="Windows 接入顺序"><div><span class="node-step">01</span><strong>登记节点</strong><p>命名服务器并保存一次性凭证</p></div><div><span class="node-step">02</span><strong>Worker 上报 GPU</strong><p>配置 Windows Worker 后启动接入</p></div><div><span class="node-step">03</span><router-link v-if="session.allows('channels:read')" to="/channels"><strong>添加 ComfyUI 渠道 →</strong></router-link><strong v-else>添加 ComfyUI 渠道</strong><p>选择物理 GPU，验证 Workflow</p></div></section>
  <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon/>
  <el-form class="node-filters" inline @submit.prevent="filter"><el-form-item label="名称或节点 ID"><el-input v-model="q" placeholder="搜索 Windows 节点" maxlength="128" clearable/></el-form-item><el-form-item label="节点状态"><el-select v-model="state" class="node-state"><el-option label="全部状态" value=""/><el-option label="已启用" value="enabled"/><el-option label="已停用" value="disabled"/><el-option label="已归档" value="archived"/></el-select></el-form-item><el-form-item><el-button native-type="submit" :disabled="loading">应用筛选</el-button></el-form-item></el-form>
  <section class="panel"><div class="panel-heading"><h2>节点清单 <span class="muted">{{ total }} 台</span></h2><span class="muted">每 5 秒更新 · {{ time(snapshot) }}</span></div>
    <el-table :data="rows" v-loading="loading" empty-text="暂无 Windows 节点。登记后启动 Worker，上报的硬件将在此显示。" table-layout="auto">
      <el-table-column label="Windows 节点" min-width="220"><template #default="{ row }"><strong>{{ row.name }}</strong><span class="id-line">{{ row.id }}</span></template></el-table-column>
      <el-table-column label="状态" min-width="155"><template #default="{ row }"><el-tag size="small" :type="statusType(row)">{{ status(row) }}</el-tag><span class="id-line">版本 {{ row.revision }}</span></template></el-table-column>
      <el-table-column label="GPU / 占用" min-width="130"><template #default="{ row }"><span class="mono">{{ row.gpu_count }} / {{ row.activity.held_gpus }}</span><span v-if="row.activity.uncertain_gpus" class="node-warning">含 {{ row.activity.uncertain_gpus }} 个未知占用</span></template></el-table-column>
      <el-table-column label="活动尝试 / 管理中" min-width="150"><template #default="{ row }"><span class="mono">{{ row.activity.open_attempts }} / {{ row.activity.running_commands }}</span><span v-if="row.activity.management_holds" class="id-line">{{ row.activity.management_holds }} 个管理保留</span></template></el-table-column>
      <el-table-column label="最近上报" min-width="175"><template #default="{ row }">{{ time(row.observed_at) }}</template></el-table-column>
      <el-table-column label="操作" min-width="360"><template #default="{ row }"><div class="actions"><el-button text size="small" @click="inspect(row)">详情</el-button><template v-if="canManage && !row.archived_at"><el-button text size="small" :disabled="saving" @click="openEdit(row)">改名</el-button><el-button v-if="row.enabled" text size="small" :disabled="saving || !row.can_disable" title="需无活动尝试、GPU 占用或管理操作" @click="act(row, 'disable')">停用</el-button><el-button v-else text size="small" :disabled="saving" @click="act(row, 'enable')">恢复接入</el-button><el-button text size="small" :disabled="saving || !row.can_rotate" title="仅空闲节点可轮换" @click="act(row, 'rotate_credential')">轮换凭证</el-button><el-button text type="danger" size="small" :disabled="saving || !row.can_archive" title="需先停用并删除全部渠道" @click="act(row, 'archive')">归档</el-button></template></div></template></el-table-column>
    </el-table><div class="pagination"><el-button size="small" :disabled="page === 1 || loading" @click="goPage(-1)">上一页</el-button><span>第 {{ page }} 页</span><el-button size="small" :disabled="!hasMore || loading" @click="goPage(1)">下一页</el-button></div>
  </section>
  <p class="form-help">停用、归档和轮换要求先排空节点。未知 GPU 占用需要完成核对，等待租约过期不能代替核对。</p>
  <el-dialog v-model="editOpen" :title="editing ? '修改节点名称' : '登记 Windows 节点'" width="min(480px, 94vw)" :close-on-click-modal="false"><el-alert v-if="formError" :title="formError" type="error" :closable="false"/><el-form label-position="top" @submit.prevent="save"><el-form-item label="Windows 节点名称" required><el-input v-model="name" maxlength="128" autocomplete="off" placeholder="例如：渲染服务器 01"/></el-form-item><p v-if="!editing" class="form-help">登记后显示 Worker 凭证；请保存到 Windows 的私密凭证文件，再启动 Worker 上报 GPU。</p><div class="drawer-footer"><el-button :disabled="saving" @click="editOpen = false">取消</el-button><el-button native-type="submit" type="primary" :loading="saving">{{ editing ? '保存名称' : '登记并生成凭证' }}</el-button></div></el-form></el-dialog>
  <el-dialog v-model="secretOpen" destroy-on-close title="保存 Worker 凭证" width="min(620px, 94vw)" :close-on-click-modal="false" @closed="clearSecret"><el-alert title="凭证仅显示一次，关闭或隐藏页面后即清除。" type="warning" :closable="false"/><p class="node-credential-id mono">{{ secretNode }}</p><el-input :model-value="secret" type="password" readonly show-password autocomplete="off" aria-label="一次性 Worker 凭证"/><div class="node-credential-actions"><el-button @click="copySecret">复制凭证 JSON</el-button><el-button type="primary" @click="clearSecret">已保存，关闭</el-button></div><p class="form-help">点击「复制凭证 JSON」，将包含节点 ID 和 token 的内容以 UTF-8 保存到 Windows 私密凭证文件，再配置控制服务地址并启动 Worker。轮换后请更新文件并重启 Worker。</p></el-dialog>
  <el-drawer v-model="detailOpen" title="Windows 节点详情" size="min(740px, 100vw)"><el-alert v-if="detailError" :title="detailError" type="error" :closable="false"/><div v-loading="detailBusy"><template v-if="detail"><h2>{{ detail.name }}</h2><p class="id-line">{{ detail.id }}</p><el-descriptions :column="1" border class="node-details"><el-descriptions-item label="登记状态">{{ status(detail) }}</el-descriptions-item><el-descriptions-item label="创建时间">{{ time(detail.created_at) }}</el-descriptions-item><el-descriptions-item label="最近上报">{{ time(detail.observed_at) }}</el-descriptions-item><el-descriptions-item label="凭证标识">{{ detail.credential_hint }}</el-descriptions-item><el-descriptions-item label="未关闭尝试 / GPU 占用">{{ detail.activity.open_attempts }} / {{ detail.activity.held_gpus }}</el-descriptions-item><el-descriptions-item label="未知占用 / 管理保留">{{ detail.activity.uncertain_gpus }} / {{ detail.activity.management_holds }}</el-descriptions-item></el-descriptions><h2>上报的物理 GPU</h2><el-table :data="detail.gpus" empty-text="尚未上报 GPU，请配置并启动 Windows Worker。"><el-table-column prop="name" label="名称"/><el-table-column prop="hardware_id" label="硬件 ID"/><el-table-column label="显存"><template #default="{ row }">{{ row.memory_bytes ? (row.memory_bytes / 1024 ** 3).toFixed(1) + ' GiB' : '未上报' }}</template></el-table-column></el-table><p v-if="detail.gpus_truncated" class="form-help">仅显示前 100 个 GPU。</p><h2 class="node-detail-heading">关联的 ComfyUI 渠道</h2><el-table :data="detail.channels" empty-text="暂无关联渠道。Worker 上报 GPU 后可添加接入地址。"><el-table-column prop="name" label="渠道"/><el-table-column label="状态"><template #default="{ row }">{{ row.draining ? '排空中' : row.enabled ? '启用' : '停用' }}</template></el-table-column></el-table><p v-if="detail.channels_truncated" class="form-help">仅显示前 100 个渠道，请在渠道页查看。</p><div class="node-credential-actions"><el-button @click="inspect(detail)">刷新详情</el-button><router-link v-if="session.allows('channels:read')" class="key-access-link" to="/channels">前往 Windows 渠道 →</router-link></div></template></div></el-drawer>
</template>

<style scoped>
.node-setup { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); border-top: 2px solid #087e8b; border-bottom: 1px solid #d8e3e9; margin: 20px 0 26px; background: #eaf2f4; }
.node-setup > div { padding: 18px 22px; border-right: 1px solid #d8e3e9; }
.node-setup > div:last-child { border-right: 0; }
.node-step { font: 12px Consolas, monospace; color: #087e8b; margin-right: 12px; }
.node-setup strong { font-size: 13px; }
.node-setup p { color: #617482; font-size: 12px; line-height: 1.7; margin-top: 8px; }
.node-setup a { color: #087e8b; }
.node-filters { margin: 20px 0 4px; }
.node-state { width: 150px; }
.node-warning { display: block; color: #a84d25; font-size: 11px; margin-top: 6px; }
.node-credential-id { overflow-wrap: anywhere; margin: 20px 0 12px; font-size: 12px; }
.node-credential-actions { display: flex; gap: 12px; flex-wrap: wrap; margin-top: 18px; }
.node-details { margin: 22px 0; }
.node-detail-heading { margin-top: 26px; }
@media(max-width: 700px) { .node-setup { grid-template-columns: 1fr; } .node-setup > div { border-right: 0; border-bottom: 1px solid #d8e3e9; padding: 14px 16px; } .node-setup > div:last-child { border-bottom: 0; } .node-setup p { margin-left: 29px; } .page-heading { gap: 14px; flex-wrap: wrap; } .node-filters :deep(.el-form-item) { margin-right: 12px; } }
</style>
