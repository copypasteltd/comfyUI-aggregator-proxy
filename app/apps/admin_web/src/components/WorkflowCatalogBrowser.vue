<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { api, readableError, time, unwrap, type Channel } from '../api'
import type { components } from '../generated/api'

type Source = components['schemas']['WorkflowExportStatus']
type Catalog = components['schemas']['WorkflowCatalogPage']
type Entry = components['schemas']['WorkflowCatalogEntry']
const props = defineProps<{ channel: Channel | undefined; open: boolean; blocked: boolean }>()
const emit = defineEmits<{ selected: [entry: Entry, revision: number]; busy: [value: boolean] }>()
const source = ref<Source | null>(null), catalog = ref<Catalog | null>(null)
const statusLoading = ref(false), loading = ref(false), statusError = ref(''), error = ref('')
const directory = ref(''), operationId = ref(''), pageIndex = ref(0)
let statusController: AbortController | null = null, controller: AbortController | null = null
let cursors: (string | null)[] = [null]
const sourceLabels = { ready: '导出目录可用', not_configured: '未配置导出目录', unavailable: '导出目录不可用', unknown: '来源状态未知或已过期' }
const errors: Record<string, string> = {
  invalid_workflow_source: 'JSON 或工作流结构无效', ui_workflow_not_supported: '请导出 API 格式', workflow_source_too_large: '文件超过 1 MiB',
  workflow_export_not_allowed: '不可读或不在允许目录内', workflow_export_changed: '文件发生变化，请刷新目录', workflow_catalog_changed: '目录发生变化，请重新加载首页',
  workflow_catalog_too_large: '目录或回执超过限额，请划分子目录', workflow_catalog_timeout: '目录检查超时，请缩小目录',
  workflow_export_not_configured: '渠道未配置导出目录', superseded_boot: 'Worker 已重启，请刷新来源状态', superseded_revision: '渠道配置已变化，请刷新来源状态',
  unsupported_command: 'Worker 尚不支持目录浏览，请更新 Worker',
}
const breadcrumbs = computed(() => directory.value ? directory.value.split('/').map((label, i, all) => ({ label, path: all.slice(0, i + 1).join('/') })) : [])
function stopCatalog() { controller?.abort(); controller = null; loading.value = false; emit('busy', false) }
function stop() { stopCatalog(); statusController?.abort(); statusController = null; statusLoading.value = false }
async function refreshStatus() {
  statusController?.abort()
  const channel = props.channel
  if (!channel || !props.open) return
  const current = new AbortController(); statusController = current; statusLoading.value = true; statusError.value = ''
  const timer = window.setTimeout(() => current.abort(), 12_000)
  try {
    const result = unwrap(await api.GET('/admin/api/v1/channels/{identifier}/workflow-exports', { signal: current.signal, params: { path: { identifier: channel.id } } }))
    if (!current.signal.aborted && statusController === current && props.channel?.id === channel.id) source.value = result
  } catch (e) {
    if (props.open && statusController === current) { source.value = null; statusError.value = current.signal.aborted ? '来源状态查询超时，请重试' : readableError(e) }
  } finally { window.clearTimeout(timer); if (statusController === current) { statusController = null; statusLoading.value = false } }
}
function delay(signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    const abort = () => { window.clearTimeout(timer); signal.removeEventListener('abort', abort); reject(new DOMException('Aborted', 'AbortError')) }
    const timer = window.setTimeout(() => { signal.removeEventListener('abort', abort); resolve() }, 1500)
    signal.addEventListener('abort', abort, { once: true }); if (signal.aborted) abort()
  })
}
async function poll(identifier: string, channelId: string, path: string, signal: AbortSignal) {
  while (!signal.aborted) {
    const result = unwrap(await api.GET('/admin/api/v1/workflow-catalogs/{identifier}', { signal, params: { path: { identifier } } }))
    if (signal.aborted) return
    if (result.channel_id !== channelId || result.relative_directory !== path) throw new Error('目录操作与来源不一致，请重新浏览')
    if (result.status === 'succeeded' && result.catalog) { catalog.value = result.catalog; return }
    if (['failed', 'partial_failed', 'cancelled'].includes(result.status)) throw new Error(errors[result.error_code ?? ''] ?? '目录读取失败，请刷新来源状态后重试')
    await delay(signal)
  }
}
async function load(path = directory.value, cursor: string | null = null, trail: (string | null)[] = [null], index = 0, resume = false) {
  const channel = props.channel, observation = source.value
  if (!channel || props.blocked || loading.value || (!resume && observation?.status !== 'ready')) return
  stopCatalog(); const current = new AbortController(); controller = current; loading.value = true; emit('busy', true); error.value = ''
  const timer = window.setTimeout(() => current.abort(), 90_000)
  try {
    if (!resume) {
      catalog.value = null; directory.value = path; operationId.value = ''; cursors = trail; pageIndex.value = index
      const accepted = unwrap(await api.POST('/admin/api/v1/workflow-catalogs', { signal: current.signal, headers: { 'Idempotency-Key': crypto.randomUUID() }, body: { channel_id: channel.id, expected_revision: observation!.channel_revision, relative_directory: path, cursor } }))
      if (current.signal.aborted) return
      operationId.value = accepted.operation_id
    }
    await poll(operationId.value, channel.id, path, current.signal)
  } catch (e) {
    if (props.open && controller === current) error.value = current.signal.aborted ? '目录读取已暂停或超时，可重新检查操作' : readableError(e)
  } finally { window.clearTimeout(timer); if (controller === current) { controller = null; loading.value = false; emit('busy', false) } }
}
function next() { const cursor = catalog.value?.next_cursor; if (cursor) void load(directory.value, cursor, [...cursors.slice(0, pageIndex.value + 1), cursor], pageIndex.value + 1) }
function previous() { const index = pageIndex.value - 1; if (index >= 0) void load(directory.value, cursors[index], cursors, index) }
function select(entry: Entry) { if (!props.blocked && !loading.value && source.value && entry.status === 'ready') emit('selected', entry, source.value.channel_revision) }
function hidden() { if (document.hidden) { const pending = loading.value; stop(); if (pending) error.value = '页面已隐藏，目录读取已暂停，可重新检查操作' } }
watch(() => [props.channel?.id, props.open], () => { stop(); source.value = null; catalog.value = null; directory.value = ''; operationId.value = ''; error.value = ''; statusError.value = ''; pageIndex.value = 0; cursors = [null]; void refreshStatus() }, { immediate: true })
document.addEventListener('visibilitychange', hidden)
onBeforeUnmount(() => { stop(); document.removeEventListener('visibilitychange', hidden) })
</script>

<template>
  <section v-if="channel" class="workflow-catalog" aria-label="Workflow 导出目录">
    <div class="toolbar">
      <el-tag :type="source?.status === 'ready' ? 'success' : 'info'">{{ source ? sourceLabels[source.status] : statusLoading ? '正在查询来源状态' : '来源状态未知或已过期' }}</el-tag>
      <el-button text :loading="statusLoading" :disabled="blocked || loading" @click="refreshStatus">刷新来源状态</el-button>
      <el-button :loading="loading" :disabled="blocked || source?.status !== 'ready' || loading" @click="load()">浏览导出目录</el-button>
    </div>
    <p v-if="statusError" role="alert" class="catalog-error">{{ statusError }}</p>
    <p v-else-if="source?.status === 'not_configured'" class="form-help">请在该 Windows Worker 中登记导出目录后刷新。</p>
    <p v-else-if="source?.status === 'unknown'" class="form-help">等待支持目录浏览的 Worker 上报新状态，再刷新；也可手动输入已知导出文件路径。</p>
    <p v-else-if="source?.status === 'unavailable'" class="form-help">请检查该 Worker 的导出目录及读取权限。</p>
    <p v-if="source?.observed_at" class="form-help">来源观测：{{ time(source.observed_at) }}</p>
    <el-alert v-if="error" :title="error" type="warning" :closable="false" role="alert" />
    <template v-if="catalog || loading || operationId">
      <nav class="catalog-breadcrumbs" aria-label="导出目录路径"><el-button link :disabled="blocked || loading" @click="load('')">导出根目录</el-button><template v-for="part in breadcrumbs" :key="part.path"><span>/</span><el-button link :disabled="blocked || loading" @click="load(part.path)">{{ part.label }}</el-button></template></nav>
      <el-table v-if="catalog" :data="catalog.entries" max-height="320" empty-text="此目录没有可列出的文件或子目录" aria-label="导出文件清单">
        <el-table-column label="名称" min-width="160"><template #default="{ row }"><span class="catalog-name">{{ row.name }}</span></template></el-table-column>
        <el-table-column label="格式 / 状态" min-width="190"><template #default="{ row }"><span v-if="row.kind === 'directory'">文件夹</span><span v-else-if="row.status === 'ready'">{{ row.export_format === 'manifest-v1' ? '发布包' : 'API 图' }} · {{ row.node_count }} 节点 · {{ Math.ceil(row.bytes / 1024) }} KiB</span><span v-else class="catalog-error">{{ errors[row.error_code] ?? '文件无效' }}</span></template></el-table-column>
        <el-table-column label="操作" width="90"><template #default="{ row }"><el-button v-if="row.kind === 'directory'" link :disabled="blocked || loading" :aria-label="'进入 ' + row.name" @click="load(row.relative_path)">进入</el-button><el-button v-else link :disabled="blocked || loading || row.status !== 'ready'" :aria-label="'选择 ' + row.name" @click="select(row)">选择</el-button></template></el-table-column>
      </el-table>
      <div v-if="catalog" class="toolbar catalog-pagination"><span class="form-help">第 {{ pageIndex + 1 }} 页 · {{ catalog.entries.length }} 项 · 跳过 {{ catalog.skipped_count }} 项</span><el-button :disabled="blocked || loading || pageIndex === 0" @click="previous">上一页</el-button><el-button :disabled="blocked || loading || !catalog.has_more" @click="next">下一页</el-button><el-button text :disabled="blocked || loading" @click="load()">重新加载首页</el-button></div>
      <p v-if="operationId" class="form-help catalog-operation">目录操作：{{ operationId }} <el-button text :disabled="blocked || loading" @click="load(directory, null, cursors, pageIndex, true)">检查目录操作</el-button></p>
      <el-button v-if="loading" text @click="stopCatalog">暂停等待目录</el-button>
    </template>
  </section>
</template>

<style scoped>
.workflow-catalog { border: 1px solid var(--el-border-color); border-radius: 8px; padding: 14px; margin-bottom: 18px; }
.catalog-breadcrumbs { display: flex; flex-wrap: wrap; align-items: center; gap: 7px; margin: 12px 0; }
.catalog-pagination { margin-top: 12px; flex-wrap: wrap; }
.catalog-name, .catalog-operation { overflow-wrap: anywhere; }
.catalog-error { color: var(--el-color-danger); }
</style>
