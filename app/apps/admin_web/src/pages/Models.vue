<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import GatewayCatalogLauncher from '../components/GatewayCatalogLauncher.vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh } from '@element-plus/icons-vue'
import { useRouter } from 'vue-router'
import { api, ApiError, readableError, revisionHeaders, time, unwrap, type ScopeOption } from '../api'
import type { components, paths } from '../generated/api'
import { useSession } from '../session'
import ScopeSelect from '../components/ScopeSelect.vue'
import ModelMappingSelect from '../components/ModelMappingSelect.vue'
import ModelRequestStatus from '../components/ModelRequestStatus.vue'
import ModelUsageDialog from '../components/ModelUsageDialog.vue'

type Model = components['schemas']['ModelView']
type Option = components['schemas']['ModelMappingOptions']['data'][number]
type Preset = Extract<Option, { kind: 'preset' }>
type Channel = Extract<Option, { kind: 'channel' }>
type Filter = NonNullable<paths['/admin/api/v1/public-models']['get']['parameters']['query']>
type Action = { key: string; description: string } & (
  { kind: 'create'; body: components['schemas']['ModelCreate'] } |
  { kind: 'patch'; id: string; body: components['schemas']['ModelPatch'] } |
  { kind: 'publish'; id: string; body: components['schemas']['ModelAction'] } |
  { kind: 'validation'; release: string; body: components['schemas']['ReleaseValidation'] })
const session = useSession(), router = useRouter(), snapshot = ref<components['schemas']['ModelCatalogPage'] | null>(null)
const usageDialog = ref(false), usageTarget = ref<Model | null>(null)
const applied = ref<Filter>({ page: 1 }), draft = reactive({ q: '', project_id: '', task_type: '' as NonNullable<Filter['task_type']> | '', status: '' as NonNullable<Filter['status']> | '' })
const visible = ref(!document.hidden), loading = ref(false), error = ref(''), writeError = ref(''), drawer = ref(false), mappingLoading = ref(false), formError = ref('')
const editing = ref<Model | null>(null), scope = ref<ScopeOption | null>(null), form = reactive({ alias: '', project_id: '', workflows: [] as string[], releases: [] as string[], presets: [] as string[], channels: [] as string[] })
const presetChoice = ref<Preset | null>(null), valid = reactive({ workflow: false, release: false, preset: false, channel: false, validation: false })
const currentPreset = computed<Preset | undefined>(() => editing.value && form.releases[0] === editing.value.release_id ? { kind: 'preset', id: 'current', release_id: editing.value.release_id, task_type: editing.value.task_type as Preset['task_type'], defaults: { ...editing.value.defaults, n: 1 } as Preset['defaults'], usable: true } : undefined)
const pending = ref<Action | null>(null), uncertain = ref(false), writing = ref(false), blocked = computed(() => writing.value || !!pending.value)
const canSave = computed(() => !!scope.value && scope.value.project_id === form.project_id && !!form.alias && valid.workflow && valid.release && valid.preset && valid.channel && !!presetChoice.value && !mappingLoading.value && !blocked.value && visible.value)
const taskNames: Record<string, string> = { text_to_image: '文生图', image_to_image: '图生图', references_to_image: '多参考图生图', text_to_video: '文生视频', references_to_video: '多参考图生视频', text_to_audio: 'Workflow 音频', image_to_3d: '单图生成 3D' }
const page = computed(() => applied.value.page ?? 1)
const validationDialog = ref(false), validationModel = ref<Model | null>(null), validationChannels = ref<string[]>([]), validationChannel = ref<Channel | null>(null), sample = ref(''), validationError = ref(''), accepted = ref<components['schemas']['ValidationAccepted'] | null>(null)
let validationKey = '', disposed = false, mappingEpoch = 0, confirmationEpoch = 0, confirming = false
type Read = 'list' | 'parent' | 'session'
const reads = new Map<Read, { request: AbortController; timer: ReturnType<typeof setTimeout> }>()
let writeController: AbortController | null = null, writeDeadline: ReturnType<typeof setTimeout> | null = null
function active() { return !disposed && !document.hidden && !!session.info && !session.ending }
function stopRead(kind: Read) { const read = reads.get(kind); if (read) { clearTimeout(read.timer); read.request.abort(); reads.delete(kind) } if (kind === 'list') loading.value = false; if (kind === 'parent') mappingLoading.value = false }
function startRead(kind: Read) { stopRead(kind); const request = new AbortController(); reads.set(kind, { request, timer: setTimeout(() => request.abort(new DOMException('读取超时，请重试', 'TimeoutError')), 10000) }); return request }
function owns(kind: Read, request: AbortController) { return active() && reads.get(kind)?.request === request }
function finishRead(kind: Read, request: AbortController) { const read = reads.get(kind); if (read?.request === request) { clearTimeout(read.timer); reads.delete(kind); if (kind === 'list') loading.value = false; if (kind === 'parent') mappingLoading.value = false } }
function readError(request: AbortController, value: unknown) { return request.signal.reason instanceof DOMException && request.signal.reason.name === 'TimeoutError' ? '读取超时，请重试' : readableError(value) }
function stopWrite() {
  if (writeDeadline) clearTimeout(writeDeadline); writeDeadline = null
  if (writeController) { writeController.abort(); writeController = null; if (pending.value && !disposed) { uncertain.value = true; writeError.value = '本地等待已停止，服务器结果尚未确认。请用原请求核对。' } }
  writing.value = false
}
function stopAll() { for (const kind of [...reads.keys()]) stopRead(kind); stopWrite(); confirmationEpoch++; if (confirming) { confirming = false; ElMessageBox.close() } }
async function refresh() {
  if (!active() || !visible.value || !session.allows('models:read')) return
  const own = startRead('list'); loading.value = true
  try { const data = unwrap(await api.GET('/admin/api/v1/public-models', { signal: own.signal, params: { query: applied.value } })); if (owns('list', own) && !own.signal.aborted) { snapshot.value = data; error.value = '' } }
  catch (e) { if (owns('list', own)) error.value = readError(own, e) }
  finally { finishRead('list', own) }
}
function applyFilters() { stopRead('list'); applied.value = { page: 1, q: draft.q.trim() || undefined, project_id: draft.project_id || undefined, task_type: draft.task_type || undefined, status: draft.status || undefined }; snapshot.value = null; error.value = ''; void refresh() }
function resetFilters() { draft.q = ''; draft.project_id = ''; draft.task_type = ''; draft.status = ''; applyFilters() }
function goPage(delta: number) { const next = page.value + delta; if (loading.value || next < 1 || next > 1000) return; stopRead('list'); applied.value = { ...applied.value, page: next }; snapshot.value = null; error.value = ''; void refresh() }
function projectChanged(value: string) { if (form.project_id === value) return; mappingEpoch++; stopRead('parent'); form.project_id = value; scope.value = null; form.workflows = []; form.releases = []; form.presets = []; form.channels = []; presetChoice.value = null }
function workflowChanged(value: string[]) { if (form.workflows[0] === value[0]) return; form.workflows = value; form.releases = []; form.presets = []; presetChoice.value = null }
function releaseChanged(value: string[]) { if (form.releases[0] === value[0]) return; form.releases = value; form.presets = []; presetChoice.value = null }
function presetResolved(value: Option[]) { presetChoice.value = value.find((item): item is Preset => item.kind === 'preset' && item.id === form.presets[0]) ?? null }
async function open(row?: Model) {
  if (blocked.value || !active() || !visible.value) return
  stopRead('parent'); const epoch = ++mappingEpoch
  editing.value = row ?? null; scope.value = null; presetChoice.value = null; formError.value = ''; writeError.value = ''
  form.alias = row?.alias ?? ''; form.project_id = row?.project_id ?? session.info?.scopes[0]?.project_id ?? ''; form.workflows = []; form.releases = []; form.presets = []; form.channels = [...(row?.channel_ids ?? [])]
  drawer.value = true
  if (!row) return
  const own = startRead('parent'); mappingLoading.value = true
  try {
    const result = unwrap(await api.GET('/admin/api/v1/model-mapping-options', { signal: own.signal, params: { query: { kind: 'release', project_id: row.project_id, ids: [row.release_id] } } }))
    if (!owns('parent', own) || own.signal.aborted || epoch !== mappingEpoch || !drawer.value) return
    const release = result.data.find(item => item.kind === 'release' && item.id === row.release_id)
    if (!release || release.kind !== 'release') throw new Error('无法核对当前冻结发布版本，请刷新模型。')
    form.workflows = [release.workflow_id]; form.releases = [release.id]; form.presets = ['current']; presetChoice.value = currentPreset.value ?? null
  } catch (e) { if (owns('parent', own) && epoch === mappingEpoch) formError.value = readError(own, e) }
  finally { finishRead('parent', own) }
}
function clearPending() { if (writing.value) return; pending.value = null; uncertain.value = false; writeError.value = '' }
async function execute(action: Action) {
  if (writing.value || !active() || !visible.value || (pending.value && pending.value !== action)) return
  const wasUnknown = uncertain.value, token = session.info?.csrf_token
  pending.value = action; uncertain.value = false; writeError.value = ''; writing.value = true
  const own = new AbortController(); writeController = own
  writeDeadline = setTimeout(() => own.abort(new DOMException('请求超时', 'TimeoutError')), 10000)
  const headers = { 'Idempotency-Key': action.key }
  try {
    let result: Model | components['schemas']['ValidationAccepted']
    if (action.kind === 'create') result = unwrap(await api.POST('/admin/api/v1/public-models', { signal: own.signal, headers, body: action.body }))
    else if (action.kind === 'patch') result = unwrap(await api.PATCH('/admin/api/v1/public-models/{identifier}', { signal: own.signal, params: { path: { identifier: action.id } }, headers: { ...headers, ...revisionHeaders(action.body.expected_revision) }, body: action.body }))
    else if (action.kind === 'publish') result = unwrap(await api.POST('/admin/api/v1/public-models/{identifier}/actions', { signal: own.signal, params: { path: { identifier: action.id } }, headers: { ...headers, ...revisionHeaders(action.body.expected_revision) }, body: action.body }))
    else result = unwrap(await api.POST('/admin/api/v1/workflow-releases/{identifier}/validate', { signal: own.signal, params: { path: { identifier: action.release } }, headers, body: action.body }))
    if (disposed || writeController !== own || own.signal.aborted || session.info?.csrf_token !== token) return
    pending.value = null; uncertain.value = false; writing.value = false; writeController = null
    if (writeDeadline) clearTimeout(writeDeadline); writeDeadline = null
    if (action.kind === 'validation') accepted.value = result as components['schemas']['ValidationAccepted']
    else { drawer.value = false; ElMessage.success(action.kind === 'publish' ? '模型发布状态已更新' : '模型映射已保存，待验证后可发布'); void refresh() }
  } catch (e) {
    if (disposed || writeController !== own || session.info?.csrf_token !== token) return
    const rejected = e instanceof ApiError && e.status >= 400 && e.status < 500 && e.status !== 408
    if (rejected && !wasUnknown) { pending.value = null; uncertain.value = false; writeError.value = readableError(e) }
    else { uncertain.value = true; writeError.value = '服务器结果尚未确认。请用原请求核对；版本冲突时先查看当前模型配置。' }
  } finally { if (writeController === own) { if (writeDeadline) clearTimeout(writeDeadline); writeDeadline = null; writeController = null; writing.value = false } }
}
function save() {
  if (!canSave.value || !scope.value || !presetChoice.value) return
  const preset = presetChoice.value.defaults
  const defaults = { n: 1, quality: preset.quality, ...(preset.size != null ? { size: preset.size } : {}), ...(preset.seconds != null ? { seconds: preset.seconds } : {}) }
  const body = { release_id: form.releases[0]!, channel_ids: [...form.channels], defaults }
  if (editing.value) void execute({ kind: 'patch', id: editing.value.id, body: { ...body, expected_revision: editing.value.revision }, key: crypto.randomUUID(), description: '编辑模型 ' + editing.value.alias })
  else void execute({ kind: 'create', body: { ...body, alias: form.alias, tenant_id: scope.value.tenant_id, project_id: scope.value.project_id }, key: crypto.randomUUID(), description: '创建模型 ' + form.alias })
}
async function publish(row: Model) {
  if (blocked.value || !active() || !visible.value) return
  const epoch = confirmationEpoch, token = session.info?.csrf_token; confirming = true
  try {
    await ElMessageBox.confirm(row.published ? '下线后不再接收该别名的新任务，已接收任务继续执行。' : '所有映射渠道须已同步 Workflow，并有目标 GPU 的验证产物和运行批准。', row.published ? '下线模型' : '发布模型', { confirmButtonText: '确认', cancelButtonText: '取消' })
    if (epoch !== confirmationEpoch || !active() || !visible.value || session.info?.csrf_token !== token) return
    void execute({ kind: 'publish', id: row.id, body: { action: row.published ? 'unpublish' : 'publish', expected_revision: row.revision }, key: crypto.randomUUID(), description: (row.published ? '下线模型 ' : '发布模型 ') + row.alias })
  } catch (e) { if (e !== 'cancel' && e !== 'close' && active()) writeError.value = readableError(e) }
  finally { confirming = false }
}
function openValidation(row: Model) {
  if (blocked.value || !active() || !visible.value) return
  validationModel.value = row; validationChannels.value = []; validationChannel.value = null; validationError.value = ''; accepted.value = null; writeError.value = ''; validationKey = crypto.randomUUID()
  const input = { ...(row.task_type === 'image_to_3d' ? {} : { prompt: 'Describe your validation sample' }), ...(['image_to_image', 'image_to_3d'].includes(row.task_type) ? { source_image: { asset_id: '' } } : {}), ...(['references_to_image', 'references_to_video'].includes(row.task_type) ? { references: [] } : {}) }
  sample.value = JSON.stringify({ model: row.alias, task_type: row.task_type, input, parameters: { ...row.defaults, n: 1 } }, null, 2); validationDialog.value = true
}
function validateSample() {
  const model = validationModel.value, channel = validationChannel.value
  if (!model || !channel || !valid.validation || blocked.value) return
  try {
    const request = JSON.parse(sample.value) as components['schemas']['JobCreate']
    if (request.model !== model.alias || request.task_type !== model.task_type) throw new Error('样本模型与能力类型必须与当前映射一致')
    const body = { model_id: model.id, expected_model_revision: model.revision, channel_id: channel.id, expected_channel_revision: channel.revision, sample_budget: 1 as const, request }
    void execute({ kind: 'validation', release: model.release_id, body, key: validationKey, description: '验证样本 · ' + model.alias })
  } catch (e) { validationError.value = readableError(e) }
}
function viewValidation() { if (accepted.value) { const id = accepted.value.id; validationDialog.value = false; void router.push({ path: '/jobs', query: { job: id } }) } }
async function visibility() {
  visible.value = false; stopAll()
  if (!active()) return
  const own = startRead('session')
  try { await session.refresh(own.signal); if (owns('session', own) && !own.signal.aborted) { visible.value = true; void refresh() } }
  catch (e) { if (owns('session', own)) error.value = readError(own, e) }
  finally { finishRead('session', own) }
}
watch(drawer, value => { if (!value) { mappingEpoch++; stopRead('parent'); if (pending.value?.kind === 'create' || pending.value?.kind === 'patch') stopWrite() } })
watch(validationDialog, value => { if (!value) { if (pending.value?.kind === 'validation') stopWrite(); sample.value = ''; validationChannels.value = []; validationChannel.value = null } })
watch(() => [session.info?.csrf_token, session.ending] as const, ([token, ending], [old]) => { if (!token || ending || token !== old) { stopAll(); usageDialog.value = false; usageTarget.value = null; pending.value = null; uncertain.value = false; drawer.value = false; validationDialog.value = false; sample.value = ''; snapshot.value = null; form.alias = ''; form.project_id = ''; form.workflows = []; form.releases = []; form.presets = []; form.channels = []; scope.value = null; presetChoice.value = null; error.value = ''; writeError.value = ''; formError.value = '' } })
onMounted(() => { void refresh(); document.addEventListener('visibilitychange', visibility) })
onUnmounted(() => { disposed = true; stopAll(); pending.value = null; sample.value = ''; document.removeEventListener('visibilitychange', visibility) })
</script>

<template>
  <div class="page-heading"><div><p class="eyebrow li-lab">CAPABILITIES / MODELS</p><h1>模型与路由</h1><p class="muted">将公开模型别名映射到冻结 Workflow、规格与渠道。</p></div><div class="toolbar"><GatewayCatalogLauncher/><el-button :icon="Refresh" :loading="loading" @click="refresh">刷新</el-button><el-button v-if="session.allows('models:write')" type="primary" :icon="Plus" :disabled="blocked || !visible" @click="open()">添加模型</el-button></div></div>
  <el-alert v-if="error" :title="error + (snapshot ? ' · 当前展示最后一次成功快照' : '')" type="error" :closable="false"/>
  <ModelRequestStatus v-if="!drawer && !validationDialog" :description="pending?.description ?? null" :uncertain="uncertain" :busy="writing" :active="visible" :error="writeError" @retry="pending && execute(pending)" @clear="clearPending"/>
  <section class="panel li-card model-filters" aria-label="模型目录筛选"><el-form label-position="top" @submit.prevent="applyFilters"><div class="model-filter-grid"><el-form-item label="模型别名搜索"><el-input v-model="draft.q" aria-label="模型别名搜索" maxlength="128" placeholder="按别名字面搜索" clearable/></el-form-item><el-form-item label="筛选项目"><ScopeSelect v-model="draft.project_id" label="筛选项目" :active="visible" clearable allow-inactive/></el-form-item><el-form-item label="能力类型"><el-select v-model="draft.task_type" aria-label="能力类型" clearable placeholder="全部能力"><el-option v-for="(name, id) in taskNames" :key="id" :label="name" :value="id"/></el-select></el-form-item><el-form-item label="发布状态"><el-select v-model="draft.status" aria-label="发布状态" clearable placeholder="全部状态"><el-option label="已发布" value="published"/><el-option label="未发布" value="unpublished"/></el-select></el-form-item></div><div class="toolbar model-filter-actions"><el-button @click="resetFilters">重置筛选</el-button><el-button type="primary" native-type="submit">应用筛选</el-button></div></el-form></section>
  <div class="snapshot-line"><span>快照时间 {{ time(snapshot?.snapshot_at) }}</span><span>当前筛选 {{ snapshot?.total ?? '—' }} 项 · 每页 50 条</span></div>
  <section class="panel li-card"><el-table :data="snapshot?.data ?? []" v-loading="loading" :empty-text="!snapshot ? (loading ? '正在读取模型目录…' : error ? '模型目录读取失败' : '等待目录快照') : '当前筛选没有模型映射'" table-layout="auto"><el-table-column label="公开模型" min-width="230"><template #default="{ row }"><strong>{{ row.alias }}</strong><span class="id-line">{{ row.id }}</span></template></el-table-column><el-table-column label="能力 / 状态" min-width="150"><template #default="{ row }">{{ taskNames[row.task_type] ?? row.task_type }}<span class="id-line">{{ row.published ? '已发布' : '未发布' }}</span></template></el-table-column><el-table-column prop="release_id" label="冻结版本" min-width="320"/><el-table-column label="映射渠道" min-width="110"><template #default="{ row }">{{ row.channel_ids.length }} 个</template></el-table-column><el-table-column label="默认规格" min-width="180"><template #default="{ row }">{{ [row.defaults.quality, row.defaults.size, row.defaults.seconds != null ? row.defaults.seconds + ' 秒' : ''].filter(Boolean).join(' · ') }}</template></el-table-column><el-table-column label="操作" min-width="320"><template #default="{ row }"><el-button text size="small" :disabled="blocked || !visible" @click="usageTarget = row; usageDialog = true">使用说明</el-button><el-button v-if="session.allows('models:write')" text size="small" :disabled="blocked || !visible" @click="open(row)">编辑映射</el-button><el-button v-if="session.allows('workflows:publish')" text size="small" :disabled="blocked || !visible" @click="openValidation(row)">验证样本</el-button><el-button v-if="session.allows('models:publish')" text size="small" :disabled="blocked || !visible" @click="publish(row)">{{ row.published ? '下线' : '发布' }}</el-button></template></el-table-column></el-table><div class="pagination"><el-button size="small" :disabled="page === 1 || loading" @click="goPage(-1)">上一页</el-button><span>第 {{ page }} 页</span><el-button size="small" :disabled="!snapshot?.has_more || loading || page >= 1000" @click="goPage(1)">下一页</el-button></div></section>
  <ModelUsageDialog v-model="usageDialog" :target="usageTarget" :active="visible" @saved="refresh"/>
  <el-drawer v-model="drawer" :title="editing ? '编辑模型映射' : '添加模型映射'" size="min(640px, 100vw)"><el-alert v-if="formError" :title="formError" type="error" :closable="false"/><ModelRequestStatus :description="pending?.description ?? null" :uncertain="uncertain" :busy="writing" :active="visible" :error="writeError" @retry="pending && execute(pending)" @clear="clearPending"/>
    <el-form label-position="top" @submit.prevent="save"><el-form-item label="公开模型别名" required><el-input v-model="form.alias" aria-label="公开模型别名" :disabled="!!editing || blocked || !visible" maxlength="128" placeholder="studio-image-v1"/></el-form-item><el-form-item label="所属项目" required><ScopeSelect :model-value="form.project_id" :active="drawer && visible && !blocked" :disabled="!!editing || blocked || mappingLoading" :allow-inactive="!!editing" @update:model-value="projectChanged" @resolved="scope = $event"/></el-form-item>
      <el-form-item label="Workflow" required><ModelMappingSelect :model-value="form.workflows" kind="workflow" label="Workflow" :project-id="form.project_id" :task-type="editing?.task_type as Filter['task_type']" :active="drawer && visible" :disabled="blocked || mappingLoading" @update:model-value="workflowChanged" @validity="valid.workflow = $event"/></el-form-item>
      <el-form-item label="冻结发布版本" required><ModelMappingSelect :model-value="form.releases" kind="release" label="冻结发布版本" :project-id="form.project_id" :workflow-id="form.workflows[0]" :active="drawer && visible" :disabled="blocked || mappingLoading" @update:model-value="releaseChanged" @validity="valid.release = $event"/></el-form-item>
      <el-form-item label="冻结规格" required><ModelMappingSelect v-model="form.presets" kind="preset" label="冻结规格" :project-id="form.project_id" :release-id="form.releases[0]" :current-preset="currentPreset" :active="drawer && visible" :disabled="blocked || mappingLoading" @resolved="presetResolved" @validity="valid.preset = $event"/><p class="form-help">选择该冻结版本支持的质量、尺寸或时长。音频规格同时显示格式、采样率与声道。</p></el-form-item>
      <el-form-item label="映射渠道" required><ModelMappingSelect v-model="form.channels" kind="channel" label="映射渠道" :project-id="form.project_id" :active="drawer && visible" :disabled="blocked || mappingLoading" multiple @validity="valid.channel = $event"/><p class="form-help">可保存同项目的停用或排空渠道；实际发布和运行仍须同步、验证与批准。</p></el-form-item><div class="drawer-footer"><el-button @click="drawer = false">取消</el-button><el-button native-type="submit" type="primary" :disabled="!canSave" :loading="writing">保存映射</el-button></div></el-form>
  </el-drawer>
  <el-dialog v-model="validationDialog" title="提交 Workflow 验证样本" width="min(740px, 94vw)"><el-alert v-if="validationError" :title="validationError" type="error" :closable="false"/><ModelRequestStatus :description="pending?.description ?? null" :uncertain="uncertain" :busy="writing" :active="visible" :error="writeError" @retry="pending && execute(pending)" @clear="clearPending"/>
    <template v-if="accepted"><el-alert title="验证任务已受理，尚未表示生成成功或运行获批" type="success" :closable="false"/><p class="mono">{{ accepted.id }}</p><el-button v-if="session.allows('jobs:read')" type="primary" @click="viewValidation">查看验证任务</el-button></template>
    <el-form v-else label-position="top" @submit.prevent="validateSample"><p class="form-help">{{ validationModel?.alias }} · {{ validationModel?.release_id }} · 单次样本也会计入资源额度。</p><el-form-item label="验证渠道" required><ModelMappingSelect v-model="validationChannels" kind="channel" label="验证渠道" :project-id="validationModel?.project_id ?? ''" :model-id="validationModel?.id" :active="validationDialog && visible" :disabled="blocked" runnable-only @resolved="validationChannel = $event.find((item): item is Channel => item.kind === 'channel') ?? null" @validity="valid.validation = $event"/><p class="form-help">仅选择该模型实际映射且启用、未排空的渠道，提交时仍核对同步与运行条件。</p></el-form-item><el-form-item label="规范样本请求 JSON" required><el-input v-model="sample" aria-label="规范样本请求 JSON" type="textarea" :rows="14" :disabled="blocked || !visible" spellcheck="false" class="json-editor"/></el-form-item><div class="drawer-footer"><el-button @click="validationDialog = false">取消</el-button><el-button native-type="submit" type="primary" :disabled="blocked || !valid.validation || !visible" :loading="writing">提交单个样本</el-button></div></el-form>
  </el-dialog>
</template>

<style scoped>
.model-filters { padding: 18px 20px; margin-bottom: 20px; }
.model-filter-grid { display: grid; grid-template-columns: minmax(0,1.1fr) minmax(0,1.3fr) minmax(0,.8fr) minmax(0,.8fr); gap: 16px; }
.model-filter-grid :deep(.el-select) { width: 100%; }
.model-filter-actions { justify-content: flex-end; }
@media(max-width: 1000px) { .model-filter-grid { grid-template-columns: minmax(0,1fr) minmax(0,1fr); } }
@media(max-width: 600px) { .model-filter-grid { grid-template-columns: minmax(0,1fr); gap: 0; } }
</style>
