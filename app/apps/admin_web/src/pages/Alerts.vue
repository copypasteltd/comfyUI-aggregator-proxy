<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { api, readableError, time, unwrap } from '../api'
import type { components, paths } from '../generated/api'
import { useSession } from '../session'

type Kind = components['schemas']['AlertView']['kind']
type Filter = NonNullable<paths['/admin/api/v1/alerts']['get']['parameters']['query']>
const session = useSession()
const snapshot = ref<components['schemas']['AlertSnapshot'] | null>(null), applied = ref<Filter>({ page: 1 }), loading = ref(false), error = ref(''), filterError = ref('')
const draft = reactive<{ kind: Kind | ''; severity: 'critical' | 'warning' | ''; resource_id: string }>({ kind: '', severity: '', resource_id: '' })
const page = computed(() => applied.value.page ?? 1)
const names: Record<Kind, string> = { gpu_occupancy_uncertain: 'GPU 占用待核对', job_reconciling: '任务正在核对', node_heartbeat_stale: '节点心跳过期', channel_observation_stale: '渠道观测过期', channel_config_unverified: '渠道配置未验证', job_waiting_over_5m: '排队超过 5 分钟', operation_failed: '管理操作失败', gateway_settlement_unacked: '网关结算待确认', worker_storage_pressure: 'Worker 磁盘或暂存空间不足', worker_memory_pressure: 'Worker 可用内存不足', worker_upload_backlog: 'Worker 上传积压', worker_capacity_unavailable: 'Worker 容量观测不可用', spec_isolation: '生成规格已隔离', job_oom_recent: '最近已确认 OOM', job_output_invalid_recent: '最近已确认产物不合格' }
const taskNames: Record<string, string> = { text_to_image: '文生图', image_to_image: '图生图', references_to_image: '多参考图生图', text_to_video: '文生视频', references_to_video: '多参考图生视频', text_to_audio: 'Workflow 音频', image_to_3d: '单图生成 3D' }
const appliedLabel = computed(() => [applied.value.kind ? names[applied.value.kind] : '全部类型', applied.value.severity === 'critical' ? '严重' : applied.value.severity === 'warning' ? '待处理' : '全部级别', applied.value.resource_id].filter(Boolean).join(' · '))
const reasonNames = { disk_space: '磁盘余量不足', memory: '可用内存不足', spool_limit: '暂存预算不足', upload_backlog: '待回传数量达到上限', observation_unavailable: '等待有效容量观测' }
function bytes(value: number | null | undefined) { return value == null ? '未知' : (value / 1024 ** 3).toFixed(2) + ' GiB' }
let controller: AbortController | null = null, resume: AbortController | null = null, disposed = false
let timer: ReturnType<typeof setInterval> | null = null, deadline: ReturnType<typeof setTimeout> | null = null, resumeDeadline: ReturnType<typeof setTimeout> | null = null
function visible() { return !disposed && !document.hidden && !!session.info && !session.ending }
function stopRead() { controller?.abort(); controller = null; loading.value = false; if (deadline) clearTimeout(deadline); deadline = null }
function stop() { stopRead(); resume?.abort(); resume = null; if (resumeDeadline) clearTimeout(resumeDeadline); resumeDeadline = null; if (timer) clearInterval(timer); timer = null }
async function refresh() {
  if (!visible() || controller || !session.allows('operations:read')) return
  const request = new AbortController(); controller = request; loading.value = true
  deadline = setTimeout(() => request.abort(new DOMException('读取告警超时，请刷新', 'TimeoutError')), 10000)
  try {
    const data = unwrap(await api.GET('/admin/api/v1/alerts', { signal: request.signal, params: { query: applied.value } }))
    if (visible() && controller === request && !request.signal.aborted) { snapshot.value = data; error.value = '' }
  } catch (e) { if (visible() && controller === request) error.value = request.signal.reason instanceof DOMException && request.signal.reason.name === 'TimeoutError' ? '读取告警超时，请刷新' : readableError(e) }
  finally { if (controller === request) { if (deadline) clearTimeout(deadline); deadline = null; controller = null; loading.value = false } }
}
function apply() {
  const id = draft.resource_id.trim()
  if (id && !/^(?:gpu|job|node|ch|op|evt|iso)_[a-f0-9]{32}$/.test(id)) { filterError.value = '请输入完整资源 ID，支持任务、渠道、节点、GPU、操作、事件或隔离记录'; return }
  stopRead(); filterError.value = ''; error.value = ''; snapshot.value = null
  applied.value = { page: 1, kind: draft.kind || undefined, severity: draft.severity || undefined, resource_id: id || undefined }; void refresh()
}
function reset() { draft.kind = ''; draft.severity = ''; draft.resource_id = ''; apply() }
function goPage(delta: number) { const next = page.value + delta; if (loading.value || next < 1 || next > 1000) return; stopRead(); applied.value = { ...applied.value, page: next }; snapshot.value = null; error.value = ''; void refresh() }
async function visibility() {
  stop()
  if (!visible()) return
  const own = new AbortController(); resume = own
  resumeDeadline = setTimeout(() => own.abort(), 10000)
  try {
    await session.refresh(own.signal)
    if (resume !== own || own.signal.aborted || !visible()) return
    void refresh(); timer = setInterval(() => void refresh(), 5000)
  } catch (e) { if (resume === own && visible()) error.value = own.signal.aborted ? '会话核对超时，请重新打开告警页' : readableError(e) }
  finally { if (resume === own) { if (resumeDeadline) clearTimeout(resumeDeadline); resumeDeadline = null; resume = null } }
}
watch(() => [session.info?.csrf_token, session.ending] as const, ([token, ending], [old]) => { if (!token || ending || token !== old) { stop(); snapshot.value = null; error.value = ''; filterError.value = ''; applied.value = { page: 1 }; draft.kind = ''; draft.severity = ''; draft.resource_id = '' } })
onMounted(() => { visibility(); document.addEventListener('visibilitychange', visibility) })
onUnmounted(() => { disposed = true; stop(); document.removeEventListener('visibilitychange', visibility) })
</script>

<template>
  <div class="page-heading"><div><p class="eyebrow li-lab">OPERATIONS / ALERTS</p><h1>运行告警</h1><p class="muted">根据当前记录识别待处理事项，每 5 秒刷新。</p></div><el-button :icon="Refresh" :loading="loading" @click="refresh">刷新告警</el-button></div>
  <el-alert v-if="error" :title="error + (snapshot ? ' · 当前展示最后一次成功快照' : '')" type="error" :closable="false" show-icon/>
  <section class="panel li-card alert-filters" aria-label="告警筛选"><el-form label-position="top" @submit.prevent="apply"><div class="alert-filter-grid"><el-form-item label="告警类型"><el-select v-model="draft.kind" aria-label="告警类型" placeholder="全部告警类型" clearable><el-option v-for="(label, value) in names" :key="value" :label="label" :value="value"/></el-select></el-form-item><el-form-item label="告警级别"><el-select v-model="draft.severity" aria-label="告警级别" placeholder="全部级别" clearable><el-option label="严重" value="critical"/><el-option label="待处理" value="warning"/></el-select></el-form-item><el-form-item label="完整资源 ID"><el-input v-model="draft.resource_id" aria-label="完整资源 ID" maxlength="64" placeholder="例如 job_… 或 ch_…" clearable/></el-form-item></div><el-alert v-if="filterError" :title="filterError" type="error" :closable="false"/><div class="alert-filter-actions"><el-button @click="reset">重置筛选</el-button><el-button type="primary" native-type="submit">应用筛选</el-button></div></el-form></section>
  <div class="snapshot-line"><span>快照时间 {{ time(snapshot?.snapshot_at) }}</span><span>已应用：{{ appliedLabel }} · 当前筛选 {{ snapshot?.total ?? '—' }} 项</span></div>
  <section class="panel li-card">
    <el-table :data="snapshot?.data ?? []" v-loading="loading" :empty-text="!snapshot ? (loading ? '正在读取告警…' : error ? '告警读取失败' : '等待告警快照') : '当前筛选没有告警'" table-layout="auto">
      <el-table-column label="级别" width="110"><template #default="{ row }"><el-tag :type="row.severity === 'critical' ? 'danger' : 'warning'" size="small">{{ row.severity === 'critical' ? '严重' : '待处理' }}</el-tag></template></el-table-column>
      <el-table-column label="告警" min-width="210"><template #default="{ row }">{{ names[row.kind as Kind] }}<span class="id-line">{{ row.kind }}</span></template></el-table-column>
      <el-table-column label="关联资源" min-width="320"><template #default="{ row }"><router-link v-if="row.resource_id.startsWith('job_')" :to="{ path: '/jobs', query: { job: row.resource_id } }" class="mono">{{ row.resource_id }}</router-link><router-link v-else-if="row.kind === 'spec_isolation'" to="/spec-isolations" class="mono">{{ row.resource_id }}</router-link><router-link v-else-if="row.resource_id.startsWith('ch_')" :to="{ path: '/channels', query: { channel: row.resource_id } }" class="mono">{{ row.resource_id }}</router-link><span v-else class="mono">{{ row.resource_id }}</span></template></el-table-column>
      <el-table-column label="已确认失败上下文" min-width="290"><template #default="{ row }"><template v-if="row.failure"><strong>{{ row.failure.model }}</strong><span class="id-line">{{ taskNames[row.failure.task_type] }} · {{ row.failure.code === 'comfy_oom' ? '已确认显存不足' : '产物未通过交付校验' }}</span><span class="id-line">{{ row.failure.release_id }}</span><span class="id-line">{{ row.failure.attempt_id }}</span><router-link v-if="session.allows('channels:read')" :to="{ path: '/channels', query: { channel: row.failure.channel_id } }" class="id-line">{{ row.failure.channel_id }}</router-link></template><span v-else class="muted">—</span></template></el-table-column>
      <el-table-column label="容量观测" min-width="290"><template #default="{ row }">
        <template v-if="row.capacity"><span>{{ row.capacity.reasons.map((reason: keyof typeof reasonNames) => reasonNames[reason]).join('、') || '容量正常' }}</span>
          <span class="id-line">磁盘可用 {{ bytes(row.capacity.free_disk_bytes) }} · 最低 {{ bytes(row.capacity.minimum_free_disk_bytes) }}</span>
          <span class="id-line">内存可用 {{ bytes(row.capacity.available_memory_bytes) }} · 最低 {{ bytes(row.capacity.minimum_available_memory_bytes) }}</span>
          <span class="id-line">暂存 {{ bytes(row.capacity.spool_bytes) }} / {{ bytes(row.capacity.maximum_spool_bytes) }} · 已预留 {{ bytes(row.capacity.reserved_bytes) }}</span>
          <span class="id-line">待回传 {{ row.capacity.pending_uploads }} / {{ row.capacity.maximum_uploads }} · 下次预留 {{ bytes(row.capacity.generation_reserve_bytes) }}</span>
          <span class="id-line">采样 {{ time(row.capacity_observed_at) }} · 暂停接收新生成</span>
        </template><span v-else class="muted">{{ row.kind === 'worker_capacity_unavailable' ? '等待有效容量观测，暂停接收新生成' : '—' }}</span>
      </template></el-table-column>
      <el-table-column label="关联记录时间" min-width="185"><template #default="{ row }">{{ time(row.recorded_at) }}</template></el-table-column>
    </el-table>
    <div class="pagination"><el-button size="small" :disabled="page === 1 || loading" @click="goPage(-1)">上一页</el-button><span>第 {{ page }} 页</span><el-button size="small" :disabled="!snapshot?.has_more || loading || page >= 1000" @click="goPage(1)">下一页</el-button></div>
  </section><p class="muted">失联和未知占用需要核对执行情况。关联记录时间不等于告警持续时长。管理操作失败、已确认 OOM 和产物不合格保留最近 24 小时；失败任务重试成功后，原失败记录在窗口内仍保留。</p>
</template>

<style scoped>
.alert-filters { padding: 18px 20px; margin-bottom: 20px; }
.alert-filter-grid { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, .7fr) minmax(0, 1.4fr); gap: 16px; }
.alert-filter-grid :deep(.el-select) { width: 100%; }
.alert-filter-actions { display: flex; gap: 10px; justify-content: flex-end; margin-top: 10px; }
@media(max-width: 800px) { .alert-filter-grid { grid-template-columns: minmax(0, 1fr); gap: 0; } }
</style>
