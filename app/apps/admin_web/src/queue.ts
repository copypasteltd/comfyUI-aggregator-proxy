import { onMounted, onUnmounted, ref, shallowRef, watch } from 'vue'
import { readableError } from './api'
import type { paths } from './generated/api'
import { useSession } from './session'

export type QueueFilters = Omit<NonNullable<paths['/admin/api/v1/queues/jobs']['get']['parameters']['query']>, 'page'>
export const queueStates: Record<string, string> = { admission_pending: '待网关确认', queued: '排队中', leased: '已领取', preparing: '准备中', submitting: '提交中', running: '生成中', collecting: '收集产物', uploading: '上传中', verifying: '验证中', reconciling: '待核对', retry_wait: '等待重试', cancel_requested: '等待取消确认', succeeded: '成功', failed: '失败', cancelled: '已取消', expired: '已过期' }
export function waitTime(value: number | null | undefined) {
  if (value == null) return '未知'
  if (value < 60) return `${value} 秒`
  if (value < 3600) return `${Math.floor(value / 60)} 分 ${value % 60} 秒`
  return `${Math.floor(value / 3600)} 时 ${Math.floor(value % 3600 / 60)} 分`
}

export function useQueueRefresh<T>(read: (query: QueueFilters & { page: number }, signal: AbortSignal) => Promise<T>, stream = false) {
  const session = useSession(), value = shallowRef<T | null>(null), filters = ref<QueueFilters>({}), page = ref(1)
  const loading = ref(false), error = ref(''), connected = ref(false)
  let alive = true, dirty = false, request: AbortController | null = null
  let events: EventSource | null = null, timer: ReturnType<typeof setInterval> | null = null
  async function refresh() {
    if (!alive || document.hidden || !session.info) return
    if (request) { dirty = true; return }
    const current = new AbortController(); request = current; loading.value = true
    try {
      const result = await read({ ...filters.value, page: page.value }, current.signal)
      if (alive && request === current && !current.signal.aborted) { value.value = result; error.value = '' }
    } catch (e) { if (alive && request === current && !current.signal.aborted) error.value = readableError(e) }
    finally {
      if (request === current) { request = null; loading.value = false; if (dirty) { dirty = false; void refresh() } }
    }
  }
  function cancel() { request?.abort(); request = null; loading.value = false; dirty = false }
  function stop() { cancel(); events?.close(); events = null; connected.value = false; if (timer) clearInterval(timer); timer = null }
  function start() {
    stop()
    if (!alive || document.hidden || !session.info) return
    if (stream) {
      events = new EventSource('/admin/api/v1/events')
      events.onopen = () => { connected.value = true; void refresh() }
      events.addEventListener('invalidation', () => void refresh())
      events.addEventListener('session_revoked', () => { stop(); session.clear() })
      events.onerror = () => { connected.value = false }
    }
    timer = setInterval(() => void refresh(), 5000)
    void refresh()
  }
  async function visibility() {
    stop()
    if (!document.hidden && alive) {
      try { await session.refresh(); start() } catch (e) { if (alive) error.value = readableError(e) }
    }
  }
  function apply(next: QueueFilters) { cancel(); filters.value = { ...next }; page.value = 1; value.value = null; error.value = ''; void refresh() }
  function goPage(delta: number) { cancel(); page.value = Math.max(1, Math.min(10000, page.value + delta)); value.value = null; error.value = ''; void refresh() }
  watch(() => session.info, info => { if (!info) stop() })
  onMounted(() => { start(); document.addEventListener('visibilitychange', visibility) })
  onUnmounted(() => { alive = false; stop(); document.removeEventListener('visibilitychange', visibility) })
  return { value, filters, page, loading, error, connected, refresh, apply, goPage }
}
