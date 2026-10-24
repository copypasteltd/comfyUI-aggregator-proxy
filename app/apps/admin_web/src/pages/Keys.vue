<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, toRaw, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh, CopyDocument } from '@element-plus/icons-vue'
import { api, ApiError, readableError, revisionHeaders, time, unwrap, type KeyRecord, type KeyPolicy, type ScopeOption } from '../api'
import type { components } from '../generated/api'
import { useSession } from '../session'
import ScopeSelect from '../components/ScopeSelect.vue'
import PolicyDirectorySelect from '../components/PolicyDirectorySelect.vue'
import KeyGenerationSelect from '../components/KeyGenerationSelect.vue'
import KeyRequestStatus from '../components/KeyRequestStatus.vue'

type Action = { key: string; description: string } & (
  { kind: 'create'; body: components['schemas']['KeyCreate'] } |
  { kind: 'patch'; id: string; body: components['schemas']['KeyPatch'] } |
  { kind: 'action'; id: string; body: components['schemas']['KeyAction'] } |
  { kind: 'delete'; id: string; revision: number })
type Read = 'list' | 'detail' | 'session'
const session = useSession(), snapshot = ref<components['schemas']['KeysView'] | null>(null), policy = ref<KeyPolicy | null>(null)
const rows = computed(() => snapshot.value?.data ?? []), defaultPolicy = computed(() => snapshot.value?.default_policy ?? null)
const drawer = ref(false), secretDialog = ref(false), secret = ref(''), error = ref(''), formError = ref(''), loading = ref(false), editing = ref<KeyRecord | null>(null), page = ref(1)
const form = ref({ name: '', project_id: '', expires_at: '' }), networkText = ref(''), scope = ref<ScopeOption | null>(null)
const scopeTenant = ref(''), lastTenant = ref(''), contentNotice = ref(''), projectGrantValid = ref(true), creatorGrantValid = ref(true)
const generationValid = reactive({ models: true, workflow_releases: true, channels: true })
const visible = ref(!document.hidden), loadedAt = ref(0), notice = ref(''), writeError = ref(''), writing = ref(false), pending = ref<Action | null>(null), uncertain = ref(false)
const detail = ref<KeyRecord | null>(null), detailDialog = ref(false), detailLoading = ref(false)
const blocked = computed(() => writing.value || !!pending.value)
const scopeLabels: Record<string, string> = { 'models:read': '查看模型', 'capabilities:read': '查看能力', 'jobs:create': '创建任务', 'jobs:read': '查询任务', 'jobs:cancel': '取消任务', 'images:create': '图片生成', 'videos:create': '视频生成', 'audio:create': '音频生成', 'assets:upload': '上传素材', 'assets:read': '查看素材', 'artifacts:read': '产物元数据', 'content:read': '查看内容', 'content:download': '下载内容', 'content:delete': '删除内容', 'content:sensitive': '查看完整提示词与原始输入' }
const limitLabels: { key: keyof KeyPolicy['limits']; label: string }[] = [{ key: 'rpm', label: '每分钟请求数' }, { key: 'burst', label: '突发请求容量' }, { key: 'daily_jobs', label: '每日接收任务' }, { key: 'unfinished_jobs', label: '未结束任务上限' }, { key: 'generating_jobs', label: '生成并发数' }, { key: 'daily_image_count', label: '每日图片额度' }, { key: 'daily_video_milliseconds', label: '每日视频额度（毫秒）' }, { key: 'daily_audio_milliseconds', label: '每日音频额度（毫秒）' }]
const selections = [{ key: 'models', label: '公开模型别名' }, { key: 'workflow_releases', label: 'Workflow 发布版本' }, { key: 'channels', label: '允许渠道' }] as const
const split = (value: string) => [...new Set(value.split(/[\n,，]/).map(s => s.trim()).filter(Boolean))]
const canSave = computed(() => !!policy.value && !!form.value.name.trim() && !!scope.value && scope.value.project_id === form.value.project_id && selections.every(item => policy.value![item.key].mode === 'all_authorized' || generationValid[item.key]) && (policy.value.content.mode === 'owner_only' || (!!policy.value.content.project_ids.length && projectGrantValid.value)) && (policy.value.content.mode !== 'shared_creator_keys' || (!!policy.value.content.creator_key_ids.length && creatorGrantValid.value)) && !blocked.value && visible.value && session.allows('keys:manage'))
const reads = new Map<Read, { request: AbortController; timer: ReturnType<typeof setTimeout> }>()
let disposed = false, confirmationEpoch = 0, confirming = false, writeController: AbortController | null = null, writeDeadline: ReturnType<typeof setTimeout> | null = null
function active() { return !disposed && !document.hidden && !!session.info && !session.ending }
function stopRead(kind: Read) { const read = reads.get(kind); if (read) { clearTimeout(read.timer); read.request.abort(); reads.delete(kind) } if (kind === 'list') loading.value = false; if (kind === 'detail') detailLoading.value = false }
function startRead(kind: Read) { stopRead(kind); const request = new AbortController(); reads.set(kind, { request, timer: setTimeout(() => request.abort(new DOMException('读取超时，请重试', 'TimeoutError')), 10000) }); return request }
function owns(kind: Read, request: AbortController) { return active() && reads.get(kind)?.request === request }
function finishRead(kind: Read, request: AbortController) { const read = reads.get(kind); if (read?.request === request) { clearTimeout(read.timer); reads.delete(kind); if (kind === 'list') loading.value = false; if (kind === 'detail') detailLoading.value = false } }
function readError(request: AbortController, value: unknown) { return request.signal.reason instanceof DOMException && request.signal.reason.name === 'TimeoutError' ? '读取超时，请重试' : readableError(value) }
function clearSecret() { secretDialog.value = false; secret.value = '' }
function stopWrite() {
  if (writeDeadline) clearTimeout(writeDeadline); writeDeadline = null
  if (writeController) { writeController.abort(); writeController = null; if (pending.value && !disposed) { uncertain.value = true; writeError.value = '本地等待已停止，服务器结果尚未确认。请使用原请求核对。' } }
  writing.value = false
}
function stopAll() { for (const kind of [...reads.keys()]) stopRead(kind); stopWrite(); confirmationEpoch++; if (confirming) { confirming = false; ElMessageBox.close() } }
async function refresh() {
  if (!active() || !visible.value || !session.allows('keys:manage')) return
  const own = startRead('list'), requestedPage = page.value; loading.value = true
  try { const data = unwrap(await api.GET('/admin/api/v1/api-keys', { signal: own.signal, params: { query: { page: requestedPage } } })); if (owns('list', own) && !own.signal.aborted && requestedPage === page.value) { snapshot.value = data; loadedAt.value = Date.now() / 1000; error.value = '' } }
  catch (e) { if (owns('list', own)) error.value = readError(own, e) }
  finally { finishRead('list', own) }
}
function open(row?: KeyRecord) {
  if (!defaultPolicy.value || blocked.value || !active() || !visible.value) return
  editing.value = row ?? null; policy.value = structuredClone(toRaw(row?.policy ?? defaultPolicy.value))
  policy.value.limits.daily_audio_milliseconds ??= { mode: 'bounded', value: 0 }; policy.value.spec_limits.audio_max_milliseconds ??= 0
  form.value = { name: row?.name ?? '', project_id: row?.project_id ?? session.info?.scopes[0]?.project_id ?? '', expires_at: row?.expires_at ? String(row.expires_at) : '' }
  scope.value = null; scopeTenant.value = row?.tenant_id ?? ''; lastTenant.value = scopeTenant.value; contentNotice.value = ''; projectGrantValid.value = !policy.value.content.project_ids.length; creatorGrantValid.value = !policy.value.content.creator_key_ids.length
  networkText.value = policy.value.allowed_cidrs.values.join('\n')
  for (const item of selections) generationValid[item.key] = !policy.value[item.key].selected_ids.length
  formError.value = ''; writeError.value = ''; drawer.value = true
}
function projectChanged(value: string) {
  if (form.value.project_id === value) return
  form.value.project_id = value; scope.value = null; scopeTenant.value = ''
  if (policy.value) for (const item of selections) { policy.value[item.key].selected_ids = []; generationValid[item.key] = true }
  contentNotice.value = '所属项目已变更，请重新选择指定的模型、版本和渠道。'
}
function acceptScope(value: ScopeOption | null) {
  if (!value || value.project_id !== form.value.project_id) { scope.value = null; scopeTenant.value = ''; return }
  if (lastTenant.value && lastTenant.value !== value.tenant_id && policy.value) {
    policy.value.content = { mode: 'owner_only', project_ids: [], creator_key_ids: [] }
    contentNotice.value = '所属租户已变更，共享范围已清空并回到仅自身内容；需要共享时请重新选择。'
  }
  scope.value = value; scopeTenant.value = value.tenant_id; lastTenant.value = value.tenant_id
}
function setLimitMode(key: keyof KeyPolicy['limits'], value: unknown) {
  if (!policy.value) return
  policy.value.limits[key] = value === 'unlimited' ? { mode: 'unlimited', value: null } : { mode: 'bounded', value: 0 }
}
function getLimit(key: keyof KeyPolicy['limits']) { return policy.value?.limits[key] ?? { mode: 'bounded' as const, value: 0 } }
function setLimit(key: keyof KeyPolicy['limits'], value: number | undefined) { if (policy.value) policy.value.limits[key] = { ...getLimit(key), value: value ?? 0 } }
function clearPending() { if (writing.value) return; pending.value = null; uncertain.value = false; writeError.value = '' }
async function execute(action: Action) {
  if (writing.value || !active() || !visible.value || (pending.value && pending.value !== action)) return
  const wasUnknown = uncertain.value, token = session.info?.csrf_token
  pending.value = action; uncertain.value = false; writeError.value = ''; writing.value = true
  const own = new AbortController(); writeController = own
  writeDeadline = setTimeout(() => own.abort(new DOMException('请求超时', 'TimeoutError')), 10000)
  const headers = { 'Idempotency-Key': action.key }
  try {
    let result: components['schemas']['KeyMutation'] | null = null
    if (action.kind === 'create') result = unwrap(await api.POST('/admin/api/v1/api-keys', { signal: own.signal, headers, body: action.body }))
    else if (action.kind === 'patch') unwrap(await api.PATCH('/admin/api/v1/api-keys/{identifier}', { signal: own.signal, params: { path: { identifier: action.id } }, headers: { ...headers, ...revisionHeaders(action.body.expected_revision) }, body: action.body }))
    else if (action.kind === 'action') result = unwrap(await api.POST('/admin/api/v1/api-keys/{identifier}/actions', { signal: own.signal, params: { path: { identifier: action.id } }, headers: { ...headers, ...revisionHeaders(action.body.expected_revision) }, body: action.body }))
    else unwrap(await api.DELETE('/admin/api/v1/api-keys/{identifier}', { signal: own.signal, params: { path: { identifier: action.id }, query: { revision: action.revision } }, headers: { ...headers, ...revisionHeaders(action.revision) } }))
    if (disposed || writeController !== own || own.signal.aborted || session.info?.csrf_token !== token) return
    pending.value = null; uncertain.value = false; writing.value = false; writeController = null
    if (writeDeadline) clearTimeout(writeDeadline); writeDeadline = null
    if (result?.secret && result.secret_available !== false) { secret.value = result.secret; secretDialog.value = true; notice.value = '' }
    else if (action.kind === 'create' || (action.kind === 'action' && action.body.action === 'rotate')) notice.value = '原请求已核对，首次密钥已不可再次显示。请核对逻辑 Key 与当前策略；需要新凭证时另行明确轮换。'
    if (action.kind === 'create' || action.kind === 'patch') drawer.value = false
    ElMessage.success(action.kind === 'delete' ? 'Key 已删除' : 'Key 操作已确认'); void refresh()
  } catch (e) {
    if (disposed || writeController !== own || session.info?.csrf_token !== token) return
    const rejected = e instanceof ApiError && e.status >= 400 && e.status < 500 && e.status !== 408
    if (rejected && !wasUnknown) { pending.value = null; uncertain.value = false; writeError.value = readableError(e) }
    else { uncertain.value = true; writeError.value = '服务器结果尚未确认。请使用原请求核对；遇到版本冲突时查看当前策略，不自动覆盖。' }
  } finally { if (writeController === own) { if (writeDeadline) clearTimeout(writeDeadline); writeDeadline = null; writeController = null; writing.value = false } }
}
function save() {
  if (!canSave.value || !policy.value || !scope.value) return
  try {
    const normalized = structuredClone(toRaw(policy.value))
    for (const item of selections) if (normalized[item.key].mode === 'all_authorized') normalized[item.key].selected_ids = []
    normalized.allowed_cidrs.values = normalized.allowed_cidrs.mode === 'allowlist' ? split(networkText.value) : []
    if (normalized.content.mode === 'owner_only') { normalized.content.project_ids = []; normalized.content.creator_key_ids = [] }
    if (normalized.content.mode === 'project_shared') normalized.content.creator_key_ids = []
    const body = { name: form.value.name.trim(), policy: normalized, expires_at: form.value.expires_at ? Number(form.value.expires_at) : null }
    if (editing.value) void execute({ kind: 'patch', id: editing.value.id, body: { ...body, expected_revision: editing.value.revision }, key: crypto.randomUUID(), description: '编辑 Key ' + editing.value.name })
    else void execute({ kind: 'create', body: { ...body, tenant_id: scope.value.tenant_id, project_id: scope.value.project_id }, key: crypto.randomUUID(), description: '创建 Key ' + body.name })
  } catch (e) { formError.value = readableError(e) }
}
async function action(row: KeyRecord, kind: 'enable' | 'disable' | 'rotate') {
  if (blocked.value || !active() || !visible.value) return
  const epoch = confirmationEpoch, token = session.info?.csrf_token; confirming = true
  try {
    const messages = { enable: '启用后可按当前策略访问业务 API。', disable: '停用会阻止后续访问，已接收任务继续按冻结配置执行。', rotate: '轮换将立即撤销旧凭证。逻辑 Key、任务归属、配额与幂等记录继续保留。' }
    await ElMessageBox.confirm(messages[kind], { enable: '启用 Key', disable: '停用 Key', rotate: '轮换凭证' }[kind], { type: 'warning', confirmButtonText: '确认', cancelButtonText: '取消' })
    if (epoch !== confirmationEpoch || !active() || !visible.value || session.info?.csrf_token !== token) return
    void execute({ kind: 'action', id: row.id, body: { action: kind, expected_revision: row.revision, overlap_seconds: 0 }, key: crypto.randomUUID(), description: { enable: '启用 Key ', disable: '停用 Key ', rotate: '轮换 Key ' }[kind] + row.name })
  } catch (e) { if (e !== 'cancel' && e !== 'close' && active()) writeError.value = readableError(e) }
  finally { confirming = false }
}
async function remove(row: KeyRecord) {
  if (blocked.value || !active() || !visible.value) return
  const epoch = confirmationEpoch, token = session.info?.csrf_token; confirming = true
  try {
    await ElMessageBox.confirm(`删除“${row.name}”会撤销其凭证并保留历史任务与用量。`, '删除 Key', { type: 'warning', confirmButtonText: '确认删除', cancelButtonText: '取消' })
    if (epoch !== confirmationEpoch || !active() || !visible.value || session.info?.csrf_token !== token) return
    void execute({ kind: 'delete', id: row.id, revision: row.revision, key: crypto.randomUUID(), description: '删除 Key ' + row.name })
  } catch (e) { if (e !== 'cancel' && e !== 'close' && active()) writeError.value = readableError(e) }
  finally { confirming = false }
}
async function inspectCurrent() {
  const action = pending.value
  if (!action || action.kind === 'create' || !active() || !visible.value) return
  const own = startRead('detail'); detailLoading.value = true; detail.value = null; detailDialog.value = true
  try { const result = unwrap(await api.GET('/admin/api/v1/api-keys/{identifier}', { signal: own.signal, params: { path: { identifier: action.id } } })); if (owns('detail', own) && !own.signal.aborted && pending.value === action) detail.value = result as KeyRecord }
  catch (e) { if (owns('detail', own)) writeError.value = readError(own, e) }
  finally { finishRead('detail', own) }
}
async function copySecret() { if (!active() || !visible.value || !secret.value) return; try { await navigator.clipboard.writeText(secret.value); if (active()) ElMessage.success('已复制') } catch { if (active()) ElMessage.error('复制失败，请手动选中复制') } }
function goPage(delta: number) { const next = page.value + delta; if (loading.value || next < 1 || next > 1000) return; stopRead('list'); page.value = next; snapshot.value = null; loadedAt.value = 0; error.value = ''; void refresh() }
function releaseForm() { if (drawer.value) return; policy.value = null; editing.value = null; scope.value = null; form.value = { name: '', project_id: '', expires_at: '' }; networkText.value = ''; scopeTenant.value = ''; lastTenant.value = ''; contentNotice.value = ''; formError.value = '' }
async function visibility() {
  visible.value = false; stopAll()
  if (secret.value) notice.value = '页面暂停时已清除首次密钥。需要新凭证时请明确轮换。'
  clearSecret(); detailDialog.value = false; detail.value = null
  if (!active()) return
  const own = startRead('session')
  try { await session.refresh(own.signal); if (owns('session', own) && !own.signal.aborted) { visible.value = true; void refresh() } }
  catch (e) { if (owns('session', own)) error.value = readError(own, e) }
  finally { finishRead('session', own) }
}
watch(drawer, value => { if (!value && (pending.value?.kind === 'create' || pending.value?.kind === 'patch')) stopWrite() })
watch(secretDialog, value => { if (!value) secret.value = '' })
watch(detailDialog, value => { if (!value) { stopRead('detail'); detail.value = null } })
watch(() => [session.info?.csrf_token, session.ending] as const, ([token, ending], [old]) => { if (!token || ending || token !== old) { stopAll(); clearSecret(); pending.value = null; uncertain.value = false; drawer.value = false; releaseForm(); detailDialog.value = false; detail.value = null; snapshot.value = null; loadedAt.value = 0; error.value = ''; writeError.value = ''; notice.value = '' } })
onMounted(() => { void refresh(); document.addEventListener('visibilitychange', visibility) })
onBeforeUnmount(() => { disposed = true; stopAll(); clearSecret(); pending.value = null; drawer.value = false; releaseForm(); detail.value = null; snapshot.value = null; document.removeEventListener('visibilitychange', visibility) })
</script>

<template>
  <div class="page-heading"><div><p class="eyebrow">ACCESS CONTROL / API KEYS</p><h1>API 密钥</h1><p class="muted">为外部生成和内容接口设置独立的权限、规格与额度。</p></div><div class="toolbar"><el-button :icon="Refresh" :loading="loading" :disabled="!visible" @click="refresh">刷新</el-button><el-button type="primary" :icon="Plus" :disabled="!defaultPolicy || blocked || !visible" @click="open()">创建 Key</el-button></div></div>
  <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon/>
  <p v-if="error && loadedAt" class="muted">显示上次成功读取的数据：{{ time(loadedAt) }}。</p>
  <el-alert v-if="notice" :title="notice" type="info" :closable="false"/>
  <KeyRequestStatus v-if="!drawer" :description="pending?.description ?? null" :uncertain="uncertain" :busy="writing" :active="visible" :error="writeError" :can-inspect="!!pending && pending.kind !== 'create'" @retry="pending && execute(pending)" @clear="clearPending" @inspect="inspectCurrent"/>
  <section class="panel"><el-table :data="rows" v-loading="loading" :empty-text="error && !snapshot ? 'Key 列表尚未读取成功，请重试。' : '暂无业务 API Key。创建后可分配不同的能力与内容访问权限。'" table-layout="auto"><el-table-column label="名称 / 逻辑 ID" min-width="235"><template #default="{ row }"><strong>{{ row.name }}</strong><span class="id-line">{{ row.id }}</span><small v-if="row.issuer !== 'local'">来源：{{ row.issuer }}</small></template></el-table-column><el-table-column label="状态" min-width="90"><template #default="{ row }"><el-tag :type="row.deleted_at ? 'info' : row.enabled ? 'success' : 'warning'" size="small">{{ row.deleted_at ? '已删除' : row.enabled ? '已启用' : '已停用' }}</el-tag></template></el-table-column><el-table-column label="权限数量" min-width="90"><template #default="{ row }">{{ row.policy.scopes.length }}</template></el-table-column><el-table-column label="过期时间" min-width="175"><template #default="{ row }">{{ row.expires_at ? time(row.expires_at) : '未设置' }}</template></el-table-column><el-table-column label="操作" min-width="300" fixed="right"><template #default="{ row }"><div class="actions"><router-link v-if="session.allows('keys:usage')" :to="'/keys/' + row.id + '/access'" class="key-access-link">访问记录</router-link><template v-if="!row.deleted_at"><el-button text size="small" :disabled="blocked || !visible" @click="open(row)">策略</el-button><el-button text size="small" :disabled="blocked || !visible" @click="action(row, row.enabled ? 'disable' : 'enable')">{{ row.enabled ? '停用' : '启用' }}</el-button><el-button v-if="row.issuer === 'local'" text size="small" :disabled="blocked || !visible" @click="action(row, 'rotate')">轮换</el-button><el-button text type="danger" size="small" :disabled="blocked || !visible" @click="remove(row)">删除</el-button></template></div></template></el-table-column></el-table><div class="pagination"><el-button size="small" :disabled="page === 1 || loading || !visible" @click="goPage(-1)">上一页</el-button><span>第 {{ page }} 页</span><el-button size="small" :disabled="!snapshot?.has_more || page >= 1000 || loading || !visible" @click="goPage(1)">下一页</el-button></div></section>
  <el-drawer v-model="drawer" :title="editing ? '编辑 Key 策略' : '创建业务 API Key'" size="min(700px, 100vw)" @closed="releaseForm"><el-alert v-if="formError" :title="formError" type="error" :closable="false" style="margin-bottom: 16px"/><KeyRequestStatus :description="pending?.description ?? null" :uncertain="uncertain" :busy="writing" :active="visible" :error="writeError" :can-inspect="!!pending && pending.kind !== 'create'" @retry="pending && execute(pending)" @clear="clearPending" @inspect="inspectCurrent"/><el-form v-if="policy" label-position="top" :disabled="blocked || !visible" @submit.prevent="save"><div class="policy-grid"><el-form-item label="名称" required><el-input v-model="form.name" aria-label="Key 名称" maxlength="128"/></el-form-item><el-form-item label="所属项目" required><ScopeSelect :model-value="form.project_id" :disabled="!!editing || blocked || !visible" :allow-inactive="!!editing" :active="drawer && visible && !blocked" class="full" @update:model-value="projectChanged" @resolved="acceptScope"/></el-form-item></div><el-form-item label="过期时间（可选）"><el-date-picker v-model="form.expires_at" type="datetime" value-format="X" placeholder="留空表示不自动过期" class="full"/></el-form-item><section class="policy-block"><h3>接口权限</h3><el-checkbox-group v-model="policy.scopes"><el-checkbox v-for="(label, value) in scopeLabels" :key="value" :value="value">{{ label }}</el-checkbox></el-checkbox-group><p class="form-help">完整提示词与原始输入需单独授予敏感内容权限。</p></section>
  <section class="policy-block"><h3>可用能力与渠道</h3><el-form-item v-for="item in selections" :key="item.key" :label="item.label"><el-select v-model="policy[item.key].mode" :aria-label="item.label + '授权方式'" class="full"><el-option value="selected" label="仅指定项目内资源"/><el-option value="all_authorized" label="项目内全部已授权资源"/></el-select><KeyGenerationSelect v-if="policy[item.key].mode === 'selected'" v-model="policy[item.key].selected_ids" :kind="item.key" :label="item.label" :project-id="form.project_id" :active="drawer && visible" :disabled="blocked" @validity="generationValid[item.key] = $event"/></el-form-item></section>
  <section class="policy-block"><h3>请求与资源额度</h3><div class="policy-grid"><el-form-item v-for="item in limitLabels" :key="item.key" :label="item.label"><el-select :model-value="getLimit(item.key).mode" @change="(value: unknown) => setLimitMode(item.key, value)"><el-option value="bounded" label="设置上限"/><el-option value="unlimited" label="不设此项上限"/></el-select><el-input-number v-if="getLimit(item.key).mode === 'bounded'" :model-value="getLimit(item.key).value ?? undefined" :min="0" :precision="0" controls-position="right" class="full" style="margin-top: 8px" @update:model-value="(value: number | undefined) => setLimit(item.key, value)"/></el-form-item></div></section>
  <section class="policy-block"><h3>生成规格</h3><div class="policy-grid"><el-form-item label="图片最大像素数"><el-input-number v-model="policy.spec_limits.image_max_pixels" :min="0" :precision="0" controls-position="right" class="full"/></el-form-item><el-form-item label="视频最大像素数"><el-input-number v-model="policy.spec_limits.video_max_pixels" :min="0" :precision="0" controls-position="right" class="full"/></el-form-item><el-form-item label="单段视频最长时长（毫秒）"><el-input-number v-model="policy.spec_limits.video_max_milliseconds" :min="0" :precision="0" controls-position="right" class="full"/></el-form-item><el-form-item label="音频最长时长（毫秒）"><el-input-number v-model="policy.spec_limits.audio_max_milliseconds" :min="0" :max="600000" :precision="0" controls-position="right" class="full"/></el-form-item><el-form-item label="最多参考图数量"><el-input-number v-model="policy.spec_limits.max_references" :min="0" :max="4" :precision="0" controls-position="right" class="full"/></el-form-item></div><el-form-item label="允许质量档"><el-checkbox-group v-model="policy.spec_limits.allowed_qualities"><el-checkbox value="draft">草稿</el-checkbox><el-checkbox value="standard">标准</el-checkbox><el-checkbox value="high">高质量</el-checkbox></el-checkbox-group></el-form-item></section>
  <section class="policy-block"><h3>内容访问范围</h3><el-alert v-if="contentNotice" :title="contentNotice" type="info" :closable="false"/><p class="form-help">仅自身内容不会授权其他创建者；指定项目共享包含所选项目全部创建者；指定创建者共享取所选项目与创建者的交集，保留当前 Key 自己的内容。</p><el-form-item label="共享方式"><el-select v-model="policy.content.mode" class="full"><el-option value="owner_only" label="仅本 Key 创建的内容"/><el-option value="project_shared" label="指定的同租户项目共享内容"/><el-option value="shared_creator_keys" label="指定项目与创建者 Key 的内容"/></el-select></el-form-item><el-form-item v-if="policy.content.mode !== 'owner_only'" label="允许共享的项目"><PolicyDirectorySelect v-model="policy.content.project_ids" :tenant-id="scopeTenant" kind="project" :active="drawer && visible && !blocked" :disabled="blocked" @validity="projectGrantValid = $event"/></el-form-item><el-form-item v-if="policy.content.mode === 'shared_creator_keys'" label="允许的创建者 Key"><PolicyDirectorySelect v-model="policy.content.creator_key_ids" :tenant-id="scopeTenant" kind="creator_key" :project-ids="policy.content.project_ids" :active="drawer && visible && !blocked" :disabled="blocked" @validity="creatorGrantValid = $event"/></el-form-item><el-form-item label="下载方式"><el-select v-model="policy.download.mode" class="full"><el-option value="authenticated_proxy" label="每次鉴权后代理下载"/><el-option value="signed_url" label="允许限时签名链接"/></el-select></el-form-item><el-form-item v-if="policy.download.mode === 'signed_url'" label="签名链接最长有效秒数"><el-input-number v-model="policy.download.signed_url_max_seconds" :min="1" :max="900" :precision="0"/><p class="form-help">已签发链接在到期前可能继续有效。</p></el-form-item></section>
  <section class="policy-block"><h3>来源 IP 限制</h3><el-select v-model="policy.allowed_cidrs.mode" class="full"><el-option value="any" label="不限制来源 IP"/><el-option value="allowlist" label="仅允许指定 CIDR"/></el-select><el-input v-if="policy.allowed_cidrs.mode === 'allowlist'" v-model="networkText" type="textarea" :rows="3" placeholder="每行一个网络地址，例如 192.168.1.0/24" style="margin-top: 10px"/></section><div class="drawer-footer"><el-button @click="drawer = false">取消</el-button><el-button native-type="submit" type="primary" :disabled="!canSave" :loading="writing">{{ editing ? '保存策略' : '创建 Key' }}</el-button></div></el-form></el-drawer>
  <el-dialog v-model="secretDialog" title="请保存 API Key" width="min(560px, 94vw)" :close-on-click-modal="false" @closed="clearSecret"><p class="muted">此密钥仅显示一次。关闭窗口后可通过轮换获得新凭证。</p><div class="secret-box" aria-label="新 API Key">{{ secret }}</div><template #footer><el-button :icon="CopyDocument" :disabled="!visible || !secret" @click="copySecret">复制密钥</el-button><el-button type="primary" @click="secretDialog = false">已保存，关闭</el-button></template></el-dialog>
  <el-dialog v-model="detailDialog" title="当前 Key 策略（只读）" width="min(700px, 94vw)"><div v-loading="detailLoading"><template v-if="detail"><p><strong>{{ detail.name }}</strong> · 当前版本 {{ detail.revision }} · {{ detail.deleted_at ? '已删除' : detail.enabled ? '已启用' : '已停用' }}</p><p class="id-line">{{ detail.id }} · {{ detail.project_id }}</p><pre class="key-policy-preview">{{ JSON.stringify(detail.policy, null, 2) }}</pre></template><p v-else class="muted">{{ detailLoading ? '正在核对当前策略…' : '无法读取当前策略，请关闭后重试。' }}</p></div></el-dialog>
</template>

<style scoped>
.key-policy-preview { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 65vh; overflow-y: auto; padding: 14px; background: #f5f8fa; font-size: 12px; }
</style>
