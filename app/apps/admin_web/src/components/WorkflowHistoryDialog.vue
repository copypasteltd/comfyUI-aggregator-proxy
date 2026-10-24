<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { api, readableError, time, unwrap } from '../api'
import type { components } from '../generated/api'
import { exactObject, formatExactDraft, isRecord } from '../workflowJson'

type History = components['schemas']['WorkflowHistoryPage']
type Diff = components['schemas']['WorkflowDiff']
type Release = History['data'][number]
const props = defineProps<{ modelValue: boolean; target: { id: string; name: string } | null; canRestore?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [boolean]; restore: [components['schemas']['WorkflowDraftRestore']] }>()
const opened = computed({ get: () => props.modelValue, set: value => emit('update:modelValue', value) })
const history = ref<History | null>(null), diff = ref<Diff | null>(null)
const base = ref<Release | null>(null), targetRelease = ref<Release | null>(null), mode = ref<'draft' | 'release'>('draft')
const errors = reactive({ history: '', diff: '', export: '' }), busy = reactive({ history: false, diff: false, export: false })
const notice = ref(''), stale = ref(false), restoration = ref<{ source: Release; revision: number } | null>(null)
const restoreRevision = computed(() => history.value?.workflow_revision)
const canRestoreSource = computed(() => !!props.canRestore && !busy.history && Number.isSafeInteger(restoreRevision.value) && (restoreRevision.value ?? 0) >= 1 && (restoreRevision.value ?? Infinity) <= 9007199254740990)
function chooseRestoration(source: Release) { if (active() && canRestoreSource.value && source.validated) restoration.value = { source: { ...source }, revision: restoreRevision.value! } }
function confirmRestoration() { const choice = restoration.value; if (!active() || !props.canRestore || !choice) return; emit('restore', { source_release_id: choice.source.id, source_digest: choice.source.digest, expected_revision: choice.revision }) }
type Read = keyof typeof busy
const reads = new Map<Read, { controller: AbortController; timer: ReturnType<typeof setTimeout> }>()
const urls = new Set<string>()
let disposed = false
function active() { return !disposed && !document.hidden && props.modelValue && !!props.target }
function stop(kind: Read) { const own = reads.get(kind); if (own) { clearTimeout(own.timer); own.controller.abort(); reads.delete(kind) } busy[kind] = false }
function begin(kind: Read) { stop(kind); const controller = new AbortController(); reads.set(kind, { controller, timer: setTimeout(() => controller.abort(new DOMException('读取超时，请重试', 'TimeoutError')), 10000) }); busy[kind] = true; errors[kind] = ''; return controller }
function owns(kind: Read, own: AbortController) { return active() && reads.get(kind)?.controller === own }
function finish(kind: Read, own: AbortController) { if (reads.get(kind)?.controller === own) { clearTimeout(reads.get(kind)!.timer); reads.delete(kind); busy[kind] = false } }
function failure(own: AbortController, e: unknown) { return own.signal.reason instanceof DOMException && own.signal.reason.name === 'TimeoutError' ? '读取超时，请重试' : readableError(e) }
function clear() { restoration.value = null; for (const kind of ['history', 'diff', 'export'] as Read[]) { stop(kind); errors[kind] = '' } for (const url of urls) URL.revokeObjectURL(url); urls.clear(); history.value = null; diff.value = null; base.value = null; targetRelease.value = null; mode.value = 'draft'; notice.value = ''; stale.value = false }
async function loadHistory(page = 1) {
  if (!active() || !props.target) return
  restoration.value = null
  const id = props.target.id, own = begin('history')
  try { const result = unwrap(await api.GET('/admin/api/v1/workflows/{identifier}/releases', { signal: own.signal, params: { path: { identifier: id }, query: { page } } })); if (owns('history', own) && !own.signal.aborted) history.value = result }
  catch (e) { if (owns('history', own)) errors.history = failure(own, e) } finally { finish('history', own) }
}
function resetDiff() { stop('diff'); diff.value = null; errors.diff = ''; notice.value = ''; stale.value = false }
function chooseBase(row: Release) { base.value = row; resetDiff() }
function chooseTarget(row: Release) { targetRelease.value = row; mode.value = 'release'; resetDiff() }
const canCompare = computed(() => !!base.value && (mode.value === 'draft' || !!targetRelease.value))
async function compare(page = 1) {
  if (!active() || !props.target || !base.value || !canCompare.value) return
  const own = begin('diff'), id = props.target.id, previous = diff.value
  try {
    const result = unwrap(await api.GET('/admin/api/v1/workflows/{identifier}/release-diff', { signal: own.signal, params: { path: { identifier: id }, query: { base_release_id: base.value.id, target_release_id: mode.value === 'release' ? targetRelease.value!.id : undefined, page } } }))
    if (!owns('diff', own) || own.signal.aborted) return
    if (page > 1 && result.target.kind === 'draft' && previous?.target.kind === 'draft' && result.target.revision !== previous.target.revision) { diff.value = null; notice.value = '已保存草稿发生变化，已重新比较第一页。'; finish('diff', own); await compare(1); return }
    diff.value = result; stale.value = false
  } catch (e) { if (owns('diff', own)) { errors.diff = failure(own, e); stale.value = !!diff.value } } finally { finish('diff', own) }
}
async function exportRelease(row: Release) {
  if (!active()) return
  const own = begin('export')
  try {
    const result = exactObject(unwrap(await api.GET('/admin/api/v1/workflow-releases/{identifier}', { parseAs: 'text', signal: own.signal, params: { path: { identifier: row.id } } })), true)
    if (!isRecord(result.manifest)) throw new Error('冻结发布包响应格式无效')
    if (!owns('export', own) || own.signal.aborted) return
    const blob = new Blob([formatExactDraft(result.manifest)], { type: 'application/json' })
    if (blob.size > 10 * 1024 * 1024) throw new Error('发布包超过 10 MiB，无法在此导出')
    const url = URL.createObjectURL(blob); urls.add(url)
    try { const anchor = document.createElement('a'); anchor.href = url; anchor.download = `${row.id}-v${row.version}.json`; document.body.append(anchor); try { anchor.click() } finally { anchor.remove() } } finally { URL.revokeObjectURL(url); urls.delete(url) }
  } catch (e) { if (owns('export', own)) errors.export = failure(own, e) } finally { finish('export', own) }
}
const changes = { added: '新增', removed: '移除', changed: '修改' }
watch(mode, resetDiff)
watch(() => props.canRestore, value => { if (!value) restoration.value = null })
watch(() => [props.modelValue, props.target?.id] as const, () => { clear(); if (active()) void loadHistory() }, { immediate: true })
onBeforeUnmount(() => { disposed = true; clear() })
</script>
<template>
  <el-dialog v-model="opened" title="Workflow 发布历史" width="min(1120px, 96vw)" class="workflow-history-dialog" :close-on-click-modal="false" @closed="clear">
    <p class="history-title">{{ target?.name }} <code>{{ target?.id }}</code></p>
    <p class="form-help">选择冻结版本作为基准，与已保存草稿或另一冻结版本比较。</p>
    <el-alert v-if="errors.history" :title="errors.history + (history ? '。当前保留上次成功读取的历史。' : '。发布历史尚未读取成功。')" type="error" :closable="false"/>
    <div class="history-toolbar"><span v-if="history">共 {{ history.total }} 个冻结版本 · {{ time(history.snapshot_at) }}</span><el-button size="small" :loading="busy.history" @click="loadHistory(history?.page ?? 1)">刷新历史</el-button></div>
    <el-table :data="history?.data ?? []" :aria-busy="busy.history" empty-text="暂无冻结版本" max-height="330" class="history-table">
      <el-table-column label="版本" width="80"><template #default="{ row }">v{{ row.version }}</template></el-table-column>
      <el-table-column label="ID / 摘要" min-width="255"><template #default="{ row }"><code>{{ row.id }}</code><br/><code class="muted">{{ row.digest }}</code></template></el-table-column>
      <el-table-column label="结构校验" width="105"><template #default="{ row }"><el-tag :type="row.validated ? 'success' : 'warning'">{{ row.validated ? '通过' : '未通过' }}</el-tag></template></el-table-column>
      <el-table-column label="创建时间" width="170"><template #default="{ row }">{{ time(row.created_at) }}</template></el-table-column>
      <el-table-column label="选择 / 导出" width="360"><template #default="{ row }"><el-button text size="small" @click="chooseBase(row)">作为基准</el-button><el-button text size="small" @click="chooseTarget(row)">作为目标</el-button><el-button text size="small" :disabled="busy.export" @click="exportRelease(row)">导出原包</el-button><el-button v-if="canRestore" text size="small" :disabled="!canRestoreSource || !row.validated" @click="chooseRestoration(row)">恢复为草稿</el-button></template></el-table-column>
    </el-table>
    <div class="pagination history-pages"><el-button size="small" :disabled="!history || history.page === 1 || busy.history" @click="loadHistory(history!.page - 1)">历史上一页</el-button><span>第 {{ history?.page ?? 1 }} 页</span><el-button size="small" :disabled="!history?.has_more || history.page >= 1000 || busy.history" @click="loadHistory(history!.page + 1)">历史下一页</el-button></div>
    <section v-if="restoration" class="restoration-confirmation" aria-label="确认恢复历史草稿">
      <strong>冻结版本 v{{ restoration.source.version }} → 草稿 revision {{ restoration.revision }}</strong>
      <p><code>{{ restoration.source.id }}</code><br/><code>{{ restoration.source.digest }}</code></p>
      <p>确认后替换整个已保存草稿，并增加一次 revision。后续仍需单独冻结、验证和发布。</p>
      <div class="toolbar"><el-button type="primary" :disabled="!canRestore" @click="confirmRestoration">确认恢复草稿</el-button><el-button @click="restoration = null">取消恢复</el-button></div>
    </section>
    <el-alert v-if="errors.export" :title="errors.export" type="error" :closable="false"/>
    <section class="comparison-selection">
      <div><span class="muted">基准冻结版本</span><p data-testid="history-base">{{ base ? `v${base.version} · ${base.id}` : '请从历史中选择基准' }}</p><el-button v-if="base" size="small" :disabled="busy.export" @click="exportRelease(base)">导出基准原包</el-button></div>
      <div><el-radio-group v-model="mode" aria-label="比较目标"><el-radio value="draft">已保存草稿</el-radio><el-radio value="release">冻结版本</el-radio></el-radio-group><p data-testid="history-target">{{ mode === 'draft' ? '与服务器当前已保存的草稿比较' : targetRelease ? `v${targetRelease.version} · ${targetRelease.id}` : '请从历史中选择目标' }}</p><el-button v-if="mode === 'release' && targetRelease" size="small" :disabled="busy.export" @click="exportRelease(targetRelease)">导出目标原包</el-button></div>
    </section>
    <div class="history-toolbar"><el-button type="primary" :disabled="!canCompare" :loading="busy.diff" @click="compare()">比较版本</el-button><span v-if="busy.diff" role="status">正在读取差异…</span></div>
    <el-alert v-if="notice" :title="notice" type="info" :closable="false"/>
    <el-alert v-if="errors.diff" :title="errors.diff + (stale ? '。当前显示上次成功比较的结果。' : '。尚未完成比较。')" type="error" :closable="false"/>
    <template v-if="diff">
      <p class="diff-summary" data-testid="history-summary">共 {{ diff.total }} 项差异：新增 {{ diff.counts.added }} / 移除 {{ diff.counts.removed }} / 修改 {{ diff.counts.changed }} · {{ diff.target.kind === 'draft' ? `草稿 revision ${diff.target.revision}` : `目标 v${diff.target.version}` }} · {{ time(diff.snapshot_at) }}</p>
      <el-table :data="diff.data" empty-text="无差异" class="diff-table" :class="{ stale }" max-height="440">
        <el-table-column label="路径 / 类型" min-width="230"><template #default="{ row }"><el-tag size="small">{{ changes[row.change as keyof typeof changes] }}</el-tag><pre>{{ row.path }}</pre><small v-if="row.path_truncated">路径已截断 · SHA-256 {{ row.path_digest }}</small><small>{{ row.before_type ?? '不存在' }} → {{ row.after_type ?? '不存在' }}</small></template></el-table-column>
        <el-table-column label="基准值" min-width="255"><template #default="{ row }"><pre>{{ row.before_preview ?? '（不存在）' }}</pre><small v-if="row.before_truncated">预览已截断，请导出原包核对</small></template></el-table-column>
        <el-table-column label="目标值" min-width="255"><template #default="{ row }"><pre>{{ row.after_preview ?? '（不存在）' }}</pre><small v-if="row.after_truncated">预览已截断，请导出原包核对</small></template></el-table-column>
      </el-table>
      <div class="pagination diff-pages"><el-button size="small" :disabled="diff.page === 1 || busy.diff" @click="compare(diff.page - 1)">差异上一页</el-button><span>第 {{ diff.page }} 页</span><el-button size="small" :disabled="!diff.has_more || diff.page >= 1000 || busy.diff" @click="compare(diff.page + 1)">差异下一页</el-button></div>
    </template>
    <template #footer><el-button @click="opened = false">关闭历史</el-button></template>
  </el-dialog>
</template>
<style scoped>
:global(.workflow-history-dialog) { display: flex; flex-direction: column; max-height: calc(100dvh - 32px); margin: 16px auto !important; }
:global(.workflow-history-dialog .el-dialog__body) { overflow: auto; min-height: 0; }
:global(.workflow-history-dialog .el-dialog__header), :global(.workflow-history-dialog .el-dialog__footer) { flex-shrink: 0; }
.restoration-confirmation { margin: 16px 0; padding: 16px; border: 1px solid var(--el-color-primary-light-5); border-left: 4px solid var(--el-color-primary); border-radius: 6px; background: var(--el-color-primary-light-9); }
.restoration-confirmation p { line-height: 1.6; overflow-wrap: anywhere; }
.restoration-confirmation code { font-size: 12px; }
.history-title { display: flex; flex-wrap: wrap; gap: 8px; align-items: baseline; font-weight: 600; }
.history-title code { overflow-wrap: anywhere; font-weight: 400; font-size: 12px; }
.history-toolbar { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; margin: 16px 0; }
.comparison-selection { display: grid; grid-template-columns: 1fr 1.2fr; gap: 20px; padding: 20px; background: var(--el-fill-color-light); border: 1px solid var(--el-border-color-lighter); border-radius: 8px; }
.comparison-selection > div { min-width: 0; }
.comparison-selection p, .diff-summary { overflow-wrap: anywhere; line-height: 1.6; }
.history-table code { font-size: 11px; overflow-wrap: anywhere; }
.diff-table pre { margin: 8px 0; white-space: pre-wrap; overflow-wrap: anywhere; font-size: 12px; line-height: 1.6; font-family: Consolas, monospace; }
.diff-table small { display: block; overflow-wrap: anywhere; color: var(--el-text-color-secondary); }
.stale { opacity: .65; }
@media (max-width: 760px) { .comparison-selection { grid-template-columns: 1fr; padding: 14px; } }
</style>
