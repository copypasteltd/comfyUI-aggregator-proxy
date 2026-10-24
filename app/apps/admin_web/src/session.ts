import { defineStore } from 'pinia'
import { ref } from 'vue'
import { api, setCsrf, unwrap, type Session, type ScopeOption } from './api'
import type { components } from './generated/api'

export const useSession = defineStore('session', () => {
  const info = ref<Session | null>(null)
  const ready = ref(false)
  const ending = ref(false)
  let epoch = 0
  function accept(value: Session) { info.value = value; setCsrf(value.csrf_token); ready.value = true }
  function clear() { epoch++; info.value = null; setCsrf(''); ready.value = true; ending.value = false }
  async function refresh(signal?: AbortSignal) {
    const current = epoch
    const result = await api.GET('/admin/api/v1/session', { signal })
    if (current !== epoch || signal?.aborted) return
    if (result.response.status === 401) { clear(); return }
    accept(unwrap(result))
  }
  async function login(username: string, password: string) {
    const current = ++epoch
    const result = await api.POST('/admin/api/v1/session', { body: { username, password } })
    if (current === epoch) accept(unwrap(result))
  }
  async function logout() {
    const current = ++epoch
    ending.value = true
    try {
      const result = await api.DELETE('/admin/api/v1/session')
      if (current !== epoch) return
      if (result.response.ok || result.response.status === 401) clear()
      else throw new Error('退出登录失败，请重试')
    } finally { if (current === epoch) ending.value = false }
  }
  function updateLifetime(value: components['schemas']['SessionLifetime'], expectedCsrf: string) {
    if (info.value?.csrf_token === expectedCsrf) info.value.lifetime = value
  }
  function allows(permission: string) { return info.value?.permissions.includes(permission) ?? false }
  function rememberScope(value: ScopeOption) {
    if (!info.value) return
    const scope = { tenant_id: value.tenant_id, tenant_name: value.tenant_name, project_id: value.project_id, project_name: value.project_name }
    info.value.scopes = [scope, ...info.value.scopes.filter(item => item.project_id !== scope.project_id)].slice(0, 100)
  }
  return { info, ready, ending, accept, clear, refresh, login, logout, updateLifetime, allows, rememberScope }
})
