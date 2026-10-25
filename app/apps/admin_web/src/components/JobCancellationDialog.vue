<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { api, ApiError, readableError, time, unwrap } from '../api'
import type { components } from '../generated/api'
import { useSession } from '../session'
import { queueStates } from '../queue'

type Job = components['schemas']['AdminJobView']
type Body = components['schemas']['JobCancellationBatch']
type View = components['schemas']['JobCancellationView']
type Preview = Pick<Job, 'id' | 'model' | 'status' | 'task_type'>
const props = defineProps<{ modelValue: boolean; jobs: Job[]; operationId: string }>()
const emit = defineEmits<{ 'update:modelValue': [value: boolean]; accepted: [id: string] }>()
const session = useSession()
const operation = ref(''), preview = ref<Preview[]>([]), reason = ref<Body['reason']>('operator_request')
const snapshot = ref<View | null>(null), error = ref(''), reading = ref(false), writing = ref(false), uncertain = ref(false)
const reasons: Record<Body['reason'], string> = { operator_request: '运维处置', key_access_changed: 'Key 访问策略调整', maintenance: '系统维护' }
const outcomes: Record<string, string> = { pending: '等待处理', cancelled: '已确认取消', cancel_requested: '取消请求已记录', unchanged: '保留原终态', failed: '此项处理失败' }
const errors: Record<string, string> = { not_found: '任务不存在', database_busy: '连续五次处理受阻，请核对后重新选择', cancellation_rejected: '当前状态拒绝取消，请查看任务详情' }
let pending: { token: string; body: Body; jobs: Preview[] } | null = null
let alive = true, read: AbortController | null = null, write: AbortController | null = null
let deadline: ReturnType<typeof setTimeout> | null = null, poll: ReturnType<typeof setInterval> | null = null
const canSubmit = computed(() => session.allows('jobs:control') && !operation.value && !writing.value && !uncertain.value && preview.value.length > 0)
function visible() { return alive && props.modelValue && !document.hidden && !!session.info && !session.ending }
function stopRead() { read?.abort(); read = null; reading.value = false; if (deadline) clearTimeout(deadline); deadline = null }
function stopPoll() { if (poll) clearInterval(poll); poll = null; stopRead() }
function close() {
  stopPoll()
  if (write) { uncertain.value = true; write.abort(); write = null; writing.value = false }
  emit('update:modelValue', false)
}
async function refresh() {
  const id = operation.value
  if (!visible() || !id || read) return
  const own = new AbortController(); read = own; reading.value = true
  deadline = setTimeout(() => own.abort(new DOMException('读取取消结果超时，请刷新', 'TimeoutError')), 10000)
  try {
    const result = unwrap(await api.GET('/admin/api/v1/job-cancellations/{identifier}', { params: { path: { identifier: id } }, signal: own.signal }))
    if (visible() && read === own && operation.value === id && !own.signal.aborted) { snapshot.value = result; error.value = '' }
  } catch (e) {
    if (visible() && read === own && operation.value === id) error.value = own.signal.reason instanceof DOMException && own.signal.reason.name === 'TimeoutError' ? '读取取消结果超时，请刷新' : readableError(e)
  } finally { if (read === own) { if (deadline) clearTimeout(deadline); deadline = null; read = null; reading.value = false } }
}
function startPoll() { stopPoll(); if (visible() && operation.value) { void refresh(); poll = setInterval(() => void refresh(), 5000) } }
async function submit(replay = false) {
  if (!visible() || writing.value || operation.value || !session.allows('jobs:control')) return
  if (replay) { if (!pending) return }
  else {
    if (!canSubmit.value || pending) return
    pending = { token: crypto.randomUUID(), body: { job_ids: preview.value.map(job => job.id), reason: reason.value }, jobs: preview.value.map(job => ({ ...job })) }
  }
  const submission = pending!, own = new AbortController(); write = own; writing.value = true; uncertain.value = false; error.value = ''
  const timer = setTimeout(() => own.abort(), 10000)
  try {
    const result = unwrap(await api.POST('/admin/api/v1/job-cancellations', { headers: { 'Idempotency-Key': submission.token }, body: submission.body, signal: own.signal }))
    if (visible() && write === own && !own.signal.aborted) {
      pending = null; operation.value = result.operation_id; emit('accepted', result.operation_id); startPoll()
    }
  } catch (e) {
    if (visible() && write === own) {
      const rejected = e instanceof ApiError && e.status >= 400 && e.status < 500 && e.status !== 408
      if (rejected) pending = null
      uncertain.value = !rejected
      error.value = rejected ? readableError(e) : '提交结果尚未确认，请用原请求核对。关闭页面不表示取消意图已撤销。'
    }
  } finally { clearTimeout(timer); if (write === own) { write = null; writing.value = false } }
}
watch(() => [props.modelValue, props.operationId] as const, ([open, id]) => {
  stopPoll(); snapshot.value = null; error.value = ''; operation.value = id
  if (write) { write.abort(); write = null; writing.value = false }
  uncertain.value = !!pending
  if (open) {
    preview.value = pending?.jobs ?? props.jobs.map(({ id, model, status, task_type }) => ({ id, model, status, task_type }))
    reason.value = pending?.body.reason ?? 'operator_request'
    if (id) startPoll()
  }
}, { immediate: true })
watch(() => [session.info?.csrf_token, session.ending] as const, ([token, ending], [old]) => { if (!token || ending || token !== old) { pending = null; preview.value = []; snapshot.value = null; uncertain.value = false; close() } })
function visibility() { if (document.hidden) close() }
document.addEventListener('visibilitychange', visibility)
onBeforeUnmount(() => { alive = false; stopPoll(); write?.abort(); write = null; pending = null; document.removeEventListener('visibilitychange', visibility) })
</script>

<template>
  <el-dialog :model-value="modelValue" title="批量取消任务" width="min(800px, 94vw)" top="5vh" :show-close="!writing" :close-on-click-modal="!writing" :close-on-press-escape="!writing" @update:model-value="close">
    <el-alert v-if="error" :title="error + (snapshot ? ' · 显示上次成功读取的结果' : '')" type="error" :closable="false"/>
    <el-alert v-if="uncertain" title="原任务范围与原因已锁定，请先核对这次提交。" type="warning" :closable="false"/>
    <template v-if="!operation">
      <p class="cancellation-intro">确认这 {{ preview.length }} 个明确任务。未领取任务可立即取消；已领取任务需等待设备确认停止。</p>
      <el-select v-model="reason" aria-label="批量取消原因" :disabled="writing || uncertain" class="cancellation-reason"><el-option v-for="(label, value) in reasons" :key="value" :label="label" :value="value"/></el-select>
      <div class="cancellation-targets" aria-label="待取消任务范围"><article v-for="job in preview" :key="job.id" class="cancellation-target"><strong>{{ job.model }}</strong><el-tag size="small">{{ queueStates[job.status] ?? job.status }}</el-tag><code>{{ job.id }}</code></article></div>
      <p class="form-help">仅处理以上任务，不扩展到筛选结果中的其他任务。运行状态以执行取消时为准。</p>
    </template>
    <template v-else>
      <p class="cancellation-operation mono">{{ operation }}</p>
      <p class="cancellation-intro">批次已持久受理，关闭此窗口后仍继续处理。每项结果与当前任务状态分别显示。</p>
      <div class="toolbar"><el-button :loading="reading" @click="refresh">刷新取消结果</el-button><span v-if="snapshot" class="muted">采样于 {{ time(snapshot.snapshot_at) }}</span></div>
      <p v-if="snapshot" class="cancellation-intro">{{ snapshot.processing_complete ? '本批次处理完毕' : '正在逐项处理' }} · {{ reasons[snapshot.reason] }} · {{ snapshot.targets.length }} 个任务</p>
      <div v-loading="reading" class="cancellation-targets" aria-live="polite"><article v-for="target in snapshot?.targets ?? []" :key="target.job_id" class="cancellation-target"><router-link :to="{ path: '/jobs', query: { job: target.job_id } }" @click="close">{{ target.job_id }}</router-link><el-tag :type="target.outcome === 'failed' ? 'danger' : target.outcome === 'cancelled' ? 'success' : 'info'">{{ outcomes[target.outcome] }}</el-tag><p>当前任务：{{ target.current_status ? queueStates[target.current_status] ?? target.current_status : '不存在或不可读取' }}</p><p v-if="target.error_code" class="muted">{{ errors[target.error_code] }}</p><p v-else-if="target.transient_attempts" class="muted">此前处理受阻 {{ target.transient_attempts }} 次</p></article></div>
      <p class="form-help">批次处理完毕表示取消意图均有处理结果。当前仍等待取消确认的任务需要设备核对，不表示 GPU 已释放。</p>
    </template>
    <template #footer><div class="cancellation-footer"><el-button :disabled="writing" @click="close">关闭</el-button><el-button v-if="uncertain && !operation" :loading="writing" @click="submit(true)">用原请求核对</el-button><el-button v-else-if="!operation" type="danger" :disabled="!canSubmit" :loading="writing" @click="submit()">确认取消这 {{ preview.length }} 个任务</el-button></div></template>
  </el-dialog>
</template>

<style scoped>
.cancellation-intro { margin: 14px 0; line-height: 1.7; }
.cancellation-reason { width: min(300px, 100%); margin-bottom: 12px; }
.cancellation-targets { max-height: 48vh; overflow: auto; border-block: 1px solid var(--rc-line); }
.cancellation-target { padding: 14px 4px; border-bottom: 1px solid var(--rc-line); display: flex; gap: 9px 12px; flex-wrap: wrap; align-items: center; font-size: 13px; line-height: 1.6; }
.cancellation-target > code, .cancellation-target > a, .cancellation-target > p { flex-basis: 100%; overflow-wrap: anywhere; }
.cancellation-target > code, .cancellation-target > a { font-family: "Cascadia Mono", Consolas, monospace; }
.cancellation-operation { margin-top: 12px; overflow-wrap: anywhere; }
.cancellation-footer { display: flex; gap: 10px; justify-content: flex-end; flex-wrap: wrap; }
</style>
