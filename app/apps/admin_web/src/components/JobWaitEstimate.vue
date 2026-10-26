<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { api, readableError, time, unwrap } from '../api'
import type { components } from '../generated/api'
import { useSession } from '../session'

type Estimate = components['schemas']['WaitEstimate']
const props = defineProps<{ jobId: string; revision: number; active: boolean }>()
const session = useSession()
const value = ref<Estimate | null>(null), busy = ref(false), error = ref(''), expired = ref(false)
let request: AbortController | undefined, expiry: ReturnType<typeof setTimeout> | undefined
const reasons: Record<NonNullable<Estimate['reason']>, string> = {
  not_queued: '任务已分配、结束或正在取消，无需估算排队等待。',
  validation_task: '验证任务不使用生产等待估算。',
  no_ready_route: '暂无可用的已批准渠道，请检查渠道状态与运行批准。',
  stale_observation: '设备或渠道观测已过期，恢复连接后可重新估算。',
  uncertain_execution: '当前执行仍待核对，确认占用后可重新估算。',
  insufficient_samples: '近期同规格成功样本不足，积累有效运行记录后可重新估算。',
  quota_blocked: '当前并发额度不足，额度释放后可重新估算。',
  deadline: '无法在任务截止前给出可靠的等待估算。',
  capacity_limit: '当前规模超过本次估算范围，请稍后重试。',
  execution_unavailable: '运行记录不完整，暂无法估算。',
}
const interval = computed(() => {
  const item = value.value
  if (!item || item.status !== 'estimated' || item.lower_wait_ms == null || item.upper_wait_ms == null) return '暂无法估算'
  if (item.upper_wait_ms === 0) return '当前可分配'
  const lower = Math.floor(item.lower_wait_ms / 1000), upper = Math.ceil(item.upper_wait_ms / 1000)
  return `${lower.toLocaleString()}～${upper.toLocaleString()} 秒`
})
function reset() {
  request?.abort(); request = undefined
  if (expiry) clearTimeout(expiry)
  expiry = undefined; busy.value = false; value.value = null; error.value = ''; expired.value = false
}
async function refresh() {
  if (!props.active || document.hidden || busy.value) return
  reset()
  const own = new AbortController(), jobId = props.jobId, revision = props.revision, started = performance.now()
  request = own; busy.value = true
  try {
    const data = unwrap(await api.GET('/admin/api/v1/jobs/{identifier}/wait-estimate', {
      params: { path: { identifier: jobId } }, signal: AbortSignal.any([own.signal, AbortSignal.timeout(5000)]),
    }))
    if (request !== own || own.signal.aborted || !props.active || document.hidden || props.jobId !== jobId || props.revision !== revision) return
    value.value = data
    const remaining = Math.max(0, data.valid_until_ms - data.snapshot_at_ms - (performance.now() - started))
    expired.value = remaining === 0
    if (remaining > 0) expiry = setTimeout(() => { expired.value = true; expiry = undefined }, remaining)
  } catch (cause) {
    if (request === own && !own.signal.aborted) error.value = readableError(cause)
  } finally {
    if (request === own) { request = undefined; busy.value = false }
  }
}
function visibility() { if (document.hidden) reset() }
watch([() => props.jobId, () => props.revision, () => props.active], reset)
watch(() => session.info, info => { if (!info) reset() })
onMounted(() => document.addEventListener('visibilitychange', visibility))
onUnmounted(() => { reset(); document.removeEventListener('visibilitychange', visibility) })
</script>

<template>
  <section class="wait-estimate detail-block" aria-label="预计可分配等待">
    <div class="wait-heading"><h3>预计可分配等待</h3><el-button :loading="busy" :disabled="!active" @click="refresh">{{ value ? '重新估算' : '估算等待' }}</el-button></div>
    <el-alert v-if="error" :title="error" type="error" :closable="false"/>
    <div v-if="value" aria-live="polite" class="wait-result">
      <template v-if="expired"><el-tag type="warning">估算已过期</el-tag><p>队列可能已变化，请重新估算。</p></template>
      <template v-else-if="value.status === 'estimated'"><strong class="wait-interval">{{ interval }}</strong><p class="muted">使用 {{ value.sample_count }} 条样本，各组至少 {{ value.minimum_route_samples }} 条。</p></template>
      <template v-else><el-tag type="info">{{ value.status === 'not_waiting' ? '无需等待估算' : '暂无法估算' }}</el-tag><p>{{ value.reason ? reasons[value.reason] : '请稍后重新估算。' }}</p></template>
      <p class="muted">快照 {{ time(value.snapshot_at_ms / 1000) }} · 有效 15 秒</p>
      <p v-if="value.oldest_sample_at_ms != null && value.newest_sample_at_ms != null" class="muted">样本 {{ time(value.oldest_sample_at_ms / 1000) }} 至 {{ time(value.newest_sample_at_ms / 1000) }}</p>
    </div>
    <p v-else-if="!error" class="muted">结合当前排队与近期运行样本，按需估算。</p>
    <p class="form-help">区间不含生成和上传。新增任务、设备变化和领取延迟会影响实际分配；样本不足时显示未知。</p>
  </section>
</template>

<style scoped>
.wait-estimate { padding: 16px; border: 1px solid var(--rc-line); border-radius: var(--rc-radius-card); background: var(--rc-canvas); }
.wait-heading { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 10px; margin-bottom: 10px; }
.wait-heading h3 { margin: 0; }
.wait-result { display: grid; gap: 6px; overflow-wrap: anywhere; }
.wait-interval { font-size: 24px; line-height: 1.3; font-variant-numeric: tabular-nums; color: var(--rc-ink); }
</style>
