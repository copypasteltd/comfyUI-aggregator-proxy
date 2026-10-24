<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { api, readableError, unwrap, type ScopeOption } from '../api'
import { useSession } from '../session'

const props = withDefaults(defineProps<{ modelValue: string; disabled?: boolean; active?: boolean; allowInactive?: boolean; clearable?: boolean; label?: string }>(), { disabled: false, active: true, allowInactive: false, clearable: false, label: '所属项目' })
const emit = defineEmits<{ 'update:modelValue': [value: string]; resolved: [value: ScopeOption | null] }>()
const session = useSession(), rows = ref<ScopeOption[]>([]), current = ref<ScopeOption | null>(null)
const query = ref(''), page = ref(1), hasMore = ref(false), loading = ref(false), error = ref(''), inactiveOnly = ref(false)
const options = computed(() => current.value && !rows.value.some(row => row.project_id === current.value?.project_id) ? [current.value, ...rows.value] : rows.value)
let request: AbortController | null = null, resolveRequest: AbortController | null = null, debounce: number | undefined, sequence = 0, disposed = false
const deadlines = new Map<AbortController, ReturnType<typeof setTimeout>>()
function endBudget(active: AbortController) { clearTimeout(deadlines.get(active)); deadlines.delete(active) }
function stop(active: AbortController | null) { if (active) { endBudget(active); active.abort() } }
function startBudget(active: AbortController) { deadlines.set(active, setTimeout(() => active.abort(new DOMException('项目读取超时，请重试', 'TimeoutError')), 10000)) }
function requestError(active: AbortController, e: unknown) {
  if (active.signal.reason instanceof DOMException && active.signal.reason.name === 'TimeoutError') error.value = '项目读取超时，请重试'
  else if (!active.signal.aborted) error.value = readableError(e)
}

async function load() {
  if (disposed || !props.active || document.hidden) return
  stop(request); const own = new AbortController(); request = own; startBudget(own)
  const active = ++sequence
  loading.value = true; error.value = ''; rows.value = []; hasMore.value = false
  try {
    const result = unwrap(await api.GET('/admin/api/v1/scopes/options', { params: { query: { page: page.value, q: query.value || undefined, enabled: props.allowInactive && inactiveOnly.value ? false : undefined } }, signal: own.signal }))
    if (active !== sequence || own.signal.aborted || disposed) return
    rows.value = result.data; hasMore.value = result.has_more
  } catch (e) { if (active === sequence && !disposed) requestError(own, e) }
  finally { endBudget(own); if (active === sequence) loading.value = false }
}
function search(value: string) {
  window.clearTimeout(debounce)
  if (disposed || !props.active || document.hidden) return
  debounce = window.setTimeout(() => { query.value = value; page.value = 1; void load() }, 250)
}
function visible(value: boolean) {
  window.clearTimeout(debounce)
  if (value) { query.value = ''; page.value = 1; void load() }
  else { sequence++; stop(request); loading.value = false }
}
function modeChanged() { sequence++; stop(request); page.value = 1; rows.value = []; hasMore.value = false; loading.value = false; error.value = '' }
function choose(value: string) {
  if (!value && props.clearable) { current.value = null; emit('update:modelValue', ''); emit('resolved', null); return }
  const row = options.value.find(option => option.project_id === value)
  if (!row || (!props.allowInactive && (!row.tenant_enabled || !row.project_enabled))) return
  current.value = row; if (row.tenant_enabled && row.project_enabled) session.rememberScope(row); emit('update:modelValue', value)
  emit('resolved', row)
}
watch(() => [props.modelValue, props.active] as const, async ([value, activeView]) => {
  stop(resolveRequest)
  if (!activeView || document.hidden || disposed) { sequence++; stop(request); window.clearTimeout(debounce); loading.value = false; return }
  if (!value) { current.value = null; emit('resolved', null); return }
  if (current.value?.project_id === value) { emit('resolved', current.value); return }
  resolveRequest = new AbortController()
  const active = resolveRequest; startBudget(active)
  try {
    const result = unwrap(await api.GET('/admin/api/v1/scopes/options', { params: { query: { project_id: value } }, signal: active.signal }))
    if (active.signal.aborted || props.modelValue !== value) return
    current.value = result.data[0] ?? null
    if (current.value?.tenant_enabled && current.value.project_enabled) session.rememberScope(current.value)
    emit('resolved', current.value)
  } catch (e) { if (resolveRequest === active && !disposed) requestError(active, e) }
  finally { endBudget(active) }
}, { immediate: true })
onBeforeUnmount(() => { disposed = true; window.clearTimeout(debounce); sequence++; stop(request); stop(resolveRequest) })
</script>

<template>
  <div class="scope-picker">
  <el-select :model-value="modelValue" :disabled="disabled" :clearable="clearable" class="full" :aria-label="label" filterable remote :remote-method="search" :loading="loading" placeholder="搜索租户、项目名称或 ID" @visible-change="visible" @update:model-value="choose">
    <template #header><p class="form-help">选择租户下的项目；可搜索并翻页。</p></template>
    <template #empty><p class="form-help">{{ loading ? '正在读取项目…' : '没有匹配的项目，可修改搜索条件。' }}</p></template>
    <el-option v-for="row in options" :key="row.project_id" :value="row.project_id" :label="row.tenant_name + ' / ' + row.project_name + ((!row.tenant_enabled || !row.project_enabled) ? '（已停用）' : '')" :disabled="!allowInactive && (!row.tenant_enabled || !row.project_enabled)"/>
    <template #footer><div class="scope-picker-pages"><el-button size="small" :disabled="page === 1 || loading" @click.stop="page--; load()">上一页</el-button><span class="muted">第 {{ page }} 页</span><el-button size="small" :disabled="!hasMore || loading" @click.stop="page++; load()">下一页</el-button></div></template>
  </el-select>
  <el-checkbox v-if="allowInactive" v-model="inactiveOnly" @change="modeChanged">查看停用项目</el-checkbox>
  <p v-if="error" role="alert" class="form-help">{{ error }}</p>
  </div>
</template>

<style scoped>
.scope-picker { min-width: 0; width: 100%; }
.scope-picker-pages { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
</style>
