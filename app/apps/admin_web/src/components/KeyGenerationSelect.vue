<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { api, readableError, unwrap } from '../api'
import type { components } from '../generated/api'
import { useSession } from '../session'

type Option = components['schemas']['KeyGenerationOptions']['data'][number]
type Kind = components['schemas']['KeyGenerationOptions']['kind']
type Read = 'browse' | 'resolve'
const props = withDefaults(defineProps<{ modelValue: string[]; kind: Kind; label: string; projectId: string; active: boolean; disabled?: boolean }>(), { disabled: false })
const emit = defineEmits<{ 'update:modelValue': [value: string[]]; validity: [value: boolean] }>()
const session = useSession(), rows = ref<Option[]>([]), selected = ref<Record<string, Option>>({}), unresolved = ref<string[]>([]), error = ref(''), resolveError = ref('')
const query = ref(''), page = ref(1), total = ref<number | null>(null), more = ref(false), loading = ref(false), resolving = ref(false)
const context = computed(() => JSON.stringify([props.kind, props.projectId]))
const available = computed(() => new Map([...Object.values(selected.value), ...rows.value].filter(item => !unresolved.value.includes(item.value)).map(item => [item.value, item])))
const valid = computed(() => props.modelValue.length <= 1000 && !resolving.value && !resolveError.value && props.modelValue.every(value => available.value.has(value)))
const options = computed(() => [...new Set([...props.modelValue, ...rows.value.map(item => item.value)])].map(value => ({ value, option: available.value.get(value) })))
const taskNames: Record<string, string> = { text_to_image: '文生图', image_to_image: '图生图', references_to_image: '多参考图生图', text_to_video: '文生视频', references_to_video: '多参考图生视频', text_to_audio: 'Workflow 音频' }
function label(option: Option | undefined, value: string) {
  if (!option) return value + (resolving.value ? ' · 核对中' : ' · 不可用，请移除')
  if (option.kind === 'models') return [option.value, taskNames[option.task_type], option.published ? '' : '未发布'].filter(Boolean).join(' · ')
  if (option.kind === 'workflow_releases') return [option.name, 'v' + option.version, taskNames[option.task_type], option.validated ? '' : '未验证', option.archived ? '已归档' : '', option.value].filter(Boolean).join(' · ')
  return [option.name, option.enabled ? '' : '已停用', option.draining ? '排空中' : '', option.archived ? '已归档' : '', option.value].filter(Boolean).join(' · ')
}

const reads = new Map<Read, { request: AbortController; timer: ReturnType<typeof setTimeout> }>()
let disposed = false, debounce: ReturnType<typeof setTimeout> | undefined
function active() { return !disposed && props.active && !props.disabled && !document.hidden && !!session.info && !session.ending }
function stop(kind: Read) { const read = reads.get(kind); if (read) { clearTimeout(read.timer); read.request.abort(); reads.delete(kind) } if (kind === 'browse') loading.value = false; else resolving.value = false }
function stopAll() { clearTimeout(debounce); stop('browse'); stop('resolve') }
function begin(kind: Read) { stop(kind); const request = new AbortController(); reads.set(kind, { request, timer: setTimeout(() => request.abort(new DOMException('目录读取超时，请重试', 'TimeoutError')), 10000) }); return request }
function owns(kind: Read, request: AbortController, key: string) { return active() && reads.get(kind)?.request === request && context.value === key }
function finish(kind: Read, request: AbortController) { const read = reads.get(kind); if (read?.request === request) { clearTimeout(read.timer); reads.delete(kind); if (kind === 'browse') loading.value = false; else resolving.value = false } }
function failure(request: AbortController, value: unknown) { return request.signal.reason instanceof DOMException && request.signal.reason.name === 'TimeoutError' ? '目录读取超时，请重试' : readableError(value) }
async function load() {
  if (!active() || !props.projectId) return
  const own = begin('browse'), key = context.value; loading.value = true; error.value = ''; rows.value = []; more.value = false; total.value = null
  try {
    const result = unwrap(await api.POST('/admin/api/v1/key-generation-options/query', { signal: own.signal, body: { kind: props.kind, mode: 'browse', project_id: props.projectId, page: page.value, q: query.value, ids: [] } }))
    if (!owns('browse', own, key) || own.signal.aborted) return
    rows.value = result.data; more.value = result.has_more; total.value = result.total
  } catch (e) { if (owns('browse', own, key)) error.value = failure(own, e) }
  finally { finish('browse', own) }
}
async function resolve() {
  stop('resolve'); resolveError.value = ''
  if (!active() || !props.projectId) return
  if (!props.modelValue.length) { selected.value = {}; unresolved.value = []; return }
  if (props.modelValue.length > 1000) { resolveError.value = '每类最多选择 1000 项，请先移除多余选择。'; return }
  const own = begin('resolve'), key = context.value, ids = [...props.modelValue]; resolving.value = true
  try {
    const result = unwrap(await api.POST('/admin/api/v1/key-generation-options/query', { signal: own.signal, body: { kind: props.kind, mode: 'resolve', project_id: props.projectId, page: 1, q: '', ids } }))
    if (!owns('resolve', own, key) || own.signal.aborted || JSON.stringify(ids) !== JSON.stringify(props.modelValue)) return
    selected.value = Object.fromEntries(result.data.map(item => [item.value, item])); unresolved.value = result.unresolved_ids
  } catch (e) { if (owns('resolve', own, key)) resolveError.value = failure(own, e) }
  finally { finish('resolve', own) }
}
function choose(values: string[]) {
  if (!active()) return
  selected.value = Object.fromEntries(values.map(value => available.value.get(value)).filter((item): item is Option => !!item).map(item => [item.value, item])); unresolved.value = unresolved.value.filter(value => values.includes(value))
  emit('update:modelValue', values)
}
function search(value: string) { clearTimeout(debounce); if (!active()) return; debounce = setTimeout(() => { query.value = value.trim(); page.value = 1; void load() }, 250) }
function visible(value: boolean) { clearTimeout(debounce); if (value) { query.value = ''; page.value = 1; void load() } else stop('browse') }
function turn(delta: number) { const next = page.value + delta; if (loading.value || next < 1 || next > 1000) return; page.value = next; void load() }
watch(() => [valid.value, props.active] as const, ([value, activeView]) => emit('validity', activeView && value), { immediate: true })
watch(context, () => { stopAll(); rows.value = []; selected.value = {}; unresolved.value = []; page.value = 1; total.value = null; more.value = false; error.value = ''; resolveError.value = ''; void resolve() })
watch(() => [JSON.stringify(props.modelValue), props.active, props.disabled] as const, () => { if (!active()) stopAll(); else void resolve() }, { immediate: true })
watch(() => [session.info?.csrf_token, session.ending] as const, ([token, ending], [old]) => { if (!token || ending || token !== old) { stopAll(); rows.value = []; selected.value = {}; unresolved.value = []; error.value = ''; resolveError.value = '' } })
onBeforeUnmount(() => { disposed = true; stopAll() })
</script>

<template>
  <div class="generation-picker">
    <el-select v-if="props.active" :model-value="modelValue" :aria-label="props.label" :disabled="disabled || !projectId" multiple :multiple-limit="1000" collapse-tags :max-collapse-tags="3" filterable remote :remote-method="search" :loading="loading || resolving" clearable class="full" placeholder="搜索名称、别名或版本，可翻页" @visible-change="visible" @update:model-value="choose">
      <template #header><p class="form-help">每页最多 50 项，已选项保留显示。历史状态可作为限制保存，实际调用仍需满足运行条件。</p></template>
      <el-option v-for="item in options" :key="item.value + ':' + label(item.option, item.value)" :value="item.value" :label="label(item.option, item.value)" :disabled="!item.option && !modelValue.includes(item.value)"/>
      <template #empty><p class="form-help">{{ loading ? '正在读取资源…' : error ? '目录读取失败，请重试。' : '没有匹配资源，可修改搜索条件。' }}</p></template>
      <template #footer><div class="generation-picker-pages"><el-button size="small" :disabled="page === 1 || loading" @click.stop="turn(-1)">上一页</el-button><span class="muted">第 {{ page }} 页<span v-if="total != null"> · {{ total }} 项</span></span><el-button size="small" :disabled="!more || page >= 1000 || loading" @click.stop="turn(1)">下一页</el-button></div><el-button v-if="error" size="small" @click.stop="load">重试读取</el-button></template>
    </el-select>
    <p v-else class="form-help">页面暂停，已选择 {{ modelValue.length }} 项。</p>
    <p v-if="error || resolveError" role="alert" class="form-help">{{ error || resolveError }}<el-button v-if="resolveError && !disabled" text size="small" @click="resolve">重新核对</el-button></p>
    <p v-else-if="modelValue.length && !valid && !resolving" role="alert" class="form-help">部分已选项不可用，请明确移除后重新选择。</p>
    <p v-else-if="!modelValue.length" class="form-help">当前指定列表为空，不授权这类生成资源。</p>
  </div>
</template>

<style scoped>
.generation-picker { width: 100%; min-width: 0; margin-top: 8px; }
.generation-picker-pages { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
</style>
