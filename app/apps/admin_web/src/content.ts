import type { paths } from './generated/api'

type Query = NonNullable<paths['/admin/api/v1/content']['get']['parameters']['query']>
export type ContentFilters = Omit<Query, 'kind' | 'page'>
export const contentStates = { available: '可用', unverified: '未验证', expired: '已到期', pending_reclaim: '等待回收', reclaimed: '已回收' }
export function contentStatus(state?: string | null) { return state && state in contentStates ? contentStates[state as keyof typeof contentStates] : '状态未知' }
