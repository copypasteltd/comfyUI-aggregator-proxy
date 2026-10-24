<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { useRoute, useRouter } from 'vue-router'
import { api, readableError, time, unwrap } from '../api'
import type { components } from '../generated/api'
import { useSession } from '../session'
import WorkflowRetryDialog from '../components/WorkflowRetryDialog.vue'
const route = useRoute(), router = useRouter(), session = useSession()
const operations = ref<components['schemas']['OperationView'][]>([]), error = ref(''), loading = ref(false), page = ref(1), hasMore = ref(false), readAt = ref<number | null>(null)
const detailOpen = ref(false), detail = ref<components['schemas']['OperationDetail'] | null>(null), detailError = ref(''), detailBusy = ref(false), selected = ref('')
const retryOpen = ref(false)
type Kind = 'list' | 'detail' | 'session'
const reads = new Map<Kind, { request: AbortController; timer: ReturnType<typeof setTimeout> }>()
let disposed = false, epoch = 0
function stop(kind: Kind) { const old = reads.get(kind); reads.delete(kind); if (old) { clearTimeout(old.timer); old.request.abort() } }
function stopAll() { for (const kind of [...reads.keys()]) stop(kind); loading.value = false; detailBusy.value = false }
function start(kind: Kind) { stop(kind); const request = new AbortController(); reads.set(kind, { request, timer: setTimeout(() => request.abort(new DOMException('操作读取超时，请重试', 'TimeoutError')), 10000) }); return request }
function owns(kind: Kind, request: AbortController) { return reads.get(kind)?.request === request && !disposed && !document.hidden && !!session.info && !session.ending }
function current(kind: Kind, request: AbortController) { return owns(kind, request) && !request.signal.aborted }
function finish(kind: Kind, request: AbortController) { if (reads.get(kind)?.request === request) { clearTimeout(reads.get(kind)!.timer); reads.delete(kind) } }
function failure(request: AbortController, e: unknown) { return request.signal.reason instanceof DOMException && request.signal.reason.name === 'TimeoutError' ? '操作读取超时，请重试' : readableError(e) }
async function refresh() {
  if (disposed || document.hidden || !session.info || session.ending) return
  const request = start('list'); loading.value = true
  try { const data = unwrap(await api.GET('/admin/api/v1/operations', { signal: request.signal, params: { query: { page: page.value } } })); if (current('list', request)) { operations.value = data.data; hasMore.value = data.has_more; readAt.value = Math.floor(Date.now() / 1000); error.value = '' } }
  catch (e) { if (owns('list', request)) error.value = failure(request, e) }
  finally { if (owns('list', request)) loading.value = false; finish('list', request) }
}
async function inspect(id: string) {
  stop('detail'); detailBusy.value = false; detailOpen.value = true; detailError.value = ''
  if (selected.value !== id) { detail.value = null; retryOpen.value = false }
  selected.value = id
  if (!/^op_[0-9a-f]{32}$/.test(id)) { detailError.value = '管理操作链接无效'; return }
  if (disposed || document.hidden || !session.info || session.ending) return
  const request = start('detail'); detailBusy.value = true
  try { const data = unwrap(await api.GET('/admin/api/v1/operations/{identifier}', { signal: request.signal, params: { path: { identifier: id } } })); if (current('detail', request) && selected.value === id) detail.value = data }
  catch (e) { if (owns('detail', request)) detailError.value = failure(request, e) }
  finally { if (owns('detail', request)) detailBusy.value = false; finish('detail', request) }
}
function open(id: string) { void router.replace({ query: { ...route.query, operation_id: id } }) }
function readRoute() { const value = route.query.operation_id; if (value === undefined) { detailOpen.value = false; return }; void inspect(typeof value === 'string' ? value : '') }
async function goPage(delta: number) { const next = page.value + delta; if (loading.value || next < 1 || next > 10000) return; page.value = next; operations.value = []; readAt.value = null; hasMore.value = false; await refresh() }
async function visibility() {
  const revision = ++epoch
  if (document.hidden) { stopAll(); return }
  const request = start('session')
  try { await session.refresh(request.signal); if (!current('session', request) || revision !== epoch) return; void refresh(); readRoute() }
  catch { if (owns('session', request)) session.clear() }
  finally { finish('session', request) }
}
watch(() => route.query.operation_id, () => { if (!document.hidden) readRoute() })
watch(detailOpen, value => { if (!value) { retryOpen.value = false; stop('detail'); detailBusy.value = false; detail.value = null; selected.value = ''; if (route.query.operation_id !== undefined) { const query = { ...route.query }; delete query.operation_id; void router.replace({ query }) } } })
watch(() => [session.info?.csrf_token, session.ending] as const, ([token, ending], [old]) => { if (!token || ending || token !== old) { epoch++; stopAll(); operations.value = []; detail.value = null; readAt.value = null } })
onMounted(() => { void visibility(); document.addEventListener('visibilitychange', visibility) })
onUnmounted(() => { disposed = true; epoch++; stopAll(); document.removeEventListener('visibilitychange', visibility) })
const commandNames: Record<string, string> = { cancel_jobs: '批量取消任务', apply_channel_config: '应用渠道配置', probe_channel: '探测渠道', scan_inventory: '扫描模型与节点', apply_workflow_release: '同步 Workflow', pull_workflow: '导入 Workflow', inspect_model_file: '检查模型文件', collect_diagnostics: '收集诊断' }
const states: Record<string, string> = { pending: '待执行', running: '执行中', succeeded: '已完成', failed: '失败', partial_failed: '部分失败' }
const reasons: Record<string, string> = { expired: '租约已过期', previous_boot: 'Worker 已重新启动', unknown_lease: '缺少有效租约' }
function stateType(state: string) { return state === 'succeeded' ? 'success' : state === 'failed' ? 'danger' : state === 'partial_failed' ? 'warning' : 'info' }
</script>
<template>
  <div class="page-heading"><div><p class="eyebrow">MANAGEMENT / OPERATIONS</p><h1>操作中心</h1><p class="muted">核对各渠道的管理指令与执行结果。</p></div><el-button :icon="Refresh" :loading="loading" @click="refresh">刷新</el-button></div>
  <el-alert v-if="error" :title="error" type="error" :closable="false"/>
  <p v-if="readAt" class="muted">读取于 {{ time(readAt) }}{{ error || loading ? ' · 显示上次结果' : '' }}</p>
  <section class="panel">
    <el-table :data="operations" v-loading="loading" empty-text="暂无管理操作" table-layout="auto"><el-table-column label="操作" min-width="180"><template #default="{ row }">{{ commandNames[row.command] ?? row.command }}<span class="id-line">{{ row.id }}</span></template></el-table-column><el-table-column label="状态" min-width="110"><template #default="{ row }"><el-tag :type="stateType(row.status)" size="small">{{ states[row.status] ?? row.status }}</el-tag></template></el-table-column><el-table-column label="提交时间" min-width="180"><template #default="{ row }">{{ time(row.created_at) }}</template></el-table-column><el-table-column label="执行目标" width="120" fixed="right"><template #default="{ row }"><router-link v-if="row.command === 'cancel_jobs' && session.allows('jobs:read')" :to="{ path: '/jobs', query: { cancellation: row.id } }">查看任务结果</router-link><el-button v-else text @click="open(row.id)">查看目标</el-button></template></el-table-column></el-table>
    <div class="pagination"><el-button size="small" :disabled="page === 1 || loading" @click="goPage(-1)">上一页</el-button><span>第 {{ page }} 页</span><el-button size="small" :disabled="!hasMore || loading || page >= 10000" @click="goPage(1)">下一页</el-button></div>
  </section>
  <el-drawer v-model="detailOpen" title="管理操作目标" size="min(880px, 100vw)">
    <el-alert v-if="detailError" :title="detailError" type="error" :closable="false"/>
    <div class="toolbar"><el-button :disabled="!selected || detailBusy" @click="inspect(selected)">刷新目标状态</el-button></div>
    <div v-loading="detailBusy"><template v-if="detail">
      <p class="mono operation-id">{{ detail.id }}</p><p class="muted">采样于 {{ time(detail.snapshot_at) }}{{ detailBusy || detailError ? ' · 显示上次结果' : '' }}</p>
      <p v-if="detail.retry_of"><router-link :to="{ path: '/operations', query: { operation_id: detail.retry_of } }">查看来源操作</router-link></p>
      <div v-if="detail.command === 'apply_workflow_release' && session.allows('workflows:publish')" class="toolbar"><el-button :disabled="detailBusy || !!detailError || !['failed', 'partial_failed'].includes(detail.status)" @click="retryOpen = true">重试失败渠道</el-button></div>
      <el-alert v-if="detail.has_more" title="只显示前 100 个目标" type="info" :closable="false"/>
      <article v-for="target in detail.targets" :key="target.id" class="operation-target">
        <div class="toolbar"><el-tag :type="stateType(target.status)">{{ states[target.status] ?? target.status }}</el-tag><el-tag v-if="target.needs_reconciliation" type="warning">待核对</el-tag></div>
        <p class="mono operation-id">{{ target.id }}</p>
        <p><router-link :to="{ path: '/channels', query: { channel: target.channel_id } }">查看渠道</router-link><span v-if="target.node_id"> · <router-link :to="{ path: '/worker-nodes', query: { node: target.node_id } }">查看 Windows 节点</router-link></span></p>
        <p class="mono operation-id">{{ target.channel_id }}</p>
        <p class="muted">租约截止 {{ time(target.lease_expires_at) }} · 完成时间 {{ time(target.completed_at) }}</p>
        <el-alert v-if="target.needs_reconciliation" :title="(reasons[target.reason ?? ''] ?? '执行结果不明') + '，等待 Worker 核对持久结果。没有完成证据时继续保留占用。'" type="warning" :closable="false"/>
        <el-alert v-else-if="target.recovery" :title="'旧指令已核对关闭（' + time(target.recovery.reconciled_at) + '）。请进入渠道明确重新应用配置，再探测、同步并验证。'" type="warning" :closable="false"/>
        <p v-if="target.receipt?.error_code" class="mono">{{ target.receipt.error_code }}</p><p v-else-if="target.receipt">{{ target.receipt.environment_verified === true ? '环境已验证' : target.receipt.success === true ? '已完成' : '等待核对' }}</p><p v-else class="muted">等待 Worker 回执</p>
      </article>
    </template></div>
  </el-drawer>
  <WorkflowRetryDialog v-model="retryOpen" :operation-id="selected" :active="detailOpen && !!session.info && !session.ending" @accepted="refresh(); inspect(selected)"/>
</template>
<style scoped>
.operation-target { padding: 18px 0; border-bottom: 1px solid var(--el-border-color-light); }
.operation-id { overflow-wrap: anywhere; }
@media (max-width: 600px) { .operation-id { font-size: 12px; } }
</style>
