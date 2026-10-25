<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { api, readableError, unwrap, type KeyPolicyOption } from '../api'

const props = withDefaults(defineProps<{ modelValue: string[]; tenantId: string; kind: 'project' | 'creator_key'; projectIds?: string[]; active: boolean; disabled?: boolean }>(), { projectIds: () => [], disabled: false })
const emit = defineEmits<{ 'update:modelValue': [value: string[]]; validity: [value: boolean] }>()
const rows = ref<KeyPolicyOption[]>([]), selected = ref<Record<string, KeyPolicyOption>>({}), unresolved = ref<string[]>([])
const query = ref(''), page = ref(1), hasMore = ref(false), loading = ref(false), resolving = ref(false), visible = ref(false)
const browseError = ref(''), resolveError = ref(''), verified = ref(false)
const label = computed(() => props.kind === 'project' ? '允许共享的项目' : '允许的创建者 Key')
const contextKey = computed(() => JSON.stringify([props.tenantId, props.kind, props.kind === 'creator_key' ? props.projectIds : []]))
const requestShape = () => ({ kind: props.kind, tenant_id: props.tenantId, project_ids: props.kind === 'creator_key' ? [...props.projectIds] : [] })
const options = computed(() => {
  const values = new Map<string, KeyPolicyOption | null>()
  for (const id of props.modelValue) values.set(id, selected.value[id] ?? null)
  for (const row of rows.value) values.set(row.id, row)
  return [...values].map(([id, row]) => ({ id, row }))
})
const valid = computed(() => !props.modelValue.length || (verified.value && !resolving.value && !resolveError.value && !unresolved.value.length))
watch(() => [valid.value, props.active] as const, ([value, active]) => emit('validity', active && value), { immediate: true })
let browse: AbortController | null = null, resolver: AbortController | null = null, browseSequence = 0, resolveSequence = 0, debounce: number | undefined
function cancelBrowse() { window.clearTimeout(debounce); browseSequence++; browse?.abort(); loading.value = false }
function cancelResolve() { resolveSequence++; resolver?.abort(); resolving.value = false }
function text(row: KeyPolicyOption | null, id: string) {
  if (!row) return id + (resolving.value ? ' · 核对中' : ' · 无法使用，请移除')
  const state = { active: '', disabled: ' · 已停用', expired: ' · 已到期', archived: ' · 已归档' }[row.lifecycle]
  return (row.name || id) + (props.kind === 'creator_key' ? ' / ' + row.project_name : '') + state
}
async function load() {
  cancelBrowse()
  if (!props.active || !props.tenantId || (props.kind === 'creator_key' && !props.projectIds.length)) { rows.value = []; hasMore.value = false; return }
  browse = new AbortController(); const active = ++browseSequence
  loading.value = true; browseError.value = ''
  try {
    const result = unwrap(await api.POST('/admin/api/v1/key-policy/options/query', { body: { ...requestShape(), mode: 'browse', page: page.value, q: query.value }, signal: browse.signal }))
    if (active !== browseSequence) return
    rows.value = result.data; hasMore.value = result.has_more
  } catch (e) { if (active === browseSequence && !(e instanceof DOMException && e.name === 'AbortError')) browseError.value = readableError(e) }
  finally { if (active === browseSequence) loading.value = false }
}
async function resolve() {
  cancelResolve(); verified.value = false; resolveError.value = ''
  const ids = [...props.modelValue]
  selected.value = Object.fromEntries(ids.filter(id => selected.value[id]).map(id => [id, selected.value[id]!]))
  if (!ids.length) { unresolved.value = []; verified.value = true; return }
  if (!props.active || !props.tenantId) return
  if (props.kind === 'creator_key' && !props.projectIds.length) { unresolved.value = ids; verified.value = true; return }
  resolver = new AbortController(); const active = ++resolveSequence
  resolving.value = true
  try {
    const result = unwrap(await api.POST('/admin/api/v1/key-policy/options/query', { body: { ...requestShape(), mode: 'resolve', ids, page: 1, q: '' }, signal: resolver.signal }))
    if (active !== resolveSequence) return
    selected.value = Object.fromEntries(result.data.map(row => [row.id, row])); unresolved.value = result.unresolved_ids; verified.value = true
  } catch (e) { if (active === resolveSequence && !(e instanceof DOMException && e.name === 'AbortError')) resolveError.value = readableError(e) }
  finally { if (active === resolveSequence) resolving.value = false }
}
function search(value: string) { window.clearTimeout(debounce); debounce = window.setTimeout(() => { query.value = value; page.value = 1; void load() }, 250) }
function opened(value: boolean) { visible.value = value; cancelBrowse(); if (value) { query.value = ''; page.value = 1; void load() } }
function choose(values: string[]) { if (values.length <= 1000) emit('update:modelValue', [...values]) }
watch(() => JSON.stringify([props.active, contextKey.value, props.modelValue]), (_value, old) => {
  const previous = old ? JSON.parse(old) as [boolean, string, string[]] : null
  cancelResolve()
  if (previous?.[1] !== contextKey.value) { cancelBrowse(); rows.value = []; selected.value = {}; page.value = 1; query.value = ''; hasMore.value = false }
  if (!props.active) { cancelBrowse(); verified.value = false; return }
  void resolve()
  if (visible.value && previous?.[1] !== contextKey.value) void load()
}, { immediate: true })
onBeforeUnmount(() => { cancelBrowse(); cancelResolve() })
</script>

<template>
  <div class="policy-directory-field">
    <el-select :model-value="modelValue" :aria-label="label" :disabled="disabled || !tenantId" multiple collapse-tags collapse-tags-tooltip :max-collapse-tags="2" :multiple-limit="1000" filterable remote :remote-method="search" :loading="loading" placeholder="搜索名称或完整 ID，可翻页多选" class="full" @visible-change="opened" @update:model-value="choose">
      <template #header><p class="form-help">{{ kind === 'creator_key' && !projectIds.length ? '请先选择共享项目，再选择其中的创建者。' : '包含停用或归档主体的历史内容；选择不会恢复其凭证。' }}</p><p v-if="browseError" role="alert" class="directory-error">{{ browseError }}</p><el-button v-if="browseError" link @click.stop="load">重试查询</el-button></template>
      <el-option v-for="option in options" :key="option.id" :value="option.id" :label="text(option.row, option.id)" class="policy-directory-option"><strong>{{ option.row?.name || option.id }}</strong><span v-if="option.row" class="directory-option-detail">{{ kind === 'creator_key' ? option.row.project_name + ' · ' : '' }}{{ { active: '可共享历史内容', disabled: '已停用', expired: '已到期', archived: '已归档' }[option.row.lifecycle] }}{{ option.row.origin === 'gateway' ? ' · 网关主体' : '' }}</span><span v-else class="directory-error">{{ resolving ? '正在核对归属' : '无法使用，请移除或恢复共享项目' }}</span><small class="mono directory-option-id">{{ option.id }}</small></el-option>
      <template #footer><div class="directory-pagination"><el-button size="small" :disabled="page === 1 || loading" @click.stop="page--; load()">上一页</el-button><span>第 {{ page }} 页</span><el-button size="small" :disabled="!hasMore || loading" @click.stop="page++; load()">下一页</el-button></div></template>
    </el-select>
    <p class="form-help directory-status" aria-live="polite">已选 {{ modelValue.length }} / 1,000 <span v-if="resolving">· 正在核对</span><span v-else-if="unresolved.length" class="directory-error">· {{ unresolved.length }} 项无法使用，请处理后保存</span></p>
    <p v-if="resolveError" role="alert" class="directory-error">{{ resolveError }} <el-button link @click="resolve">重新核对已选项</el-button></p>
  </div>
</template>

<style scoped>
.policy-directory-field { width: 100%; min-width: 0; }
.policy-directory-option { height: auto; min-height: 64px; line-height: 1.5; padding-top: 8px; padding-bottom: 8px; }
.policy-directory-option strong, .directory-option-detail, .directory-option-id { display: block; }
.policy-directory-option strong { overflow: hidden; text-overflow: ellipsis; font-size: 13px; }
.directory-option-detail, .directory-option-id { font-size: 11px; color: var(--rc-text-muted); }
.directory-option-id { overflow-wrap: anywhere; white-space: normal; }
.directory-error { color: var(--rc-warning); font-size: 12px; }
.directory-status { margin: 6px 0 0; line-height: 1.7; }
.directory-pagination { display: flex; align-items: center; justify-content: space-between; gap: 10px; font-size: 12px; }
</style>
