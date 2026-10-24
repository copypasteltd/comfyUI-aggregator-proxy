<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { api, readableError, unwrap } from '../api'
import type { components } from '../generated/api'
import { useSession } from '../session'

type Option = components['schemas']['WorkflowSyncOptions']['data'][number]
const props = withDefaults(defineProps<{ modelValue: string[]; kind: 'release' | 'channel'; label: string; projectId: string; workflowId: string; active: boolean; disabled?: boolean }>(), { disabled: false })
const emit = defineEmits<{ 'update:modelValue': [value: string[]]; resolved: [value: Option[]]; validity: [value: boolean] }>()
const session = useSession(), rows = ref<Option[]>([]), selected = ref<Record<string, Option>>({}), query = ref(''), page = ref(1), total = ref<number | null>(null), more = ref(false)
const loading = ref(false), resolving = ref(false), error = ref(''), resolveError = ref('')
const context = computed(() => JSON.stringify([props.kind, props.projectId, props.workflowId]))
const available = computed(() => new Map([...Object.values(selected.value), ...rows.value].map(item => [item.id, item])))
function usable(option: Option | undefined) { return !!option && (option.kind === 'channel' || option.validated) }
const valid = computed(() => !!props.modelValue.length && props.modelValue.length <= (props.kind === 'release' ? 1 : 100) && !resolving.value && !resolveError.value && props.modelValue.every(id => usable(selected.value[id])))
const options = computed(() => [...new Set([...props.modelValue, ...rows.value.map(item => item.id)])].map(id => ({ id, option: available.value.get(id) })))
function label(option: Option | undefined, id: string) {
  if (!option) return id + (resolving.value ? ' · 核对中' : ' · 不可用，请移除')
  return option.kind === 'release' ? 'v' + option.version + ' · ' + option.id + (!option.validated ? ' · 未验证' : '') : option.name + (!option.enabled ? ' · 已停用' : option.draining ? ' · 排空中' : '')
}
type Read = 'browse' | 'resolve'
const reads = new Map<Read, { controller: AbortController; timer: ReturnType<typeof setTimeout> }>()
let disposed = false, debounce: ReturnType<typeof setTimeout> | undefined
function active() { return !disposed && props.active && !props.disabled && !document.hidden && !!session.info && !session.ending && session.allows('workflows:publish') }
function stop(kind: Read) { const read = reads.get(kind); if (read) { clearTimeout(read.timer); read.controller.abort(); reads.delete(kind) } if (kind === 'browse') loading.value = false; else resolving.value = false }
function stopAll() { clearTimeout(debounce); stop('browse'); stop('resolve') }
function begin(kind: Read) { stop(kind); const controller = new AbortController(); reads.set(kind, { controller, timer: setTimeout(() => controller.abort(new DOMException('目录读取超时，请重试', 'TimeoutError')), 10000) }); return controller }
function owns(kind: Read, own: AbortController, key: string) { return active() && reads.get(kind)?.controller === own && context.value === key }
function finish(kind: Read, own: AbortController) { if (reads.get(kind)?.controller === own) { clearTimeout(reads.get(kind)!.timer); reads.delete(kind); if (kind === 'browse') loading.value = false; else resolving.value = false } }
function failure(own: AbortController, value: unknown) { return own.signal.reason instanceof DOMException && own.signal.reason.name === 'TimeoutError' ? '目录读取超时，请重试' : readableError(value) }
function base() { return { kind: props.kind, project_id: props.projectId, workflow_id: props.workflowId } }
async function load() {
  if (!active() || !props.projectId || !props.workflowId) return
  const own = begin('browse'), key = context.value; loading.value = true; error.value = ''; rows.value = []; more.value = false
  try {
    const result = unwrap(await api.GET('/admin/api/v1/workflow-sync-options', { signal: own.signal, params: { query: { ...base(), page: page.value, q: query.value || undefined } } }))
    if (!owns('browse', own, key) || own.signal.aborted) return
    rows.value = result.data; total.value = result.total; more.value = result.has_more
  } catch (e) { if (owns('browse', own, key)) error.value = failure(own, e) }
  finally { finish('browse', own) }
}
async function resolve() {
  stop('resolve'); selected.value = {}; emit('resolved', [])
  if (!active() || !props.projectId || !props.workflowId) return
  resolveError.value = ''
  const ids = [...props.modelValue]
  if (!ids.length) return
  if (ids.length > (props.kind === 'release' ? 1 : 100)) { resolveError.value = '同步目标最多 100 个，版本只能选择一个'; return }
  const own = begin('resolve'), key = context.value; resolving.value = true
  try {
    const batches = [ids.slice(0, 50), ids.slice(50)].filter(batch => batch.length)
    const responses = await Promise.all(batches.map(batch => api.GET('/admin/api/v1/workflow-sync-options', { signal: own.signal, params: { query: { ...base(), ids: batch } } }).then(unwrap)))
    if (!owns('resolve', own, key) || own.signal.aborted || JSON.stringify(ids) !== JSON.stringify(props.modelValue)) return
    selected.value = Object.fromEntries(responses.flatMap(response => response.data).map(item => [item.id, item]))
    emit('resolved', ids.map(id => selected.value[id]).filter((item): item is Option => !!item))
  } catch (e) { if (owns('resolve', own, key)) resolveError.value = failure(own, e) }
  finally { finish('resolve', own) }
}
function choose(value: string | string[]) { emit('update:modelValue', Array.isArray(value) ? value : value ? [value] : []) }
function search(value: string) { clearTimeout(debounce); if (active()) debounce = setTimeout(() => { query.value = value.trim(); page.value = 1; void load() }, 250) }
function opened(value: boolean) { clearTimeout(debounce); if (value) { query.value = ''; page.value = 1; void load() } else stop('browse') }
function turn(delta: number) { if (loading.value || page.value + delta < 1 || page.value + delta > 1000) return; page.value += delta; void load() }
watch(context, () => { stopAll(); rows.value = []; selected.value = {}; page.value = 1; total.value = null; more.value = false; error.value = ''; resolveError.value = ''; void resolve() })
watch(() => [JSON.stringify(props.modelValue), props.active, props.disabled] as const, () => { if (!active()) stopAll(); else void resolve() }, { immediate: true })
watch(() => [valid.value, props.active, props.disabled] as const, () => emit('validity', props.active && !props.disabled && valid.value), { immediate: true })
watch(() => [session.info?.csrf_token, session.ending] as const, stopAll)
onBeforeUnmount(() => { disposed = true; stopAll(); selected.value = {}; rows.value = [] })
</script>
<template>
  <div class="workflow-sync-picker">
    <el-select v-if="props.active" :model-value="kind === 'channel' ? modelValue : modelValue[0] || ''" :aria-label="props.label" :disabled="disabled || !projectId || !workflowId" :multiple="kind === 'channel'" :multiple-limit="100" filterable remote :remote-method="search" :loading="loading || resolving" clearable class="full" placeholder="搜索名称或版本，可翻页" @visible-change="opened" @update:model-value="choose">
      <template #header><p class="form-help">每页最多 50 项，已选项会保留显示。同步不会启用停用渠道。</p></template>
      <el-option v-for="item in options" :key="item.id + ':' + label(item.option, item.id)" :value="item.id" :label="label(item.option, item.id)" :disabled="!usable(item.option) && !modelValue.includes(item.id)"/>
      <template #empty><p class="form-help">{{ loading ? '正在读取目录…' : error ? '目录读取失败，请重试。' : '没有匹配项，可修改搜索。' }}</p></template>
      <template #footer><div class="picker-pages"><el-button size="small" :disabled="page === 1 || loading" @click.stop="turn(-1)">上一页</el-button><span>第 {{ page }} 页<span v-if="total != null"> · {{ total }} 项</span></span><el-button size="small" :disabled="!more || page >= 1000 || loading" @click.stop="turn(1)">下一页</el-button></div><el-button v-if="error" size="small" @click.stop="load">重试读取</el-button></template>
    </el-select>
    <p v-if="error || resolveError" role="alert" class="form-help">{{ error || resolveError }}<el-button v-if="resolveError && !disabled" text @click="resolve">重新核对</el-button></p>
    <p v-else-if="modelValue.length && !valid && !resolving" role="alert" class="form-help">部分已选项不可用，请核对并移除后重新选择。</p>
  </div>
</template>
<style scoped>
.workflow-sync-picker { width: 100%; min-width: 0; }
.picker-pages { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
</style>
