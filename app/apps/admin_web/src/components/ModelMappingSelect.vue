<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { api, readableError, unwrap } from '../api'
import type { components, paths } from '../generated/api'
import { useSession } from '../session'

type Option = components['schemas']['ModelMappingOptions']['data'][number]
type Kind = components['schemas']['ModelMappingOptions']['kind']
type Query = NonNullable<paths['/admin/api/v1/model-mapping-options']['get']['parameters']['query']>
const props = withDefaults(defineProps<{ modelValue: string[]; kind: Kind; label: string; projectId: string; workflowId?: string; releaseId?: string; modelId?: string; taskType?: Query['task_type']; active: boolean; disabled?: boolean; multiple?: boolean; runnableOnly?: boolean; currentPreset?: Extract<Option, { kind: 'preset' }> }>(), { disabled: false, multiple: false, runnableOnly: false })
const emit = defineEmits<{ 'update:modelValue': [value: string[]]; resolved: [value: Option[]]; validity: [value: boolean] }>()
const session = useSession(), rows = ref<Option[]>([]), selected = ref<Record<string, Option>>({}), error = ref(''), resolveError = ref('')
const query = ref(''), page = ref(1), total = ref<number | null>(null), more = ref(false), loading = ref(false), resolving = ref(false), opened = ref(false)
const context = computed(() => JSON.stringify([props.kind, props.projectId, props.workflowId, props.releaseId, props.modelId, props.taskType]))
const current = computed(() => props.currentPreset?.release_id === props.releaseId ? props.currentPreset : undefined)
const available = computed(() => {
  const result = new Map<string, Option>()
  for (const item of [...Object.values(selected.value), ...rows.value]) result.set(item.id, item)
  if (current.value) result.set(current.value.id, current.value)
  return result
})
function usable(option: Option | undefined) {
  if (!option) return false
  if (option.kind === 'workflow') return !option.archived
  if (option.kind === 'release') return option.validated
  if (option.kind === 'preset') return option.usable
  return !props.runnableOnly || (option.enabled && !option.draining)
}
const valid = computed(() => !!props.modelValue.length && !resolving.value && !resolveError.value && props.modelValue.every(id => usable(available.value.get(id))))
watch(() => [valid.value, props.active] as const, ([value, active]) => emit('validity', active && value), { immediate: true })
const options = computed(() => [...new Set([...props.modelValue, ...rows.value.map(item => item.id), ...(current.value ? [current.value.id] : [])])].map(id => ({ id, option: available.value.get(id) })))
const quality = { draft: '草稿', standard: '标准', high: '高质量' }
function optionLabel(option: Option | undefined, id: string) {
  if (!option) return id + (resolving.value ? ' · 核对中' : ' · 不可用，请移除')
  if (option.kind === 'workflow') return option.name + (option.archived ? ' · 已归档' : '')
  if (option.kind === 'channel') return option.name + (!option.enabled ? ' · 已停用' : option.draining ? ' · 排空中' : '')
  if (option.kind === 'release') return 'v' + option.version + ' · ' + option.id + (!option.validated ? ' · 未验证' : '')
  return [option.id === 'current' ? '当前冻结规格' : quality[option.defaults.quality], option.defaults.size, option.defaults.seconds != null ? option.defaults.seconds + ' 秒' : '', option.audio ? `${option.audio.format.toUpperCase()} · ${option.audio.sample_rate} Hz · ${option.audio.channels} 声道` : '', option.usable ? '' : '规格重复，不可选择'].filter(Boolean).join(' · ')
}
type Read = 'browse' | 'resolve'
const reads = new Map<Read, { request: AbortController; timer: ReturnType<typeof setTimeout> }>()
let disposed = false, debounce: ReturnType<typeof setTimeout> | undefined
function active() { return !disposed && props.active && !props.disabled && !document.hidden && !!session.info && !session.ending }
function stop(kind: Read) { const read = reads.get(kind); if (read) { clearTimeout(read.timer); read.request.abort(); reads.delete(kind) } if (kind === 'browse') loading.value = false; else resolving.value = false }
function stopAll() { clearTimeout(debounce); stop('browse'); stop('resolve'); opened.value = false }
function begin(kind: Read) { stop(kind); const request = new AbortController(); reads.set(kind, { request, timer: setTimeout(() => request.abort(new DOMException('目录读取超时，请重试', 'TimeoutError')), 10000) }); return request }
function owns(kind: Read, request: AbortController, key: string) { return active() && reads.get(kind)?.request === request && context.value === key }
function finish(kind: Read, request: AbortController) { const read = reads.get(kind); if (read?.request === request) { clearTimeout(read.timer); reads.delete(kind); if (kind === 'browse') loading.value = false; else resolving.value = false } }
function failure(request: AbortController, value: unknown) { return request.signal.reason instanceof DOMException && request.signal.reason.name === 'TimeoutError' ? '目录读取超时，请重试' : readableError(value) }
function base(): Query { return { kind: props.kind, project_id: props.projectId, workflow_id: props.kind === 'release' ? props.workflowId || undefined : undefined, release_id: props.kind === 'preset' ? props.releaseId || undefined : undefined, model_id: props.kind === 'channel' ? props.modelId || undefined : undefined, task_type: ['workflow', 'release'].includes(props.kind) ? props.taskType || undefined : undefined } }
function canBrowse() { return active() && !!props.projectId && (props.kind !== 'release' || !!props.workflowId) && (props.kind !== 'preset' || !!props.releaseId) }
async function load() {
  if (!canBrowse()) return
  const own = begin('browse'), key = context.value; loading.value = true; error.value = ''
  try {
    const result = unwrap(await api.GET('/admin/api/v1/model-mapping-options', { signal: own.signal, params: { query: { ...base(), page: page.value, q: props.kind === 'preset' ? undefined : query.value || undefined } } }))
    if (!owns('browse', own, key) || own.signal.aborted) return
    rows.value = result.data; more.value = result.has_more; total.value = result.total
  } catch (e) { if (owns('browse', own, key)) error.value = failure(own, e) }
  finally { finish('browse', own) }
}
async function resolve() {
  stop('resolve')
  if (!active() || !props.projectId) return
  resolveError.value = ''
  if (!props.modelValue.length) { selected.value = {}; emit('resolved', []); return }
  if (props.kind === 'preset') { emit('resolved', props.modelValue.map(id => available.value.get(id)).filter((item): item is Option => !!item)); return }
  const own = begin('resolve'), key = context.value, ids = [...props.modelValue]; resolving.value = true
  try {
    const batches = []
    for (let i = 0; i < ids.length; i += 50) batches.push(ids.slice(i, i + 50))
    const responses = await Promise.all(batches.map(batch => api.GET('/admin/api/v1/model-mapping-options', { signal: own.signal, params: { query: { ...base(), page: 1, ids: batch } } }).then(unwrap)))
    if (!owns('resolve', own, key) || own.signal.aborted || JSON.stringify(ids) !== JSON.stringify(props.modelValue)) return
    selected.value = Object.fromEntries(responses.flatMap(response => response.data).map(item => [item.id, item]))
    emit('resolved', ids.map(id => selected.value[id]).filter((item): item is Option => !!item))
  } catch (e) { if (owns('resolve', own, key)) resolveError.value = failure(own, e) }
  finally { finish('resolve', own) }
}
function choose(value: string | string[]) {
  const ids = Array.isArray(value) ? value : value ? [value] : []
  const kept = ids.map(id => available.value.get(id)).filter((item): item is Option => !!item)
  selected.value = Object.fromEntries(kept.map(item => [item.id, item])); emit('update:modelValue', ids); emit('resolved', kept)
}
function search(value: string) { clearTimeout(debounce); if (!active()) return; debounce = setTimeout(() => { query.value = value.trim(); page.value = 1; void load() }, 250) }
function visible(value: boolean) { clearTimeout(debounce); opened.value = value; if (value) { query.value = ''; page.value = 1; void load() } else stop('browse') }
function turn(delta: number) { const next = page.value + delta; if (loading.value || next < 1 || next > 1000) return; page.value = next; void load() }
watch(context, () => { stopAll(); rows.value = []; selected.value = {}; page.value = 1; total.value = null; more.value = false; error.value = ''; resolveError.value = ''; void resolve() })
watch(() => [JSON.stringify(props.modelValue), props.active, props.disabled] as const, () => { if (!active()) stopAll(); else void resolve() }, { immediate: true })
watch(() => [session.info?.csrf_token, session.ending] as const, ([token, ending], [old]) => { if (!token || ending || token !== old) { stopAll(); rows.value = []; selected.value = {}; error.value = ''; resolveError.value = '' } })
onBeforeUnmount(() => { disposed = true; stopAll() })
</script>

<template>
  <div class="mapping-picker">
    <el-select v-if="props.active" :model-value="multiple ? modelValue : modelValue[0] || ''" :aria-label="props.label" :disabled="disabled || !projectId || (kind === 'release' && !workflowId) || (kind === 'preset' && !releaseId)" :multiple="multiple" :multiple-limit="multiple ? 100 : 0" :filterable="kind !== 'preset'" :remote="kind !== 'preset'" :remote-method="search" :loading="loading || resolving" clearable class="full" :placeholder="kind === 'preset' ? '选择冻结版本的规格' : '搜索名称或版本，可翻页'" @visible-change="visible" @update:model-value="choose">
      <template #header><p class="form-help">{{ kind === 'preset' ? '仅列出当前冻结版本的规格，不能手填替代。' : '每页最多 50 项，已选项会保留显示。' }}</p></template>
      <el-option v-for="item in options" :key="item.id + ':' + optionLabel(item.option, item.id)" :value="item.id" :label="optionLabel(item.option, item.id)" :disabled="!usable(item.option)"/>
      <template #empty><p class="form-help">{{ loading ? '正在读取目录…' : error ? '目录读取失败，请重试。' : '没有匹配项，可修改搜索或先发布 Workflow。' }}</p></template>
      <template #footer><div class="mapping-picker-pages"><el-button size="small" :disabled="page === 1 || loading" @click.stop="turn(-1)">上一页</el-button><span class="muted">第 {{ page }} 页<span v-if="total != null"> · {{ total }} 项</span></span><el-button size="small" :disabled="!more || page >= 1000 || loading" @click.stop="turn(1)">下一页</el-button></div><el-button v-if="error" size="small" @click.stop="load">重试读取</el-button></template>
    </el-select>
    <p v-else class="form-help">页面暂停，已选择 {{ modelValue.length }} 项。</p>
    <p v-if="error || resolveError" role="alert" class="form-help">{{ error || resolveError }}<el-button v-if="resolveError && !disabled" text size="small" @click="resolve">重新核对</el-button></p>
    <p v-else-if="modelValue.length && !valid && !resolving" role="alert" class="form-help">部分已选项不可用，请核对并移除后重新选择。</p>
  </div>
</template>

<style scoped>
.mapping-picker { width: 100%; min-width: 0; }
.mapping-picker-pages { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
</style>
