<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import { api, readableError, revisionHeaders, time, unwrap } from '../api'
import type { components } from '../generated/api'
import { useSession } from '../session'
import SpecIsolationCreate from '../components/SpecIsolationCreate.vue'
import QueueFilters from '../components/QueueFilters.vue'
import JobTimings from '../components/JobTimings.vue'
import JobCancellationDialog from '../components/JobCancellationDialog.vue'
import { queueStates as states, useQueueRefresh, waitTime } from '../queue'

type Job = components['schemas']['AdminJobView']
const session = useSession(), route = useRoute(), router = useRouter()
const { value: data, error, loading, page, filters, refresh, apply, goPage } = useQueueRefresh(async (query, signal) => unwrap(await api.GET('/admin/api/v1/queues/jobs', { signal, params: { query } })))
const rows = computed(() => data.value?.data ?? []), hasMore = computed(() => data.value?.has_more ?? false), updatedAt = computed(() => data.value?.snapshot_at)
const busy = ref(false), detailOpen = ref(false), detailBusy = ref(false), detailError = ref('')
const detail = ref<components['schemas']['AdminJobDetail'] | null>(null), priority = ref(0)
const terminal = new Set(['succeeded', 'failed', 'cancelled', 'expired'])
const selectedJobs = ref<string[]>([]), batchOpen = ref(false), batchOperation = ref(''), batchJobs = ref<Job[]>([])
function selectable(row: Job) { return !terminal.has(row.status) && row.status !== 'cancel_requested' }
function selectJob(row: Job, selected: boolean) { selectedJobs.value = selected ? [...new Set([...selectedJobs.value, row.id])].slice(0, 50) : selectedJobs.value.filter(id => id !== row.id) }
function openBatch() { batchJobs.value = rows.value.filter(row => selectedJobs.value.includes(row.id)); batchOperation.value = ''; batchOpen.value = true; void router.replace({ query: { ...route.query, cancellation: undefined } }) }
function openExistingBatch(id: unknown) { if (typeof id === 'string' && /^op_[a-f0-9]{32}$/.test(id)) { batchOperation.value = id; batchOpen.value = true } }
function batchAccepted(id: string) { selectedJobs.value = []; void router.replace({ query: { ...route.query, cancellation: id } }); void refresh() }
watch([page, filters], () => { selectedJobs.value = [] })
watch(rows, value => { selectedJobs.value = selectedJobs.value.filter(id => value.some(row => row.id === id && selectable(row))) })
watch(() => route.query.cancellation, openExistingBatch)
watch(() => session.info, value => { if (!value) { selectedJobs.value = []; batchJobs.value = []; batchOpen.value = false } })
const tokens = new Map<string, string>()
let alive = true
const controller = new AbortController()
let detailController: AbortController | undefined
async function inspect(id: string) {
  detailController?.abort()
  const request = new AbortController(); detailController = request
  detailOpen.value = true; detailBusy.value = true; detailError.value = ''; detail.value = null
  try { const result = unwrap(await api.GET('/admin/api/v1/jobs/{identifier}', { signal: AbortSignal.any([controller.signal, request.signal]), params: { path: { identifier: id } } })); if (alive && !request.signal.aborted) { detail.value = result; priority.value = result.priority } }
  catch (e) { if (alive && !request.signal.aborted) detailError.value = readableError(e) } finally { if (detailController === request) detailBusy.value = false }
}
async function action(row: Job, kind: 'cancel' | 'retry') {
  if (busy.value) return
  try {
    await ElMessageBox.confirm(kind === 'retry' ? '重试将创建关联的新任务，重新检查当前授权、素材和配额，并计入新的受理数。网关任务需从网关重新提交。' : '取消已领取任务需要等待设备确认停止；尚未领取的任务可以立即取消。', kind === 'retry' ? '创建重试任务' : '取消任务', { type: 'warning', confirmButtonText: '确认', cancelButtonText: '返回' })
    busy.value = true
    const tokenKey = kind + ':' + row.id
    if (!tokens.has(tokenKey)) tokens.set(tokenKey, crypto.randomUUID())
    const options = { signal: controller.signal, params: { path: { identifier: row.id } }, headers: { 'Idempotency-Key': tokens.get(tokenKey)! } }
    const result = kind === 'retry' ? unwrap(await api.POST('/admin/api/v1/jobs/{identifier}/retry', options)) : unwrap(await api.POST('/admin/api/v1/jobs/{identifier}/cancel', options))
    tokens.delete(tokenKey); ElMessage.success(kind === 'retry' ? '重试任务已受理' : '取消请求已记录'); await refresh(); await inspect(result.id)
  } catch (e) { if (e !== 'cancel' && e !== 'close' && alive) ElMessage.error(readableError(e)) } finally { busy.value = false }
}
async function savePriority() {
  const row = detail.value
  if (!row) return
  busy.value = true; detailError.value = ''
  try { unwrap(await api.PATCH('/admin/api/v1/jobs/{identifier}/priority', { signal: controller.signal, params: { path: { identifier: row.id } }, headers: revisionHeaders(row.updated_revision), body: { expected_revision: row.updated_revision, priority: priority.value } })); await inspect(row.id); await refresh(); ElMessage.success('优先级已保存') }
  catch (e) { detailError.value = readableError(e) } finally { busy.value = false }
}
function visibility() { if (document.hidden) detailController?.abort() }
watch(detailOpen, open => { if (!open) detailController?.abort() })
watch(() => route.query.job, id => { if (typeof id === 'string') void inspect(id) })
onMounted(() => { document.addEventListener('visibilitychange', visibility); if (typeof route.query.job === 'string') void inspect(route.query.job); openExistingBatch(route.query.cancellation) })
onUnmounted(() => { alive = false; controller.abort(); detailController?.abort(); document.removeEventListener('visibilitychange', visibility); tokens.clear() })
</script>

<template>
  <div class="page-heading"><div><p class="eyebrow">EXECUTION / JOBS</p><h1>任务管理</h1><p class="muted">查看执行状态、尝试记录并处置排队任务。</p></div><el-button :icon="Refresh" :loading="loading" @click="refresh">刷新</el-button></div>
  <el-alert v-if="error" :title="error + (data ? ' · 当前展示最后一次成功快照' : '')" type="error" :closable="false" show-icon/>
  <QueueFilters @apply="apply"/>
  <section class="panel"><p class="form-help">每 5 秒按已应用条件刷新 · 快照时间 {{ time(updatedAt) }} · 等待时长截至首次分配，未分配任务截至快照时间</p>
    <div v-if="session.allows('jobs:control')" class="toolbar batch-actions"><span class="muted">已选 {{ selectedJobs.length }} 个 · 仅当前页</span><el-button :disabled="loading || batchOpen" @click="selectedJobs = rows.filter(selectable).map(row => row.id)">选择本页可取消任务</el-button><el-button :disabled="!selectedJobs.length || batchOpen" @click="selectedJobs = []">清空选择</el-button><el-button type="danger" plain :disabled="!selectedJobs.length || loading || busy || batchOpen" @click="openBatch">批量取消所选任务</el-button></div>
    <el-table :data="rows" v-loading="loading" empty-text="暂无符合条件的任务" table-layout="auto"><el-table-column v-if="session.allows('jobs:control')" label="选择" width="70" fixed="left"><template #default="{ row }"><el-checkbox :model-value="selectedJobs.includes(row.id)" :aria-label="'选择任务 ' + row.id" :disabled="!selectable(row) || loading || batchOpen" @change="selectJob(row, !!$event)"/></template></el-table-column><el-table-column label="任务 / 模型" min-width="280"><template #default="{ row }"><el-button text @click="inspect(row.id)">{{ row.model }}</el-button><span class="id-line">{{ row.id }}</span><span class="id-line">{{ row.release_id }}</span><span v-if="row.retry_of" class="id-line">重试来源 {{ row.retry_of }}</span></template></el-table-column><el-table-column label="状态" min-width="110"><template #default="{ row }"><el-tag :type="row.status === 'succeeded' ? 'success' : ['failed', 'reconciling'].includes(row.status) ? 'danger' : 'info'" size="small">{{ states[row.status] ?? row.status }}</el-tag><span v-if="row.retry?.count" class="id-line">内部重试 {{ row.retry.count }} / {{ row.retry.limit }}</span><span v-if="row.retry?.next_at" class="id-line">可再次分配 {{ time(row.retry.next_at) }}</span></template></el-table-column><el-table-column label="实际等待" min-width="120"><template #default="{ row }">{{ waitTime(row.queue_wait_seconds) }}</template></el-table-column><el-table-column prop="priority" label="优先级" width="85"/><el-table-column label="归属 Key" min-width="210"><template #default="{ row }"><span class="mono">{{ row.key_id }}</span></template></el-table-column><el-table-column label="受理时间" min-width="175"><template #default="{ row }">{{ time(row.created_at) }}</template></el-table-column><el-table-column label="操作" min-width="160" fixed="right"><template #default="{ row }"><el-button text @click="inspect(row.id)">详情</el-button><template v-if="session.allows('jobs:control')"><el-button v-if="!terminal.has(row.status) && row.status !== 'cancel_requested'" text type="danger" :disabled="busy" @click="action(row, 'cancel')">取消</el-button><el-button v-if="['failed', 'cancelled', 'expired'].includes(row.status) && !row.is_validation" text :disabled="busy" @click="action(row, 'retry')">重试</el-button></template></template></el-table-column></el-table>
    <div class="pagination"><el-button size="small" :disabled="page === 1 || loading" @click="goPage(-1)">上一页</el-button><span>第 {{ page }} 页</span><el-button size="small" :disabled="!hasMore || loading || page >= 10000" @click="goPage(1)">下一页</el-button></div>
  </section>
  <JobCancellationDialog v-model="batchOpen" :jobs="batchJobs" :operation-id="batchOperation" @accepted="batchAccepted"/>
  <el-drawer v-model="detailOpen" title="任务详情" size="min(800px, 100vw)"><el-alert v-if="detailError" :title="detailError" type="error" :closable="false"/><div v-loading="detailBusy"><template v-if="detail"><div class="toolbar"><el-button :disabled="detailBusy" @click="inspect(detail.id)">刷新详情</el-button><el-button v-if="session.allows('jobs:control') && !terminal.has(detail.status) && detail.status !== 'cancel_requested'" type="danger" plain :loading="busy" @click="action(detail, 'cancel')">请求取消</el-button><SpecIsolationCreate v-if="session.allows('channels:write') && detail.current_attempt_id" :job-id="detail.id" :revision="detail.updated_revision"/></div><el-descriptions :column="1" border class="detail-block"><el-descriptions-item label="任务 ID">{{ detail.id }}</el-descriptions-item><el-descriptions-item label="状态">{{ states[detail.status] ?? detail.status }}</el-descriptions-item><el-descriptions-item v-if="detail.retry" label="内部重试">{{ detail.retry.count }} / {{ detail.retry.limit }}</el-descriptions-item><el-descriptions-item v-if="detail.retry?.last_error" label="重试原因">{{ detail.retry.last_error === 'comfy_oom' ? '显存不足，重试需使用其他 GPU' : '提交前连接暂时失败' }}</el-descriptions-item><el-descriptions-item v-if="detail.retry?.next_at" label="可再次分配时间">{{ time(detail.retry.next_at) }}</el-descriptions-item><el-descriptions-item label="实际等待">{{ waitTime(detail.queue_wait_seconds) }}</el-descriptions-item><el-descriptions-item label="首次分配时间">{{ time(detail.first_assigned_at) }}</el-descriptions-item><el-descriptions-item label="公开模型">{{ detail.model }}</el-descriptions-item><el-descriptions-item label="Workflow 版本">{{ detail.release_id }}</el-descriptions-item><el-descriptions-item label="租户 / 项目">{{ detail.tenant_id }} / {{ detail.project_id }}</el-descriptions-item><el-descriptions-item label="归属 Key">{{ detail.key_id }}</el-descriptions-item><el-descriptions-item v-if="detail.retry_of" label="重试来源"><el-button text @click="inspect(detail.retry_of)">{{ detail.retry_of }}</el-button></el-descriptions-item><el-descriptions-item v-if="detail.error" label="错误代码">{{ detail.error.code }}</el-descriptions-item></el-descriptions>
      <el-form v-if="session.allows('jobs:control') && ['queued', 'retry_wait'].includes(detail.status) && !detail.current_attempt_id" inline class="detail-block" @submit.prevent="savePriority"><el-form-item label="优先级"><el-input-number v-model="priority" :min="-100" :max="100" :step="1" :precision="0"/></el-form-item><el-button native-type="submit" :loading="busy">保存优先级</el-button></el-form>
      <p v-if="session.allows('jobs:control') && ['queued', 'retry_wait'].includes(detail.status) && !detail.current_attempt_id" class="form-help">优先级结合租户内等待老化生效；调序不改变租户份额、当前占用或冻结授权。</p>
      <JobTimings :detail="detail"/>
      <h3>执行尝试</h3><el-table :data="detail.attempts" empty-text="尚未领取" table-layout="auto"><el-table-column label="尝试 / 渠道" min-width="250"><template #default="{ row }"><span class="mono">{{ row.id }}</span><router-link :to="{ path: '/channels', query: { channel: row.channel_id } }" class="id-line">{{ row.channel_id }}</router-link></template></el-table-column><el-table-column prop="fence" label="栅栏值" width="80"/><el-table-column label="GPU / 结果提交" min-width="190"><template #default="{ row }">{{ row.occupancy_uncertain ? '占用待核对' : row.gpu_occupied ? '占用中' : row.generation_released_at ? '已释放' : '待确认' }} / {{ row.closed_at ? '已关闭' : '未关闭' }}</template></el-table-column></el-table><h3>关联重试</h3><p v-if="!detail.retries.length" class="muted">暂无关联重试任务</p><el-button v-for="id in detail.retries" :key="id" text @click="inspect(id)">{{ id }}</el-button><h3>生成内容</h3><p v-if="!detail.artifacts.length" class="muted">暂无已交付产物</p><p v-for="id in detail.artifacts" :key="id" class="mono">{{ id }}</p>
    </template></div></el-drawer>
</template>

<style scoped>
.batch-actions { padding: 14px 20px; border-bottom: 1px solid #e8eef1; flex-wrap: wrap; }
@media (max-width: 600px) {
  .detail-block :deep(.el-descriptions__table) { table-layout: fixed; }
  .detail-block :deep(.el-descriptions__label.el-descriptions__cell) { width: 94px; }
  .detail-block :deep(.el-descriptions__content.el-descriptions__cell) { overflow-wrap: anywhere; }
}
</style>
