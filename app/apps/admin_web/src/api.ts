import createClient from 'openapi-fetch'
import type { paths, components } from './generated/api'

export type Session = components['schemas']['SessionView']
export type Channel = components['schemas']['ChannelView']
export type Node = components['schemas']['NodeView']
export type KeyRecord = components['schemas']['KeyView']
export type KeyPolicy = components['schemas']['KeyPolicy']
export type Queue = components['schemas']['QueueView']
export type ChannelQueue = components['schemas']['ChannelQueueView']
export type Inventory = components['schemas']['InventoryView']
export type TenantRecord = components['schemas']['TenantView']
export type ProjectRecord = components['schemas']['ProjectView']
export type ScopeOption = components['schemas']['ScopeOption']
export type ScopeLimits = components['schemas']['ScopeLimits']
export type KeyPolicyOption = components['schemas']['KeyPolicyOption']

let csrf = ''
let sessionRevision = 0
const requestSessions = new WeakMap<Request, number>()
export const setCsrf = (value: string) => { if (csrf !== value) sessionRevision++; csrf = value }
export const api = createClient<paths>({ credentials: 'same-origin' })
api.use({
  onRequest({ request }) {
    requestSessions.set(request, sessionRevision)
    if (!['GET', 'HEAD'].includes(request.method)) {
      if (csrf) request.headers.set('X-CSRF-Token', csrf)
      if (!request.headers.has('Idempotency-Key')) request.headers.set('Idempotency-Key', crypto.randomUUID())
    }
    return request
  },
  onResponse({ request, response }) {
    if (response.status === 401 && requestSessions.get(request) === sessionRevision) window.dispatchEvent(new Event('session-expired'))
    return response
  },
})

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message) }
}

export function unwrap<T>(result: { data?: T; error?: unknown; response: Response }): T {
  if (!result.response.ok || result.data === undefined) {
    const body = result.error as { error?: { message?: string; request_id?: string } } | undefined
    throw new ApiError(result.response.status, body?.error?.message ?? `请求失败（${result.response.status}）`)
  }
  return result.data
}

export function revisionHeaders(revision: number) { return { 'If-Match': `"${revision}"` } }
export const readableError = (error: unknown) => error instanceof Error ? error.message : '操作失败，请重试'
export const time = (seconds?: number | null) => seconds ? new Date(seconds * 1000).toLocaleString('zh-CN', { hour12: false }) : '未知'
