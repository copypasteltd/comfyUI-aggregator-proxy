import { onMounted, onUnmounted, watch } from 'vue'
import { api, unwrap } from './api'
import { useSession } from './session'

const INTERVAL = 30_000, REQUEST_TIMEOUT = 10_000

/** Only actual, visible interaction can renew a session. Reads stay passive. */
export function useSessionActivity() {
  const session = useSession()
  let alive = true, verified = !document.hidden, pending = false, revision = 0
  let nextSend = 0, deadline = 0
  let sendTimer: ReturnType<typeof setTimeout> | undefined
  let deadlineTimer: ReturnType<typeof setTimeout> | undefined
  let requestTimer: ReturnType<typeof setTimeout> | undefined
  let request: AbortController | undefined

  function stop() {
    revision++; pending = false; verified = false
    clearTimeout(sendTimer); clearTimeout(deadlineTimer); clearTimeout(requestTimer)
    sendTimer = deadlineTimer = requestTimer = undefined
    request?.abort(); request = undefined
  }
  function expire() {
    stop()
    window.dispatchEvent(new CustomEvent('session-expired', { detail: '会话已到期，请重新登录' }))
  }
  function armDeadline() {
    clearTimeout(deadlineTimer); deadlineTimer = undefined
    const lifetime = session.info?.lifetime
    if (!alive || document.hidden || !lifetime) return
    // performance.now is unaffected by changes to the user's wall clock.
    deadline = performance.now() + Math.max(0, Math.min(lifetime.absolute_expires_at, lifetime.idle_expires_at) - lifetime.server_time) * 1000
    deadlineTimer = setTimeout(() => { deadlineTimer = undefined; void recheck() }, Math.max(1, deadline - performance.now()))
  }
  async function recheck() {
    stop()
    if (!alive || document.hidden || !session.info) return
    const current = revision, csrf = session.info.csrf_token
    const controller = new AbortController(); request = controller
    requestTimer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT)
    try {
      await session.refresh(controller.signal)
      if (alive && revision === current && !controller.signal.aborted && session.info?.csrf_token === csrf) { verified = true; armDeadline() }
    } catch {
      if (alive && revision === current && session.info?.csrf_token === csrf) expire()
    } finally {
      if (request === controller) { clearTimeout(requestTimer); requestTimer = undefined; request = undefined }
    }
  }
  function schedule() {
    if (!pending || request || sendTimer || !alive || !verified || document.hidden || !session.info) return
    sendTimer = setTimeout(() => { sendTimer = undefined; void send() }, Math.max(0, nextSend - performance.now()))
  }
  async function send() {
    if (!pending || request || !alive || !verified || document.hidden || !session.info) return
    if (performance.now() >= deadline) { void recheck(); return }
    pending = false; nextSend = performance.now() + INTERVAL
    const current = revision, csrf = session.info.csrf_token
    const controller = new AbortController(); request = controller
    requestTimer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT)
    try {
      const result = await api.POST('/admin/api/v1/session/activity', { body: { activity: 'interactive' }, signal: controller.signal })
      if (alive && revision === current && !controller.signal.aborted && session.info?.csrf_token === csrf) session.updateLifetime(unwrap(result), csrf)
    } catch { /* Keep the known deadline; only subsequent real input may retry. */ }
    finally {
      if (request === controller) { clearTimeout(requestTimer); requestTimer = undefined; request = undefined; schedule() }
    }
  }
  function interact(event: Event) {
    if (!event.isTrusted || !verified || session.ending || document.hidden || !session.info?.lifetime) return
    pending = true; schedule()
  }
  function visibility() { if (document.hidden) stop(); else void recheck() }
  watch(() => session.info?.csrf_token, () => {
    stop(); nextSend = 0
    if (alive && !document.hidden && session.info) { verified = true; armDeadline() }
  }, { immediate: true })
  watch(() => session.info?.lifetime, () => { if (verified) armDeadline() })
  watch(() => session.ending, value => { if (value) stop(); else if (session.info) void recheck() }, { flush: 'sync' })
  onMounted(() => {
    for (const name of ['pointerdown', 'keydown', 'wheel']) window.addEventListener(name, interact, { passive: true, capture: true })
    document.addEventListener('visibilitychange', visibility)
  })
  onUnmounted(() => {
    alive = false; stop()
    for (const name of ['pointerdown', 'keydown', 'wheel']) window.removeEventListener(name, interact, true)
    document.removeEventListener('visibilitychange', visibility)
  })
}
