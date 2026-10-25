<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Plus, Refresh } from '@element-plus/icons-vue'
import { api, readableError, revisionHeaders, time, unwrap, type Channel, type Node, type ChannelQueue, type Inventory } from '../api'
import type { paths, components } from '../generated/api'
import { useSession } from '../session'
import ScopeSelect from '../components/ScopeSelect.vue'
import ModelFiles from '../components/ModelFiles.vue'
import ChannelDeletionDialog from '../components/ChannelDeletionDialog.vue'
import ChannelReapplyDialog from '../components/ChannelReapplyDialog.vue'

type Filter = Omit<NonNullable<paths['/admin/api/v1/channels']['get']['parameters']['query']>, 'page'>
type Status = NonNullable<Filter['status']>
type Detail = components['schemas']['ChannelDetailView']
const route = useRoute(), router = useRouter(), session = useSession()
const rows = ref<Channel[]>([]), nodes = ref<Node[]>([]), error = ref(''), nodeError = ref(''), loading = ref(false), saving = ref(false), drawer = ref(false), editing = ref<Channel | null>(null), page = ref(1), hasMore = ref(false), readAt = ref<number | null>(null), visible = ref(!document.hidden)
const emptyDraft = () => ({ q: '', channel_id: '', project_id: '', node_id: '', status: '' as Status | '' })
const draft = ref(emptyDraft()), applied = ref<Filter>({})
const form = ref({ name: '', project_id: '', node_id: '', gpu_id: '', base_url: '', authorization: '', clear_authorization: false })
const gpuOptions = computed(() => nodes.value.find(n => n.id === form.value.node_id)?.gpus ?? [])
const inspecting = ref<Channel | null>(null), info = ref<Detail | null>(null), queue = ref<ChannelQueue | null>(null), inventory = ref<Inventory | null>(null), detailOpen = ref(false), detailBusy = ref(false), detailError = ref(''), queueError = ref(''), inventoryError = ref('')
const approvalDialog = ref(false), approvalForm = ref({ release_id: '', profile_id: '', evidence_id: '' }), profileIds = ref<string[]>([]), approvalError = ref(''), approvalBusy = ref(false)
const deletionTarget = ref<Channel | null>(null), deletionOpen = ref(false)
const reapplyOpen = ref(false)
type Read = 'list' | 'nodes' | 'detail' | 'approval' | 'session'
const reads = new Map<Read, { request: AbortController; timer: ReturnType<typeof setTimeout> }>()
let disposed = false, approvalToken = '', approvalRevision = 0, visibilityEpoch = 0
function stop(kind: Read) { const old = reads.get(kind); if (old) { clearTimeout(old.timer); reads.delete(kind); old.request.abort(); if (kind === 'approval') approvalBusy.value = false } }
function start(kind: Read) { stop(kind); const request = new AbortController(); reads.set(kind, { request, timer: setTimeout(() => request.abort(new DOMException('渠道读取超时，请稍后重试', 'TimeoutError')), 10000) }); return request }
function owns(kind: Read, request: AbortController) { return reads.get(kind)?.request === request && !disposed && !document.hidden }
function current(kind: Read, request: AbortController) { return owns(kind, request) && !request.signal.aborted }
function finish(kind: Read, request: AbortController) { if (reads.get(kind)?.request === request) { clearTimeout(reads.get(kind)!.timer); reads.delete(kind) } }
function failure(request: AbortController, e: unknown) { return request.signal.reason instanceof DOMException && request.signal.reason.name === 'TimeoutError' ? '渠道读取超时，请稍后重试' : readableError(e) }
function stopReads() { for (const kind of [...reads.keys()]) stop(kind); loading.value = false; detailBusy.value = false }
async function refresh() {
  if (disposed || document.hidden) return
  const request = start('list'); loading.value = true
  try {
    const data = unwrap(await api.GET('/admin/api/v1/channels', { signal: request.signal, params: { query: { ...applied.value, page: page.value } } }))
    if (current('list', request)) { rows.value = data.data; hasMore.value = data.has_more; readAt.value = Math.floor(Date.now() / 1000); error.value = '' }
  } catch (e) { if (owns('list', request)) error.value = failure(request, e) }
  finally { if (owns('list', request)) loading.value = false; finish('list', request) }
}
async function loadNodes() {
  if (disposed || document.hidden) return
  const request = start('nodes')
  try { const data = unwrap(await api.GET('/admin/api/v1/nodes', { signal: request.signal })); if (current('nodes', request)) { nodes.value = data.data; nodeError.value = '' } }
  catch (e) { if (owns('nodes', request)) nodeError.value = failure(request, e) }
  finally { finish('nodes', request) }
}
function applyFilters() { applied.value = { q: draft.value.q.trim() || undefined, channel_id: draft.value.channel_id.trim() || undefined, project_id: draft.value.project_id || undefined, node_id: draft.value.node_id || undefined, status: draft.value.status || undefined }; page.value = 1; rows.value = []; hasMore.value = false; readAt.value = null; error.value = ''; void refresh() }
function resetFilters() { draft.value = emptyDraft(); applyFilters() }
async function goPage(delta: number) { const next = page.value + delta; if (loading.value || next < 1 || next > 10000) return; page.value = next; rows.value = []; readAt.value = null; hasMore.value = false; await refresh() }
async function loadDetail(identifier: string) {
  stop('detail'); stop('approval'); approvalDialog.value = false
  info.value = null; inspecting.value = null; queue.value = null; inventory.value = null; detailError.value = ''; queueError.value = ''; inventoryError.value = ''; detailBusy.value = false; detailOpen.value = true
  if (!/^ch_[a-f0-9]{32}$/.test(identifier)) { detailError.value = '渠道链接无效'; return }
  if (disposed || document.hidden) return
  const request = start('detail'); detailBusy.value = true
  try {
    const data = unwrap(await api.GET('/admin/api/v1/channels/{identifier}', { signal: request.signal, params: { path: { identifier } } }))
    if (!current('detail', request)) return
    info.value = data; inspecting.value = data
    if (data.deleted_at) return
    await Promise.all([
      session.allows('queues:read') ? api.GET('/admin/api/v1/channels/{identifier}/queue', { signal: request.signal, params: { path: { identifier } } }).then(result => { const value = unwrap(result); if (current('detail', request)) queue.value = value }).catch(e => { if (owns('detail', request)) queueError.value = failure(request, e) }) : Promise.resolve(),
      api.GET('/admin/api/v1/channels/{identifier}/inventory', { signal: request.signal, params: { path: { identifier } } }).then(result => { const value = unwrap(result); if (current('detail', request)) inventory.value = value }).catch(e => { if (owns('detail', request)) inventoryError.value = failure(request, e) }),
    ])
  } catch (e) { if (owns('detail', request)) detailError.value = failure(request, e) }
  finally { if (owns('detail', request)) detailBusy.value = false; finish('detail', request) }
}
async function inspect(row: Channel) { await loadDetail(row.id) }
function openDetails(row: Channel) { if (route.query.channel === row.id) void loadDetail(row.id); else void router.push({ path: '/channels', query: { channel: row.id } }) }
function readRoute() { const value = route.query.channel; if (value === undefined) { detailOpen.value = false; return }; void loadDetail(typeof value === 'string' ? value : '') }
async function openApproval(releaseId: string) {
  if (!queue.value || !inspecting.value || inspecting.value.deleted_at || document.hidden) return
  const request = start('approval'); approvalBusy.value = true; detailError.value = ''
  try {
    const release = unwrap(await api.GET('/admin/api/v1/workflow-releases/{identifier}', { signal: request.signal, params: { path: { identifier: releaseId } } }))
    if (!current('approval', request)) return
    const hardware = nodes.value.find(n => n.id === inspecting.value?.node_id)?.gpus.find(g => g.id === inspecting.value?.gpu_id)?.hardware_id
    const profiles = release.manifest.runtime_profiles
    profileIds.value = Array.isArray(profiles) ? profiles.filter((p): p is { id: string; gpu_hardware_id: string } => !!p && typeof p === 'object' && typeof p.id === 'string' && p.gpu_hardware_id === hardware).slice(0, 1).map(p => p.id) : []
    if (!profileIds.value.length) throw new Error('冻结版本没有此 GPU 的运行画像')
    approvalRevision = queue.value!.revision; approvalToken = crypto.randomUUID(); approvalError.value = ''
    approvalForm.value = { release_id: releaseId, profile_id: profileIds.value.length === 1 ? profileIds.value[0]! : '', evidence_id: '' }; approvalDialog.value = true
  } catch (e) { if (owns('approval', request)) detailError.value = failure(request, e) }
  finally { if (reads.get('approval')?.request === request) approvalBusy.value = false; finish('approval', request) }
}
async function approve() {
  if (!inspecting.value || inspecting.value.deleted_at) return
  approvalBusy.value = true; approvalError.value = ''
  try {
    if (!approvalForm.value.profile_id || !approvalForm.value.evidence_id.trim()) throw new Error('请选择运行画像并填写验证任务的成功产物 ID')
    unwrap(await api.POST('/admin/api/v1/channels/{identifier}/workflow-bindings/approvals', { params: { path: { identifier: inspecting.value.id } }, headers: { ...revisionHeaders(approvalRevision), 'Idempotency-Key': approvalToken }, body: { ...approvalForm.value, evidence_id: approvalForm.value.evidence_id.trim(), expected_revision: approvalRevision } }))
    approvalDialog.value = false; ElMessage.success('运行批准已记录，可到模型页发布能力'); await inspect(inspecting.value); await refresh()
  } catch (e) { approvalError.value = readableError(e) } finally { approvalBusy.value = false }
}
async function scan() {
  if (!inspecting.value || inspecting.value.deleted_at) return
  try { unwrap(await api.POST('/admin/api/v1/channels/{identifier}/inventory-scans', { params: { path: { identifier: inspecting.value.id } } })); ElMessage.success('清单扫描已提交，完成后刷新详情') } catch (e) { detailError.value = readableError(e) }
}
function open(row?: Channel) {
  editing.value = row ?? null
  form.value = { name: row?.name ?? '', project_id: row?.project_id ?? session.info?.scopes[0]?.project_id ?? '', node_id: row?.node_id ?? '', gpu_id: row?.gpu_id ?? '', base_url: row?.base_url ?? '', authorization: '', clear_authorization: false }
  error.value = ''; drawer.value = true
}
async function save() {
  saving.value = true; error.value = ''
  try {
    if (!form.value.name.trim() || !form.value.base_url) throw new Error('请填写渠道名称和 ComfyUI 地址')
    if (editing.value) {
      const body = { expected_revision: editing.value.revision, name: form.value.name.trim(), ...(form.value.base_url !== editing.value.base_url ? { base_url: form.value.base_url } : {}), ...(form.value.clear_authorization ? { authorization: null } : form.value.authorization ? { authorization: form.value.authorization } : {}) }
      unwrap(await api.PATCH('/admin/api/v1/channels/{identifier}', { params: { path: { identifier: editing.value.id } }, headers: revisionHeaders(editing.value.revision), body }))
    } else {
      const scope = session.info?.scopes.find(s => s.project_id === form.value.project_id)
      if (!scope || !form.value.node_id || !form.value.gpu_id) throw new Error('请选择项目、Windows 节点和物理 GPU')
      unwrap(await api.POST('/admin/api/v1/channels', { body: { tenant_id: scope.tenant_id, project_id: scope.project_id, name: form.value.name.trim(), node_id: form.value.node_id, gpu_id: form.value.gpu_id, base_url: form.value.base_url, authorization: form.value.authorization || null } }))
    }
    drawer.value = false; form.value.authorization = ''; ElMessage.success('配置已保存，环境变更将等待 Worker 应用与验证'); await refresh()
  } catch (e) { error.value = readableError(e) } finally { saving.value = false }
}
async function action(row: Channel, kind: 'enable' | 'disable' | 'drain' | 'resume') {
  try {
    unwrap(await api.POST('/admin/api/v1/channels/{identifier}/actions', { params: { path: { identifier: row.id } }, headers: revisionHeaders(row.revision), body: { action: kind, expected_revision: row.revision } }))
    await refresh()
  } catch (e) { ElMessage.error(readableError(e)) }
}
async function probe(row: Channel) {
  try { unwrap(await api.POST('/admin/api/v1/channels/{identifier}/probe', { params: { path: { identifier: row.id } } })); ElMessage.success('探测任务已提交，请在操作中心查看结果') } catch (e) { ElMessage.error(readableError(e)) }
}
function remove(row: Channel) { deletionTarget.value = row; deletionOpen.value = true }
async function visibility() {
  const epoch = ++visibilityEpoch; visible.value = !document.hidden
  if (document.hidden) { stopReads(); approvalDialog.value = false; form.value.authorization = ''; return }
  const request = start('session')
  try {
    await session.refresh(request.signal)
    if (!session.info || !current('session', request) || epoch !== visibilityEpoch) return
    void refresh(); void loadNodes(); readRoute()
  } catch { if (owns('session', request)) session.clear() }
  finally { finish('session', request) }
}
watch(() => route.query.channel, () => { if (!document.hidden) readRoute() })
watch(detailOpen, open => { if (!open) { stop('detail'); stop('approval'); detailBusy.value = false; approvalBusy.value = false; approvalDialog.value = false; info.value = null; inspecting.value = null; queue.value = null; inventory.value = null; if (route.query.channel !== undefined) { const query = { ...route.query }; delete query.channel; void router.replace({ query }) } } })
onMounted(() => { void visibility(); document.addEventListener('visibilitychange', visibility) })
onUnmounted(() => { disposed = true; visibilityEpoch++; stopReads(); form.value.authorization = ''; document.removeEventListener('visibilitychange', visibility) })
</script>

<template>
  <ChannelDeletionDialog v-model="deletionOpen" :target="deletionTarget" :active="visible" @deleted="refresh"/>
  <ChannelReapplyDialog v-model="reapplyOpen" :target="inspecting" :active="visible && detailOpen" @applied="refresh"/>
  <div class="page-heading"><div><p class="eyebrow li-lab">INFRASTRUCTURE / CHANNELS</p><h1>Windows 渠道</h1><p class="muted">管理 ComfyUI 接入地址、凭据与运行环境版本。</p></div><div class="toolbar"><el-button :icon="Refresh" :loading="loading" @click="refresh(); loadNodes()">刷新</el-button><el-button v-if="session.allows('channels:write')" type="primary" :icon="Plus" @click="open()">添加渠道</el-button></div></div>
  <el-alert v-if="error && !drawer" :title="error + (readAt ? ' · 当前展示最后一次成功读取' : '')" type="error" :closable="false" show-icon/>
  <el-alert v-if="nodeError && !drawer" :title="'节点选项读取失败 · ' + nodeError" type="warning" :closable="false" show-icon/>
  <section class="panel li-card channel-filter-panel"><el-form class="channel-filters" label-position="top" @submit.prevent="applyFilters">
    <el-form-item label="名称或地址"><el-input v-model="draft.q" aria-label="名称或地址" maxlength="128" clearable placeholder="按名称或 ComfyUI 地址查找"/></el-form-item>
    <el-form-item label="所属项目"><ScopeSelect v-model="draft.project_id" :active="visible" allow-inactive clearable label="筛选项目"/></el-form-item>
    <el-form-item label="Windows 节点"><el-select v-model="draft.node_id" aria-label="筛选节点" filterable clearable placeholder="全部节点"><el-option v-for="n in nodes" :key="n.id" :value="n.id" :label="n.name"/></el-select></el-form-item>
    <el-form-item label="登记状态"><el-select v-model="draft.status" aria-label="登记状态" clearable placeholder="全部状态（含删除）"><el-option label="启用（未删除）" value="enabled"/><el-option label="停用（未删除）" value="disabled"/><el-option label="排空中（未删除）" value="draining"/><el-option label="已删除登记" value="deleted"/></el-select></el-form-item>
    <el-form-item label="渠道 ID（精确）"><el-input v-model="draft.channel_id" aria-label="渠道 ID（精确）" maxlength="35" clearable placeholder="ch_…"/></el-form-item>
    <div class="channel-filter-actions"><el-button type="primary" native-type="submit">应用筛选</el-button><el-button @click="resetFilters">重置筛选</el-button></div>
  </el-form><p class="form-help">所有条件取交集；名称和地址按字面匹配，编辑后点击应用。全部状态包含已删除历史。</p></section>
  <div class="snapshot-line"><span>上次读取 {{ time(readAt) }}</span><span>第 {{ page }} 页 · 每页 50 条</span></div>
  <section class="panel li-card"><el-table :data="rows" v-loading="loading" :empty-text="loading ? '正在读取渠道…' : error && !readAt ? '渠道读取失败' : '当前条件没有渠道登记'" table-layout="auto">
    <el-table-column label="渠道 / 地址" min-width="250"><template #default="{ row }"><router-link :to="{ path: '/channels', query: { channel: row.id } }"><strong>{{ row.name }}</strong></router-link><span class="id-line">{{ row.base_url }}</span><span class="id-line mono">{{ row.id }}</span></template></el-table-column>
    <el-table-column label="详情" width="80"><template #default="{ row }"><el-button text size="small" @click="openDetails(row)">查看</el-button></template></el-table-column>
    <el-table-column label="配置版本" min-width="165"><template #default="{ row }"><span class="mono">{{ row.desired_revision }} / {{ row.applied_revision ?? '—' }} / {{ row.verified_revision ?? '—' }}</span><span class="id-line">期望 / 已应用 / 已验证</span></template></el-table-column>
    <el-table-column label="状态" min-width="90"><template #default="{ row }"><el-tag :type="row.deleted_at ? 'info' : row.draining ? 'warning' : row.enabled ? 'success' : 'info'" size="small">{{ row.deleted_at ? '已删除' : row.draining ? '排空中' : row.enabled ? '已启用' : '已停用' }}</el-tag></template></el-table-column>
    <el-table-column label="最近观测" min-width="175"><template #default="{ row }">{{ time(row.observed_at) }}</template></el-table-column>
    <el-table-column v-if="session.allows('channels:write')" label="操作" min-width="275"><template #default="{ row }"><div v-if="!row.deleted_at" class="actions"><el-button text size="small" @click="open(row)">编辑</el-button><el-button text size="small" @click="probe(row)">探测</el-button><el-button v-if="row.enabled && !row.draining" text size="small" @click="action(row, 'drain')">排空</el-button><el-button v-if="row.enabled && row.draining" text size="small" @click="action(row, 'resume')">恢复接收</el-button><el-button v-if="row.enabled" text size="small" @click="action(row, 'disable')">停用</el-button><el-button v-if="!row.enabled" text size="small" :disabled="row.verified_revision !== row.desired_revision || row.applied_revision !== row.desired_revision" title="配置应用并验证后可启用" @click="action(row, 'enable')">启用</el-button><el-button text type="danger" size="small" @click="remove(row)">删除</el-button></div></template></el-table-column>
  </el-table><div class="pagination"><el-button size="small" :disabled="page === 1 || loading" @click="goPage(-1)">上一页</el-button><span>第 {{ page }} 页</span><el-button size="small" :disabled="!hasMore || loading || page === 10000" @click="goPage(1)">下一页</el-button></div></section>
  <el-drawer v-model="drawer" :title="editing ? '编辑渠道' : '添加 Windows 渠道'" size="min(520px, 100vw)" @closed="form.authorization = ''"><el-alert v-if="error" :title="error" type="error" :closable="false" style="margin-bottom: 18px"/><el-form label-position="top" @submit.prevent="save"><el-form-item label="渠道名称" required><el-input v-model="form.name" maxlength="128"/></el-form-item><el-alert v-if="nodeError" :title="nodeError" type="warning" :closable="false"/><template v-if="!editing"><el-form-item label="所属项目" required><ScopeSelect v-model="form.project_id" :active="drawer && visible" class="full"/></el-form-item><el-form-item label="Windows Worker" required><el-select v-model="form.node_id" class="full" placeholder="选择已登记节点" @change="form.gpu_id = ''"><el-option v-for="n in nodes" :key="n.id" :value="n.id" :label="n.name"/></el-select><p v-if="!nodes.length" class="form-help">尚未登记节点。请先在 <router-link to="/worker-nodes">Windows 节点</router-link> 登记并保存凭证，再启动 Worker 上报 GPU。</p></el-form-item><el-form-item label="物理 GPU" required><el-select v-model="form.gpu_id" class="full" placeholder="选择物理 GPU"><el-option v-for="gpu in gpuOptions" :key="gpu.id" :value="gpu.id" :label="gpu.name + ' · ' + gpu.hardware_id"/></el-select></el-form-item></template><el-form-item label="ComfyUI 地址" required><el-input v-model="form.base_url" placeholder="http://192.168.1.10:8188"/><p class="form-help">由 Worker 访问此地址。修改运行环境后，渠道将等待重新验证。</p></el-form-item><el-form-item :label="editing ? '更新 Authorization（留空保留）' : 'Authorization（可选）'"><el-input v-model="form.authorization" type="password" autocomplete="off" show-password/><el-checkbox v-if="editing" v-model="form.clear_authorization">清除已保存凭据</el-checkbox></el-form-item><div class="drawer-footer"><el-button @click="drawer = false">取消</el-button><el-button type="primary" native-type="submit" :loading="saving">保存渠道</el-button></div></el-form></el-drawer>
  <el-drawer v-model="detailOpen" :title="inspecting ? inspecting.name + ' · 渠道详情' : '渠道详情'" size="min(700px, 100vw)">
    <div class="toolbar"><el-button :loading="detailBusy" :disabled="!inspecting" @click="inspecting && inspect(inspecting)">刷新详情</el-button><el-button v-if="inspecting && !inspecting.deleted_at && session.allows('channels:write')" @click="scan">扫描渠道资源</el-button></div>
    <el-button v-if="inspecting && !inspecting.deleted_at && session.allows('channels:write')" :disabled="detailBusy" @click="reapplyOpen = true">重新应用当前配置</el-button>
    <el-alert v-if="detailError" :title="detailError" type="error" :closable="false" show-icon/>
    <p v-if="detailBusy && !info" class="muted">正在读取渠道配置…</p>
    <el-alert v-if="info?.deleted_at" title="登记已删除，仅显示保留的配置与历史。" type="info" :closable="false"/>
    <el-descriptions v-if="info" :column="1" border class="detail-block">
      <el-descriptions-item label="渠道 ID"><span class="mono">{{ info.id }}</span></el-descriptions-item><el-descriptions-item label="ComfyUI 地址">{{ info.base_url }}</el-descriptions-item>
      <el-descriptions-item label="项目"><span class="mono">{{ info.project_id }}</span></el-descriptions-item><el-descriptions-item label="节点 / GPU"><span class="mono">{{ info.node_id }} / {{ info.gpu_id }}</span></el-descriptions-item>
      <el-descriptions-item label="配置版本">{{ info.desired_revision }} / {{ info.applied_revision ?? '未知' }} / {{ info.verified_revision ?? '未知' }}（期望 / 应用 / 验证）</el-descriptions-item><el-descriptions-item label="接入凭证">{{ info.has_authorization ? '已配置' : '未配置' }}</el-descriptions-item>
    </el-descriptions>
    <template v-if="info && !info.deleted_at">
      <h3>现场队列</h3><el-alert v-if="queueError" :title="queueError" type="warning" :closable="false"/>
      <p v-if="!session.allows('queues:read')" class="muted">当前账号无队列读取权限。</p><p v-else-if="!queue && !queueError" class="muted">等待读取现场队列。</p>
      <el-descriptions v-if="queue" :column="1" border class="detail-block"><el-descriptions-item label="共享候选">{{ queue.shared_candidates }}</el-descriptions-item><el-descriptions-item label="已分配未结束">{{ queue.assigned_unfinished }}</el-descriptions-item><el-descriptions-item label="ComfyUI 运行 / 排队">{{ queue.comfy_queue_running ?? '未知' }} / {{ queue.comfy_queue_pending ?? '未知' }}</el-descriptions-item><el-descriptions-item label="队列观测时间">{{ time(queue.queue_observed_at) }}</el-descriptions-item><el-descriptions-item label="物理 GPU">{{ queue.gpu_occupancy_uncertain ? '待核对' : queue.gpu_occupied ? '生成占用' : queue.gpu_management_hold ? '管理操作占用' : '未分配' }}</el-descriptions-item></el-descriptions>
      <h3>Workflow 同步</h3><el-alert v-if="inventoryError" :title="inventoryError" type="warning" :closable="false"/>
      <el-table v-if="inventory" :data="inventory.bindings" empty-text="尚未绑定 Workflow" table-layout="auto"><el-table-column prop="release_id" label="冻结版本" min-width="200"/><el-table-column label="同步 / 批准" min-width="170"><template #default="{ row }">{{ row.verified_digest === row.desired_digest && row.applied_digest === row.desired_digest ? '环境已验证' : '等待同步' }} / {{ row.runtime_approved ? '运行已批准' : '待批准' }}</template></el-table-column><el-table-column v-if="session.allows('channels:write')" label="运行画像" min-width="120"><template #default="{ row }"><el-button text :loading="approvalBusy" :disabled="!queue || row.verified_digest !== row.desired_digest || row.applied_digest !== row.desired_digest" @click="openApproval(row.release_id)">{{ row.runtime_approved ? '重新批准' : '批准运行' }}</el-button></template></el-table-column></el-table>
      <ModelFiles v-if="inspecting" :key="inspecting.id" :channel="inspecting" :active="detailOpen && visible"/>
      <template v-if="inventory"><h3>节点清单</h3><p class="muted">{{ inventory.stale ? '清单尚未获取或已过期' : '最近扫描' }} · {{ time(inventory.observed_at) }}</p><pre class="code-view">{{ JSON.stringify(inventory.inventory, null, 2) }}</pre></template>
    </template>
    <template v-if="info"><h3>配置历史（最近 50 条）</h3><el-table :data="info.revisions" empty-text="未记录历史配置" table-layout="auto"><el-table-column prop="revision" label="版本" width="80"/><el-table-column prop="base_url" label="地址" min-width="230"/><el-table-column label="记录时间" min-width="180"><template #default="{ row }">{{ time(row.created_at) }}</template></el-table-column><el-table-column label="凭证" width="100"><template #default="{ row }">{{ row.has_authorization ? '已配置' : '未配置' }}</template></el-table-column></el-table></template>
  </el-drawer>
  <el-dialog v-model="approvalDialog" title="批准 GPU 运行画像" width="min(600px, 94vw)" :close-on-click-modal="!approvalBusy"><el-alert v-if="approvalError" :title="approvalError" type="error" :closable="false"/><el-form label-position="top" @submit.prevent="approve"><p class="form-help">{{ inspecting?.name }} · {{ approvalForm.release_id }}</p><el-form-item label="运行画像" required><el-select v-model="approvalForm.profile_id" class="full"><el-option v-for="id in profileIds" :key="id" :value="id" :label="id"/></el-select></el-form-item><el-form-item label="成功验证产物 ID" required><el-input v-model="approvalForm.evidence_id" placeholder="content_…"/><p class="form-help">从任务详情复制同渠道、同发布版本的成功验证产物 ID。服务端会再次检查配置版本和证据有效性。</p></el-form-item><div class="drawer-footer"><el-button :disabled="approvalBusy" @click="approvalDialog = false">取消</el-button><el-button native-type="submit" type="primary" :loading="approvalBusy">确认运行批准</el-button></div></el-form></el-dialog>
</template>

<style scoped>
.channel-filter-panel { padding: 20px; }
.channel-filters { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 0 20px; }
.channel-filter-actions { display: flex; align-items: center; gap: 8px; padding-bottom: 18px; }
@media (max-width: 800px) { .channel-filters { grid-template-columns: 1fr; } }
</style>
