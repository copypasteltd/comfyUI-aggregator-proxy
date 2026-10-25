<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh, Upload } from '@element-plus/icons-vue'
import { api, ApiError, readableError, revisionHeaders, time, unwrap, type ScopeOption } from '../api'
import type { components } from '../generated/api'
import { useSession } from '../session'
import ScopeSelect from '../components/ScopeSelect.vue'
import WorkflowImportDialog from '../components/WorkflowImportDialog.vue'
import WorkflowSyncSelect from '../components/WorkflowSyncSelect.vue'
import WorkflowRequestStatus from '../components/WorkflowRequestStatus.vue'
import WorkflowHistoryDialog from '../components/WorkflowHistoryDialog.vue'
import WorkflowManifestForm from '../components/WorkflowManifestForm.vue'
import { exactObject, formatExactDraft, isRecord, safeInteger, stringifyExactJson } from '../workflowJson'

type Workflow = components['schemas']['ManagedWorkflow']
type Task = Workflow['task_type']
type Option = components['schemas']['WorkflowSyncOptions']['data'][number]
type Action = { key: string; description: string } & (
  { kind: 'create'; body: components['schemas']['WorkflowCreate'] } |
  { kind: 'patch'; id: string; body: components['schemas']['WorkflowPatch'] } |
  { kind: 'lifecycle'; id: string; body: components['schemas']['WorkflowLifecycle'] } |
  { kind: 'restore'; id: string; body: components['schemas']['WorkflowDraftRestore'] } |
  { kind: 'release'; id: string; body: components['schemas']['ExpectedRevision'] } |
  { kind: 'sync'; body: components['schemas']['WorkflowSync'] })
const session = useSession(), snapshot = ref<components['schemas']['WorkflowDirectoryPage'] | null>(null), loading = ref(false), error = ref(''), page = ref(1)
const rows = computed(() => snapshot.value?.data ?? []), visible = ref(!document.hidden), loadedAt = ref(0)
const filters = reactive({ q: '', project_id: '', task_type: '' as Task | '', status: '' as 'active' | 'archived' | '' }), applied = ref({ ...filters })
const taskNames: Record<Task, string> = { text_to_image: '文生图', image_to_image: '图生图', references_to_image: '多参考文图生图', text_to_video: '文生视频', references_to_video: '多参考文图生视频', text_to_audio: '音频生成' }
const metadataDirty = ref(false), restoreStatus = ref(false)
const drawer = ref(false), editing = ref<Workflow | null>(null), detailLoading = ref(false), jsonText = ref('{}'), formError = ref(''), readonlyDraft = ref(false)
const form = ref({ name: '', project_id: '', task_type: 'text_to_image' as Task }), scope = ref<ScopeOption | null>(null), fileInput = ref<HTMLInputElement | null>(null), fileLoading = ref(false)
const importDialog = ref(false), importTarget = ref<Workflow | null>(null), syncDialog = ref(false), syncTarget = ref<Workflow | null>(null)
const lifecycleDialog = ref(false), lifecycleTarget = ref<Workflow | null>(null)
const historyDialog = ref(false), historyTarget = ref<Workflow | null>(null)
const releaseIds = ref<string[]>([]), channelIds = ref<string[]>([]), releaseOptions = ref<Option[]>([]), channelOptions = ref<Option[]>([]), releaseValid = ref(false), channelValid = ref(false)
const writing = ref(false), pending = ref<Action | null>(null), uncertain = ref(false), writeError = ref(''), operationId = ref(''), notice = ref('')
const blocked = computed(() => writing.value || !!pending.value), editable = computed(() => session.allows('workflows:write') && !readonlyDraft.value && !detailLoading.value && !blocked.value && visible.value)
const canSync = computed(() => releaseValid.value && channelValid.value && !blocked.value && visible.value && session.allows('workflows:publish'))
type Read = 'list' | 'detail' | 'session'
const reads = new Map<Read, { controller: AbortController; timer: ReturnType<typeof setTimeout> }>()
let disposed = false, confirmationEpoch = 0, confirming = false, writeController: AbortController | null = null, writeDeadline: ReturnType<typeof setTimeout> | undefined
let reader: FileReader | null = null, fileTimer: ReturnType<typeof setTimeout> | undefined
function active() { return !disposed && !document.hidden && !!session.info && !session.ending }
function stopRead(kind: Read) { const read = reads.get(kind); if (read) { clearTimeout(read.timer); read.controller.abort(); reads.delete(kind) } if (kind === 'list') loading.value = false; if (kind === 'detail') detailLoading.value = false }
function begin(kind: Read) { stopRead(kind); const controller = new AbortController(); reads.set(kind, { controller, timer: setTimeout(() => controller.abort(new DOMException('读取超时，请重试', 'TimeoutError')), 10000) }); return controller }
function owns(kind: Read, own: AbortController) { return active() && reads.get(kind)?.controller === own }
function finish(kind: Read, own: AbortController) { if (reads.get(kind)?.controller === own) { clearTimeout(reads.get(kind)!.timer); reads.delete(kind); if (kind === 'list') loading.value = false; if (kind === 'detail') detailLoading.value = false } }
function readError(own: AbortController, e: unknown) { return own.signal.reason instanceof DOMException && own.signal.reason.name === 'TimeoutError' ? '读取超时，请重试' : readableError(e) }
function stopFile() { clearTimeout(fileTimer); fileTimer = undefined; const own = reader; reader = null; if (own?.readyState === FileReader.LOADING) own.abort(); fileLoading.value = false; if (fileInput.value) fileInput.value.value = '' }
function stopWrite() { clearTimeout(writeDeadline); writeDeadline = undefined; if (writeController) { writeController.abort(); writeController = null; if (pending.value && !disposed) { uncertain.value = true; writeError.value = '本地等待已停止，服务器结果尚未确认。请使用原请求核对。' } } writing.value = false }
function stopAll() { confirmationEpoch++; for (const kind of ['list', 'detail', 'session'] as Read[]) stopRead(kind); stopWrite(); stopFile() }
function clearDraft() { metadataDirty.value = false; stopRead('detail'); stopFile(); editing.value = null; jsonText.value = '{}'; form.value = { name: '', project_id: '', task_type: 'text_to_image' }; scope.value = null; formError.value = ''; readonlyDraft.value = false }
function clearSync() { syncTarget.value = null; releaseIds.value = []; channelIds.value = []; releaseOptions.value = []; channelOptions.value = []; releaseValid.value = false; channelValid.value = false }
async function refresh() {
  if (!active()) return
  const own = begin('list'); loading.value = true; error.value = ''; const query = { ...applied.value }
  try { const result = unwrap(await api.GET('/admin/api/v1/workflows', { signal: own.signal, params: { query: { page: page.value, q: query.q || undefined, project_id: query.project_id || undefined, task_type: query.task_type || undefined, status: query.status || undefined } } })); if (owns('list', own) && !own.signal.aborted) { snapshot.value = result; loadedAt.value = Date.now() } }
  catch (e) { if (owns('list', own)) error.value = readError(own, e) } finally { finish('list', own) }
}
function apply() { applied.value = { ...filters, q: filters.q.trim() }; page.value = 1; void refresh() }
function reset() { Object.assign(filters, { q: '', project_id: '', task_type: '', status: '' }); apply() }
function turn(delta: number) { if (loading.value || page.value + delta < 1 || page.value + delta > 1000) return; page.value += delta; void refresh() }
async function open(row?: Workflow, inspect = false) {
  if (!active() || (blocked.value && !inspect)) return
  clearDraft(); editing.value = row ?? null; readonlyDraft.value = inspect || !!row?.archived || !session.allows('workflows:write'); drawer.value = true
  form.value = { name: row?.name ?? '', project_id: row?.project_id ?? session.info?.scopes[0]?.project_id ?? '', task_type: row?.task_type ?? 'text_to_image' }
  if (!row) return
  const own = begin('detail'); detailLoading.value = true
  try { const result = exactObject(unwrap(await api.GET('/admin/api/v1/workflows/{identifier}', { parseAs: 'text', signal: own.signal, params: { path: { identifier: row.id } } })), true); if (typeof result.name !== 'string' || !isRecord(result.draft)) throw new Error('Workflow 草稿响应格式无效'); const revision = safeInteger(result.revision, 1); if (!owns('detail', own) || own.signal.aborted || !drawer.value || editing.value?.id !== row.id) return; editing.value = { ...row, name: result.name, revision, archived: result.archived === true }; readonlyDraft.value = inspect || result.archived === true || !session.allows('workflows:write'); form.value.name = result.name; jsonText.value = formatExactDraft(result.draft) }
  catch (e) { if (owns('detail', own)) { formError.value = readError(own, e); readonlyDraft.value = true } } finally { finish('detail', own) }
}
function importFile(event: Event) {
  const input = event.target as HTMLInputElement, file = input.files?.[0]; stopFile()
  if (!file || !editable.value || !drawer.value) return
  if (file.size > 10 * 1024 * 1024) { formError.value = '发布包文件不能超过 10 MiB'; return }
  const own = new FileReader(), draftId = editing.value?.id; reader = own; fileLoading.value = true; formError.value = ''
  const valid = () => reader === own && active() && drawer.value && editable.value && editing.value?.id === draftId
  const done = () => { if (reader === own) { reader = null; clearTimeout(fileTimer); fileLoading.value = false } }
  own.onload = () => { if (valid()) { try { exactObject(String(own.result)); jsonText.value = String(own.result); ElMessage.success('已读入草稿，保存后可进行冻结校验') } catch (e) { formError.value = readableError(e) } } done() }
  own.onerror = () => { if (valid()) formError.value = '文件读取失败，请重试'; done() }
  fileTimer = setTimeout(() => { if (valid()) formError.value = '文件读取超时，请重试'; stopFile() }, 10000); own.readAsText(file)
}
function clearPending() { if (writing.value) return; restoreStatus.value = false; pending.value = null; uncertain.value = false; writeError.value = '' }
async function execute(action: Action) {
  if (!active() || writing.value || (pending.value && pending.value !== action) || !session.allows(action.kind === 'create' || action.kind === 'patch' || action.kind === 'restore' || action.kind === 'lifecycle' ? 'workflows:write' : 'workflows:publish')) return
  const unknown = uncertain.value, token = session.info?.csrf_token
  pending.value = action; uncertain.value = false; writing.value = true; writeError.value = ''; notice.value = ''
  const own = new AbortController(); writeController = own; writeDeadline = setTimeout(() => own.abort(new DOMException('请求超时', 'TimeoutError')), 10000)
  const headers = { 'Idempotency-Key': action.key }
  let restoration: components['schemas']['WorkflowDraftRestored'] | undefined, lifecycle: components['schemas']['WorkflowLifecycleResult'] | undefined
  try {
    if (action.kind === 'create') unwrap(await api.POST('/admin/api/v1/workflows', { signal: own.signal, headers, body: action.body, bodySerializer: body => stringifyExactJson(body) }))
    else if (action.kind === 'patch') unwrap(await api.PATCH('/admin/api/v1/workflows/{identifier}', { signal: own.signal, params: { path: { identifier: action.id } }, headers: { ...headers, ...revisionHeaders(action.body.expected_revision) }, body: action.body, bodySerializer: body => stringifyExactJson(body) }))
    else if (action.kind === 'lifecycle') lifecycle = unwrap(await api.POST('/admin/api/v1/workflows/{identifier}/lifecycle', { signal: own.signal, params: { path: { identifier: action.id } }, headers: { ...headers, ...revisionHeaders(action.body.expected_revision) }, body: action.body }))
    else if (action.kind === 'restore') restoration = unwrap(await api.POST('/admin/api/v1/workflows/{identifier}/draft-restorations', { signal: own.signal, params: { path: { identifier: action.id } }, headers: { ...headers, ...revisionHeaders(action.body.expected_revision) }, body: action.body }))
    else if (action.kind === 'release') unwrap(await api.POST('/admin/api/v1/workflows/{identifier}/releases', { signal: own.signal, params: { path: { identifier: action.id } }, headers: { ...headers, ...revisionHeaders(action.body.expected_revision) }, body: action.body }))
    else { const result = unwrap(await api.POST('/admin/api/v1/workflow-syncs', { signal: own.signal, headers, body: action.body })); if (writeController === own && !own.signal.aborted && session.info?.csrf_token === token) operationId.value = result.operation_id }
    if (disposed || writeController !== own || own.signal.aborted || session.info?.csrf_token !== token) return
    pending.value = null; uncertain.value = false; writing.value = false; writeController = null; clearTimeout(writeDeadline)
    if (action.kind === 'sync') { syncDialog.value = false; notice.value = '批量同步已受理，Worker 应用结果请在操作中心核对；尚未授予运行批准。' }
    else if (action.kind === 'lifecycle') { notice.value = `原状态变更事务已确认：${lifecycle!.archived ? '归档' : '解除归档'}，Workflow revision ${lifecycle!.previous_revision} → ${lifecycle!.revision}。当前状态请刷新核对。` }
    else if (action.kind === 'restore') { restoreStatus.value = false; notice.value = `原恢复事务已确认：草稿 revision ${restoration!.previous_revision} → ${restoration!.revision}。当前内容请重新读取；冻结、验证和发布需单独操作。` }
    else { drawer.value = false; notice.value = action.kind === 'release' ? '版本已冻结，设备依赖和运行画像仍需验证。' : '草稿保存已确认。' }
    ElMessage.success('Workflow 操作已确认'); void refresh()
  } catch (e) {
    if (disposed || writeController !== own || session.info?.csrf_token !== token) return
    const rejected = !own.signal.aborted && e instanceof ApiError && e.status >= 400 && e.status < 500 && e.status !== 408
    if (rejected && !unknown) { pending.value = null; uncertain.value = false; writeError.value = readableError(e) }
    else { uncertain.value = true; writeError.value = '服务器结果尚未确认。请使用原请求核对；版本冲突时查看当前草稿，不自动覆盖。' }
  } finally { if (writeController === own) { clearTimeout(writeDeadline); writeController = null; writing.value = false } }
}
function save() {
  if (!editable.value || fileLoading.value) return
  formError.value = ''
  try {
    if (!form.value.name.trim()) throw new Error('请填写 Workflow 名称')
    if (metadataDirty.value) throw new Error('参数表单还有未应用修改，请先应用到本地草稿或放弃表单修改')
    const draft = exactObject(jsonText.value)
    const base = { name: form.value.name.trim(), draft }, key = crypto.randomUUID()
    if (editing.value) void execute({ kind: 'patch', key, description: '核对草稿编辑：' + editing.value.id + ' · revision ' + editing.value.revision, id: editing.value.id, body: { ...base, expected_revision: editing.value.revision } })
    else { if (!scope.value || scope.value.project_id !== form.value.project_id) throw new Error('请选择并核对所属项目'); void execute({ kind: 'create', key, description: '核对创建 Workflow：' + base.name, body: { ...base, tenant_id: scope.value.tenant_id, project_id: scope.value.project_id, task_type: form.value.task_type } }) }
  } catch (e) { formError.value = readableError(e) }
}
async function release(row: Workflow) {
  if (!active() || blocked.value || row.archived || confirming || !session.allows('workflows:publish')) return
  const epoch = confirmationEpoch, token = session.info?.csrf_token; confirming = true
  try { await ElMessageBox.confirm('校验当前草稿并冻结为新的不可变版本。设备依赖与运行画像需另外验证。', '冻结发布版本', { confirmButtonText: '校验并冻结', cancelButtonText: '取消' }); if (active() && !blocked.value && epoch === confirmationEpoch && session.info?.csrf_token === token) void execute({ kind: 'release', id: row.id, key: crypto.randomUUID(), description: '核对冻结版本：' + row.id + ' · revision ' + row.revision, body: { expected_revision: row.revision } }) }
  catch (e) { if (e !== 'cancel' && e !== 'close') error.value = readableError(e) } finally { confirming = false }
}
function openSync(row: Workflow) { if (!active() || blocked.value || row.archived || !session.allows('workflows:publish')) return; clearSync(); syncTarget.value = row; writeError.value = ''; syncDialog.value = true }
function synchronize() {
  if (!canSync.value) return
  const release = releaseOptions.value.find(item => item.kind === 'release' && item.id === releaseIds.value[0])
  if (!release || release.kind !== 'release' || !release.validated || channelIds.value.length > 100) return
  const targets = channelIds.value.map(id => { const channel = channelOptions.value.find(item => item.kind === 'channel' && item.id === id); if (!channel || channel.kind !== 'channel') throw new Error('目标渠道不可用，请重新核对'); return { channel_id: id, expected_revision: channel.revision } })
  void execute({ kind: 'sync', key: crypto.randomUUID(), description: '核对同步：' + release.id + ' · ' + targets.length + ' 个渠道', body: { direction: 'push', release_id: release.id, release_digest: release.digest, targets } })
}
function openImport(row?: Workflow) { if (!active() || blocked.value || row?.archived || !session.allows('workflows:write')) return; importTarget.value = row ?? null; importDialog.value = true }
function restoreDraft(body: components['schemas']['WorkflowDraftRestore']) {
  if (!active() || blocked.value || !session.allows('workflows:write') || !historyDialog.value || !historyTarget.value || historyTarget.value.archived) return
  const id = historyTarget.value.id
  historyDialog.value = false; restoreStatus.value = true
  void execute({ kind: 'restore', id, key: crypto.randomUUID(), body: { ...body }, description: `恢复 ${body.source_release_id} 为 ${id} 的草稿 · revision ${body.expected_revision}` })
}
function openLifecycle(row: Workflow) { if (!active() || blocked.value || !Number.isSafeInteger(row.revision) || row.revision > 9007199254740990 || !session.allows('workflows:write')) return; lifecycleTarget.value = { ...row }; lifecycleDialog.value = true }
function changeLifecycle() {
  const row = lifecycleTarget.value
  if (!row || !active() || blocked.value || !session.allows('workflows:write')) return
  const action: Action = { kind: 'lifecycle', id: row.id, key: crypto.randomUUID(), description: `核对 ${row.archived ? '解除归档' : '归档'}：${row.id} · revision ${row.revision}`, body: { expected_revision: row.revision, archived: !row.archived } }
  lifecycleDialog.value = false
  void execute(action)
}
function openHistory(row: Workflow) { if (!active() || !visible.value) return; historyTarget.value = row; historyDialog.value = true }
function inspectCurrent() { const action = pending.value; if (!action || (action.kind !== 'patch' && action.kind !== 'release' && action.kind !== 'restore' && action.kind !== 'lifecycle')) return; const row = rows.value.find(row => row.id === action.id); if (row) void open(row, true) }
async function visibility() {
  if (document.hidden) { visible.value = false; stopAll(); drawer.value = false; clearDraft(); syncDialog.value = false; clearSync(); importDialog.value = false; importTarget.value = null; lifecycleDialog.value = false; lifecycleTarget.value = null; historyDialog.value = false; historyTarget.value = null; return }
  visible.value = false
  const own = begin('session')
  try { await session.refresh(own.signal); if (owns('session', own) && !own.signal.aborted) { visible.value = true; void refresh() } } catch (e) { if (owns('session', own)) error.value = readError(own, e) } finally { finish('session', own) }
}
watch(drawer, value => { if (!value) { if (pending.value?.kind === 'create' || pending.value?.kind === 'patch') stopWrite(); clearDraft() } })
watch(syncDialog, value => { if (!value) { if (pending.value?.kind === 'sync') stopWrite(); clearSync() } })
watch(lifecycleDialog, value => { if (!value) lifecycleTarget.value = null })
watch(historyDialog, value => { if (!value) historyTarget.value = null })
watch(importDialog, value => { if (!value) importTarget.value = null })
watch(() => [session.info?.csrf_token, session.ending] as const, ([token, ending], [old]) => { if (!token || ending || token !== old) { restoreStatus.value = false; stopAll(); pending.value = null; uncertain.value = false; drawer.value = false; clearDraft(); syncDialog.value = false; clearSync(); importDialog.value = false; importTarget.value = null; lifecycleDialog.value = false; lifecycleTarget.value = null; historyDialog.value = false; historyTarget.value = null; snapshot.value = null; loadedAt.value = 0; error.value = ''; writeError.value = ''; notice.value = ''; operationId.value = '' } })
onMounted(() => { void refresh(); document.addEventListener('visibilitychange', visibility) })
onBeforeUnmount(() => { disposed = true; stopAll(); pending.value = null; clearDraft(); clearSync(); snapshot.value = null; document.removeEventListener('visibilitychange', visibility) })
</script>
<template>
  <el-dialog v-if="visible && session.info && !session.ending" v-model="lifecycleDialog" title="Workflow 状态变更" width="min(560px, 96vw)" :close-on-click-modal="false">
    <template v-if="lifecycleTarget"><h3>{{ lifecycleTarget.archived ? '解除归档' : '归档' }} {{ lifecycleTarget.name }}</h3><p class="id-line">{{ lifecycleTarget.id }} · revision {{ lifecycleTarget.revision }}</p><p>{{ lifecycleTarget.archived ? '解除归档后可继续维护草稿；公开模型仍需单独发布。' : '归档后停止新编辑、冻结、同步和验证。已发布的公开模型或待完成同步会阻止归档。' }}</p><p class="form-help">历史版本和已接收任务保留；此操作不会删除设备文件或取消任务。</p></template>
    <template #footer><el-button @click="lifecycleDialog = false">取消状态变更</el-button><el-button type="primary" :disabled="blocked || !visible" @click="changeLifecycle">确认{{ lifecycleTarget?.archived ? '解除归档' : '归档' }}</el-button></template>
  </el-dialog>
  <WorkflowHistoryDialog v-if="visible && session.info && !session.ending" v-model="historyDialog" :target="historyTarget" :can-restore="session.allows('workflows:write') && !blocked && !historyTarget?.archived && visible" @restore="restoreDraft"/>
  <section v-if="restoreStatus" aria-label="草稿恢复请求">
    <WorkflowRequestStatus :description="pending?.kind === 'restore' ? pending.description : null" :uncertain="uncertain" :busy="writing" :active="visible" :error="writeError" :can-inspect="pending?.kind === 'restore'" @retry="pending?.kind === 'restore' && execute(pending)" @clear="clearPending" @inspect="inspectCurrent"/>
    <el-button v-if="!pending" size="small" @click="restoreStatus = false; writeError = ''">关闭恢复提示</el-button>
  </section>
  <WorkflowImportDialog v-if="visible && session.info && !session.ending" v-model="importDialog" :target="importTarget" @completed="refresh"/>
  <div class="page-heading"><div><p class="eyebrow li-lab">CAPABILITIES / WORKFLOWS</p><h1>Workflow 管理</h1><p class="muted">统一管理图片、视频和音频草稿、冻结版本与渠道同步。</p></div><div class="toolbar"><el-button :icon="Refresh" :loading="loading" :disabled="!visible" @click="refresh">刷新</el-button><el-button v-if="session.allows('workflows:write')" :disabled="blocked || !visible" @click="openImport()">从渠道导入</el-button><el-button v-if="session.allows('workflows:write')" type="primary" :icon="Plus" :disabled="blocked || !visible" @click="open()">导入 Workflow</el-button></div></div>
  <el-alert v-if="error" :title="error + (loadedAt ? '。当前显示最后成功读取的数据。' : '。目录尚未读取成功。')" type="error" :closable="false"/>
  <el-button v-if="!visible" @click="visibility">核对管理会话</el-button>
  <el-alert v-if="notice" :title="notice" type="success" :closable="false"/><p v-if="operationId" class="form-help">操作 ID：{{ operationId }} · <router-link to="/operations">在操作中心查看</router-link></p>
  <WorkflowRequestStatus v-if="!restoreStatus && !drawer && !syncDialog" :description="pending?.description ?? null" :uncertain="uncertain" :busy="writing" :active="visible" :error="writeError" :can-inspect="!!pending && ['patch', 'release', 'restore', 'lifecycle'].includes(pending.kind)" @retry="pending && execute(pending)" @clear="clearPending" @inspect="inspectCurrent"/>
  <section class="panel li-card">
    <el-form label-position="top" class="workflow-filters" @submit.prevent="apply"><el-form-item label="Workflow 名称"><el-input v-model="filters.q" aria-label="Workflow 名称搜索" maxlength="128" clearable/></el-form-item><el-form-item label="项目"><ScopeSelect v-model="filters.project_id" :active="visible" allow-inactive clearable label="Workflow 项目筛选"/></el-form-item><el-form-item label="能力"><el-select v-model="filters.task_type" aria-label="Workflow 能力筛选" clearable><el-option v-for="(label, value) in taskNames" :key="value" :label="label" :value="value"/></el-select></el-form-item><el-form-item label="状态"><el-select v-model="filters.status" aria-label="Workflow 状态筛选" clearable><el-option label="未归档" value="active"/><el-option label="已归档" value="archived"/></el-select></el-form-item><div class="toolbar"><el-button native-type="submit" type="primary" :disabled="!visible || loading">应用筛选</el-button><el-button :disabled="!visible || loading" @click="reset">重置</el-button></div></el-form>
    <p class="form-help">{{ snapshot ? '共 ' + snapshot.total + ' 个 Workflow · 快照 ' + time(snapshot.snapshot_at) : '等待首次读取目录' }}</p>
    <el-table :data="rows" v-loading="loading" :empty-text="error && !loadedAt ? '目录读取失败，请重试' : '没有匹配的 Workflow'" table-layout="auto"><el-table-column label="名称 / ID" min-width="240"><template #default="{ row }"><strong>{{ row.name }}</strong><span class="id-line">{{ row.id }}</span></template></el-table-column><el-table-column label="能力" min-width="170"><template #default="{ row }">{{ taskNames[row.task_type as Task] }}</template></el-table-column><el-table-column prop="revision" label="草稿版本" min-width="100"/><el-table-column label="状态" min-width="90"><template #default="{ row }">{{ row.archived ? '已归档' : '未归档' }}</template></el-table-column><el-table-column label="创建时间" min-width="175"><template #default="{ row }">{{ time(row.created_at) }}</template></el-table-column><el-table-column label="操作" min-width="220" fixed="right"><template #default="{ row }"><el-button text size="small" :disabled="blocked || !visible" @click="open(row)">{{ row.archived ? '查看草稿' : session.allows('workflows:write') ? '编辑草稿' : '查看' }}</el-button><el-button text size="small" :disabled="!visible" @click="openHistory(row)">发布历史</el-button><el-button v-if="session.allows('workflows:write')" text size="small" :disabled="row.archived || blocked || !visible" @click="openImport(row)">从渠道更新</el-button><el-button v-if="session.allows('workflows:write')" text size="small" :disabled="blocked || !visible" @click="openLifecycle(row)">{{ row.archived ? '解除归档' : '归档' }}</el-button><el-button v-if="session.allows('workflows:publish')" text size="small" :disabled="row.archived || blocked || !visible" @click="release(row)">冻结版本</el-button><el-button v-if="session.allows('workflows:publish')" text size="small" :disabled="row.archived || blocked || !visible" @click="openSync(row)">同步到渠道</el-button></template></el-table-column></el-table>
    <div class="pagination"><el-button size="small" :disabled="page === 1 || loading || !visible" @click="turn(-1)">上一页</el-button><span>第 {{ snapshot?.page ?? page }} 页</span><el-button size="small" :disabled="!snapshot?.has_more || page >= 1000 || loading || !visible" @click="turn(1)">下一页</el-button></div>
  </section>
  <el-drawer v-model="drawer" :title="readonlyDraft ? '查看 Workflow 草稿' : editing ? 'Workflow 草稿' : '导入 Workflow 发布包'" size="min(780px, 100vw)">
    <el-alert v-if="formError" :title="formError" type="error" :closable="false"/>
    <WorkflowRequestStatus :description="pending?.description ?? null" :uncertain="uncertain" :busy="writing" :active="visible" :error="writeError" :can-inspect="!!pending && ['patch', 'release', 'restore', 'lifecycle'].includes(pending.kind)" @retry="pending && execute(pending)" @clear="clearPending" @inspect="inspectCurrent"/>
    <WorkflowManifestForm v-if="drawer && visible && session.info && !session.ending" :key="editing?.id ?? 'new'" v-model="jsonText" :task-type="form.task_type" :readonly="!editable" :available="drawer && visible && !detailLoading" @dirty="metadataDirty = $event"/>
    <el-form v-loading="detailLoading" label-position="top" :disabled="!editable" @submit.prevent="save"><div class="policy-grid"><el-form-item label="名称" required><el-input v-model="form.name" aria-label="Workflow 草稿名称" maxlength="128"/></el-form-item><el-form-item label="能力类型" required><el-select v-model="form.task_type" :disabled="!!editing" aria-label="Workflow 草稿能力" class="full"><el-option v-for="(label, value) in taskNames" :key="value" :value="value" :label="label"/></el-select></el-form-item></div><el-form-item v-if="!editing" label="所属项目" required><ScopeSelect v-model="form.project_id" :active="drawer && visible && !blocked" :disabled="!editable" @resolved="scope = $event"/></el-form-item><el-form-item label="发布包 JSON"><div class="full"><input ref="fileInput" type="file" accept=".json,application/json" hidden @change="importFile"/><el-button v-if="!readonlyDraft" :icon="Upload" :loading="fileLoading" @click="fileInput?.click()">从本地文件导入</el-button><p class="form-help">包含 API-format 图、参数绑定、最终输出、规格预设、依赖锁与运行画像。仅保存草稿不会发布生成能力。</p><el-input v-model="jsonText" aria-label="Workflow 发布包 JSON" type="textarea" :rows="21" spellcheck="false" class="json-editor"/></div></el-form-item><div class="drawer-footer"><el-button v-if="!readonlyDraft" native-type="submit" type="primary" :disabled="!editable || fileLoading" :loading="writing">保存草稿</el-button></div></el-form><el-button @click="drawer = false">关闭</el-button>
  </el-drawer>
  <el-dialog v-model="syncDialog" title="同步 Workflow 到渠道" width="min(620px, 94vw)">
    <WorkflowRequestStatus :description="pending?.description ?? null" :uncertain="uncertain" :busy="writing" :active="visible" :error="writeError" :can-inspect="false" @retry="pending && execute(pending)" @clear="clearPending"/>
    <el-form v-if="syncTarget" label-position="top" :disabled="blocked || !visible" @submit.prevent="synchronize"><el-form-item label="冻结版本" required><WorkflowSyncSelect v-model="releaseIds" kind="release" label="同步冻结版本" :project-id="syncTarget.project_id" :workflow-id="syncTarget.id" :active="syncDialog && visible" :disabled="blocked" @resolved="releaseOptions = $event" @validity="releaseValid = $event"/></el-form-item><el-form-item label="目标渠道" required><WorkflowSyncSelect v-model="channelIds" kind="channel" label="同步目标渠道" :project-id="syncTarget.project_id" :workflow-id="syncTarget.id" :active="syncDialog && visible" :disabled="blocked" @resolved="channelOptions = $event" @validity="channelValid = $event"/></el-form-item><p class="form-help">最多 100 个渠道。推送后需等待 Worker 回执和验证，运行批准另行处理。</p><el-button type="primary" native-type="submit" :loading="writing" :disabled="!canSync">推送并排队</el-button></el-form><template #footer><el-button @click="syncDialog = false">关闭</el-button></template>
  </el-dialog>
</template>
<style scoped>
.workflow-filters { display: grid; grid-template-columns: minmax(160px, 1.3fr) minmax(180px, 1.5fr) minmax(150px, 1fr) minmax(110px, .8fr); gap: 12px; padding: 20px; }
.panel > .form-help { padding: 0 20px; }
.workflow-filters .toolbar { grid-column: 1 / -1; margin-bottom: 12px; }
.workflow-filters :deep(.el-form-item) { margin-bottom: 0; min-width: 0; }
@media (max-width: 760px) { .workflow-filters { grid-template-columns: 1fr; } }
</style>
