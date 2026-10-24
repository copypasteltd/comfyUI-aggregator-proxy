<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, ApiError, readableError, revisionHeaders, time, unwrap } from '../api'
import type { components } from '../generated/api'
import { catalogDiff, catalogRows, modelRow, publicationRules, taskNames, type DraftCatalogRow } from '../gatewayCatalog'
import { useSession } from '../session'
import ScopeSelect from './ScopeSelect.vue'

type State = components['schemas']['CatalogState']
type Release = components['schemas']['CatalogReleaseView']
type Model = components['schemas']['ModelView']
type Action = { key: string; gateway: string; description: string } & (
  { kind: 'publish'; body: components['schemas']['CatalogPublication'] } |
  { kind: 'replay'; body: components['schemas']['CatalogReplay'] })
const props = defineProps<{ modelValue: boolean; gateway: Pick<components['schemas']['GatewayView'], 'id' | 'name' | 'enabled'> | null }>()
const emit = defineEmits<{ 'update:modelValue': [value: boolean] }>()
const session = useSession(), state = ref<State | null>(null), tab = ref('status'), error = ref(''), writeError = ref(''), formError = ref('')
const visible = ref(!document.hidden), loading = ref(false), writing = ref(false), pending = ref<Action | null>(null), uncertain = ref(false)
const history = ref<components['schemas']['Page_CatalogReleaseSummary_'] | null>(null), historyPage = ref(1), historyLoading = ref(false), detail = ref<Release | null>(null)
const editing = ref(false), baseRevision = ref(0), instance = ref(''), rows = ref<DraftCatalogRow[]>([])
const choices = ref<components['schemas']['ModelCatalogPage'] | null>(null), choiceLoading = ref(false), choicePage = ref(1), filter = reactive({ q: '', project: '' })
const active = computed(() => props.modelValue && visible.value && !!session.info && !session.ending)
const blocked = computed(() => writing.value || !!pending.value)
const canPublish = computed(() => active.value && session.allows('models:publish'))
const changedBase = computed(() => editing.value && state.value !== null && state.value.revision !== baseRevision.value)
const statusNames: Record<State['status'], string> = { legacy: '使用启动配置', pending: '等待 Provider 确认', confirmed: '已确认', rejected: 'Provider 已拒绝', stale: '模型绑定已变化' }
const actionNames = { publish: '发布', retry: '重试', rollback: '回滚' }
const errors: Record<string, string> = { unsupported_configuration: '配置或绑定不受支持', account_unavailable: '用量账户不可用', group_unavailable: '分组不可用', storage_unavailable: 'Provider 持久存储不可用', apply_failed: 'Provider 应用失败' }
const diff = computed(() => {
  try { return { rows: catalogDiff(catalogRows(state.value?.confirmed ?? null), catalogRows(state.value?.desired ?? null)), error: '' } }
  catch (e) { return { rows: [], error: readableError(e) } }
})
type Read = 'state' | 'history' | 'detail' | 'choices' | 'session'
const reads = new Map<Read, { controller: AbortController; timer: ReturnType<typeof setTimeout> }>()
let writeController: AbortController | null = null, writeTimer: ReturnType<typeof setTimeout> | null = null, poll: ReturnType<typeof setInterval> | null = null
let disposed = false, confirmation = 0, confirming = false
function stopRead(kind: Read) {
  const entry = reads.get(kind)
  if (entry) { clearTimeout(entry.timer); entry.controller.abort(); reads.delete(kind) }
  if (kind === 'state') loading.value = false
  if (kind === 'history') historyLoading.value = false
  if (kind === 'choices') choiceLoading.value = false
}
function read(kind: Read) {
  stopRead(kind); const controller = new AbortController()
  reads.set(kind, { controller, timer: setTimeout(() => controller.abort(new DOMException('读取超时，请重试', 'TimeoutError')), 10000) })
  return controller
}
function owns(kind: Read, own: AbortController) { return !disposed && active.value && reads.get(kind)?.controller === own }
function finish(kind: Read, own: AbortController) { if (reads.get(kind)?.controller === own) { clearTimeout(reads.get(kind)?.timer); reads.delete(kind); if (kind === 'state') loading.value = false; if (kind === 'history') historyLoading.value = false; if (kind === 'choices') choiceLoading.value = false } }
function readError(own: AbortController, value: unknown) { return own.signal.reason instanceof DOMException && own.signal.reason.name === 'TimeoutError' ? '读取超时，请重试' : readableError(value) }
function stopWrite() {
  if (writeTimer) clearTimeout(writeTimer); writeTimer = null
  if (writeController) { writeController.abort(); writeController = null; if (pending.value) { uncertain.value = true; writeError.value = '已停止本地等待，服务器结果尚未确认。请使用原请求核对。' } }
  writing.value = false
}
function stopAll() { for (const kind of [...reads.keys()]) stopRead(kind); stopWrite(); confirmation++; if (confirming) { confirming = false; ElMessageBox.close() } }
function clearData() { state.value = null; history.value = null; detail.value = null; choices.value = null; rows.value = []; editing.value = false; instance.value = ''; baseRevision.value = 0; filter.q = ''; filter.project = ''; historyPage.value = 1; choicePage.value = 1; pending.value = null; uncertain.value = false; error.value = ''; writeError.value = ''; formError.value = ''; tab.value = 'status' }
async function refresh() {
  if (!active.value || !props.gateway || !session.allows('models:read')) return
  const own = read('state'); loading.value = true
  try {
    const data = unwrap(await api.GET('/admin/api/v1/gateways/{identifier}/catalog', { signal: own.signal, params: { path: { identifier: props.gateway.id } } }))
    if (owns('state', own) && !own.signal.aborted) { state.value = data; error.value = '' }
  } catch (e) { if (owns('state', own)) error.value = readError(own, e) }
  finally { finish('state', own) }
}
async function loadHistory(delta = 0) {
  if (!active.value || !props.gateway || historyLoading.value || historyPage.value + delta < 1) return
  historyPage.value += delta; const own = read('history'); historyLoading.value = true; history.value = null
  try { const data = unwrap(await api.GET('/admin/api/v1/gateways/{identifier}/catalog/releases', { signal: own.signal, params: { path: { identifier: props.gateway.id }, query: { page: historyPage.value } } })); if (owns('history', own) && !own.signal.aborted) { history.value = data; error.value = '' } }
  catch (e) { if (owns('history', own)) error.value = readError(own, e) }
  finally { finish('history', own) }
}
async function showRelease(number: number) {
  if (!active.value || !props.gateway) return
  const own = read('detail'); detail.value = null
  try { const data = unwrap(await api.GET('/admin/api/v1/gateways/{identifier}/catalog/releases/{number}', { signal: own.signal, params: { path: { identifier: props.gateway.id, number } } })); if (owns('detail', own) && !own.signal.aborted) detail.value = data }
  catch (e) { if (owns('detail', own)) error.value = readError(own, e) }
  finally { finish('detail', own) }
}
async function loadChoices(reset = false, delta = 0) {
  if (!canPublish.value || !editing.value || choicePage.value + delta < 1) return
  choicePage.value = reset ? 1 : choicePage.value + delta; const own = read('choices'); choiceLoading.value = true; choices.value = null
  try {
    const data = unwrap(await api.GET('/admin/api/v1/public-models', { signal: own.signal, params: { query: { page: choicePage.value, status: 'published', q: filter.q.trim() || undefined, project_id: filter.project || undefined } } }))
    if (owns('choices', own) && !own.signal.aborted) choices.value = data
  } catch (e) { if (owns('choices', own)) formError.value = readError(own, e) }
  finally { finish('choices', own) }
}
async function confirm(message: string, title: string) {
  if (!active.value) return false
  const own = confirmation, token = session.info?.csrf_token; confirming = true
  try { await ElMessageBox.confirm(message, title, { type: 'warning', confirmButtonText: '确认', cancelButtonText: '取消', closeOnClickModal: false }); return own === confirmation && active.value && session.info?.csrf_token === token }
  catch { return false }
  finally { if (own === confirmation) confirming = false }
}
async function edit(release?: Release | null) {
  if (!canPublish.value || blocked.value || !state.value) return
  if (editing.value && !await confirm('重新载入将丢弃尚未发布的编辑。', '重新载入目录')) return
  try {
    const source = release === undefined ? state.value.desired ?? state.value.confirmed : release
    rows.value = catalogRows(source).map(row => ({ ...row, rule: structuredClone(row.rule), groups: row.rule.group_ids.join('\n') }))
    baseRevision.value = state.value.revision; instance.value = state.value.instance_id ?? ''; editing.value = true; formError.value = ''; writeError.value = ''; tab.value = 'edit'; choicePage.value = 1; void loadChoices()
  } catch (e) { formError.value = readableError(e) }
}
function add(model: Model) {
  if (!canPublish.value || blocked.value || !model.published) return
  try {
    const next = modelRow(model), existing = rows.value.find(row => row.alias === model.alias)
    if (!existing) { if (rows.value.length >= 256) throw new Error('最多 256 个模型别名'); rows.value.push(next) }
    else {
      if (existing.task !== next.task || existing.defaults !== next.defaults) throw new Error('同名模型的能力和默认规格必须相同，请先在模型页处理差异')
      const binding = existing.rule.model_bindings.findIndex(item => item.model_id === model.id)
      if (binding >= 0) { existing.rule.model_bindings[binding] = next.rule.model_bindings[0]!; existing.bindings[binding] = next.bindings[0]! }
      else { if (existing.bindings.length >= 64) throw new Error('同名模型最多 64 个项目绑定'); existing.rule.model_bindings.push(next.rule.model_bindings[0]!); existing.bindings.push(next.bindings[0]!) }
    }
    formError.value = ''
  } catch (e) { formError.value = readableError(e) }
}
function removeBinding(index: number, binding: number) { if (!canPublish.value || blocked.value) return; rows.value[index]!.bindings.splice(binding, 1); rows.value[index]!.rule.model_bindings.splice(binding, 1); if (!rows.value[index]!.bindings.length) rows.value.splice(index, 1) }
async function publish() {
  if (!canPublish.value || blocked.value || !state.value || !props.gateway || changedBase.value) return
  try {
    if (!/^[a-z0-9][a-z0-9_-]{1,63}$/.test(instance.value)) throw new Error('实例标识须为 2～64 位小写字母、数字、下划线或连字符')
    const body = { expected_revision: baseRevision.value, instance_id: instance.value, models: publicationRules(rows.value) }
    if (!await confirm(body.models.length ? '将发布新的期望目录，等待 Provider 确认后用于新请求。上一确认配置和已有任务保留。' : '空目录确认后将下架此网关全部模型的新请求。已有任务仍可恢复、查询与交付。', body.models.length ? '发布期望目录' : '下架全部模型')) return
    void execute({ kind: 'publish', body, gateway: props.gateway.id, key: crypto.randomUUID(), description: `发布期望目录 · 基线 v${body.expected_revision} · ${body.models.length} 个别名` })
  } catch (e) { formError.value = readableError(e) }
}
async function replay(action: 'retry' | 'rollback', source: Release) {
  if (!canPublish.value || blocked.value || !state.value || !props.gateway || !state.value.revision) return
  const body = { action, expected_revision: state.value.revision, source_revision: source.revision }
  if (!await confirm(`${actionNames[action]} v${source.revision} 会按当前模型事实验证并创建新版本，等待 Provider 确认。旧任务保留原版本。`, actionNames[action] + '为新版本')) return
  void execute({ kind: 'replay', body, gateway: props.gateway.id, key: crypto.randomUUID(), description: `${actionNames[action]} v${source.revision} · 基线 v${body.expected_revision}` })
}
async function execute(action: Action) {
  if (!canPublish.value || writing.value || pending.value && pending.value.key !== action.key) return
  const wasUnknown = uncertain.value, token = session.info?.csrf_token
  pending.value = action; uncertain.value = false; writing.value = true; writeError.value = ''
  const own = new AbortController(); writeController = own; writeTimer = setTimeout(() => own.abort(), 10000)
  const headers = { ...revisionHeaders(action.body.expected_revision), 'Idempotency-Key': action.key }
  try {
    const options = { params: { path: { identifier: action.gateway } }, signal: own.signal, headers }
    const result = action.kind === 'publish'
      ? unwrap(await api.POST('/admin/api/v1/gateways/{identifier}/catalog/releases', { ...options, body: action.body }))
      : unwrap(await api.POST('/admin/api/v1/gateways/{identifier}/catalog/replay', { ...options, body: action.body }))
    if (disposed || writeController !== own || own.signal.aborted || session.info?.csrf_token !== token) return
    pending.value = null; uncertain.value = false; editing.value = false; rows.value = []; choices.value = null; detail.value = result; tab.value = 'status'; ElMessage.success(`期望目录 v${result.revision} 已保存，等待 Provider 确认`); void refresh()
  } catch (e) {
    if (disposed || writeController !== own || session.info?.csrf_token !== token) return
    if (e instanceof ApiError && e.status >= 400 && e.status < 500 && e.status !== 408 && !wasUnknown) { pending.value = null; uncertain.value = false; writeError.value = readableError(e); void refresh() }
    else { uncertain.value = true; writeError.value = '服务器结果尚未确认。请保留原请求核对；不要另发重复发布。' }
  } finally { if (writeController === own) { if (writeTimer) clearTimeout(writeTimer); writeTimer = null; writeController = null; writing.value = false } }
}
async function clearPending() { if (writing.value || !pending.value) return; if (await confirm('此操作只清除本地请求记录，不撤销服务器操作。请先检查当前版本和历史，避免重复发布。', '关闭本地记录')) { pending.value = null; uncertain.value = false; writeError.value = ''; void refresh() } }
async function beforeClose(done: () => void) {
  if (blocked.value) { ElMessage.warning('请先核对请求结果，或明确关闭本地记录后再离开'); return }
  if (editing.value && !await confirm('关闭将丢弃尚未发布的编辑。', '关闭目录管理')) return
  done()
}
async function visibility() {
  visible.value = false; stopAll()
  if (document.hidden || !props.modelValue || !session.info || session.ending) return
  const own = read('session')
  try { await session.refresh(own.signal); if (!disposed && reads.get('session')?.controller === own && !own.signal.aborted && session.info && !session.ending) { visible.value = true; void refresh(); if (tab.value === 'history') void loadHistory(); if (tab.value === 'edit') void loadChoices() } }
  catch (e) { if (reads.get('session')?.controller === own && !disposed) error.value = readError(own, e) }
  finally { finish('session', own) }
}
watch(() => [props.modelValue, props.gateway?.id] as const, ([open], previous) => { stopAll(); clearData(); if (open) { visible.value = !document.hidden; void refresh() } }, { immediate: true })
watch(tab, value => { stopRead('detail'); detail.value = null; if (value === 'history') void loadHistory(); else stopRead('history'); if (value === 'edit') void loadChoices(); else stopRead('choices') })
watch(() => [session.info?.csrf_token, session.ending] as const, ([token, ending], [old]) => { if (!token || ending || token !== old) { stopAll(); clearData(); emit('update:modelValue', false) } })
onBeforeRouteLeave(async () => { if (blocked.value) { ElMessage.warning('请先核对目录发布请求，或明确关闭本地记录'); return false } if (props.modelValue && editing.value) return confirm('离开将丢弃尚未发布的编辑。', '离开目录管理'); return true })
onMounted(() => { document.addEventListener('visibilitychange', visibility); poll = setInterval(() => { if (active.value && !loading.value) void refresh() }, 10000) })
onUnmounted(() => { disposed = true; stopAll(); clearData(); if (poll) clearInterval(poll); document.removeEventListener('visibilitychange', visibility) })
</script>

<template>
  <el-drawer :model-value="modelValue" title="网关模型目录" size="min(1120px, 100vw)" class="gateway-catalog" :before-close="beforeClose" :close-on-click-modal="false" @update:model-value="emit('update:modelValue', $event)">
    <div class="catalog-heading"><div><p class="eyebrow">GATEWAY / CATALOG</p><h2>{{ gateway?.name }}</h2><p class="muted mono">{{ gateway?.id }}</p></div><el-button :loading="loading" :disabled="!active" @click="refresh">刷新目录状态</el-button></div>
    <el-alert v-if="error" :title="error + (state ? ' · 展示最后一次成功快照' : '')" type="error" :closable="false"/>
    <el-alert v-if="writeError" :title="writeError" type="error" :closable="false"/>
    <section v-if="pending" class="catalog-request" aria-label="目录请求核对" aria-live="polite"><strong>{{ writing ? '正在保存期望目录' : '发布结果待核对' }}</strong><p>{{ pending.description }}</p><p class="form-help">保留原正文、版本和幂等键。关闭本地记录不会撤销服务器操作。</p><div class="catalog-toolbar"><el-button type="primary" :disabled="!uncertain || writing || !active" @click="execute(pending)">使用原请求核对</el-button><el-button :disabled="writing || !active" @click="clearPending">关闭本地记录</el-button></div></section>
    <template v-if="state">
      <div class="catalog-state-line" aria-live="polite"><el-tag :type="state.status === 'confirmed' ? 'success' : state.status === 'rejected' || state.status === 'stale' ? 'danger' : 'warning'">{{ statusNames[state.status] }}</el-tag><span class="muted">{{ state.managed ? '已启用目录管理' : '尚未首次确认，Provider 仍可使用启动配置' }}</span></div>
      <div class="catalog-versions"><section><span>期望版本</span><strong>{{ state.desired ? 'v' + state.desired.revision : '尚未发布' }}</strong><p class="muted mono">{{ state.desired?.digest ?? '—' }}</p><p class="form-help">{{ state.desired ? time(state.desired.created_at) : '发布后等待 Provider 应用与确认' }}</p></section><section><span>已确认版本</span><strong>{{ state.confirmed ? 'v' + state.confirmed.revision : '启动配置' }}</strong><p class="muted mono">{{ state.confirmed?.digest ?? '—' }}</p><p class="form-help">{{ state.confirmed_stale ? '绑定已变化，新任务准入会重新核验' : state.confirmed ? 'Provider 声明已应用；实际健康需另行观测' : '首次确认后不会自动回退启动配置' }}</p></section></div>
      <el-alert v-if="state.desired_stale || state.confirmed_stale" title="模型绑定与发布时快照已变化。请核对模型、渠道和规格后创建新版本。" type="warning" :closable="false"/>
      <p v-if="state.last_receipt" class="form-help">最新回执：v{{ state.last_receipt.revision }} · {{ state.last_receipt.status === 'applied' ? '已应用' : '已拒绝' }}{{ state.last_receipt.error_code ? ' · ' + errors[state.last_receipt.error_code] : '' }} · {{ time(state.last_receipt.observed_at) }}</p>
      <el-tabs v-model="tab" aria-label="目录管理视图">
        <el-tab-pane label="映射与差异" name="status"><div class="catalog-toolbar"><el-button v-if="session.allows('models:publish')" type="primary" :disabled="blocked || !active" @click="edit()">编辑期望目录</el-button><el-button v-if="state.desired" :disabled="!active" @click="showRelease(state.desired.revision)">查看期望版本 JSON</el-button><el-button v-if="state.confirmed" :disabled="!active" @click="showRelease(state.confirmed.revision)">查看已确认 JSON</el-button></div><p class="form-help">比较已确认与期望目录。发布新版本不改变已有任务的 Workflow、目标和价格。</p><el-alert v-if="diff.error" :title="diff.error" type="error" :closable="false"/><el-table :data="diff.rows" table-layout="auto" empty-text="尚无目录映射"><el-table-column prop="alias" label="模型别名" min-width="180"/><el-table-column prop="change" label="差异" width="80"/><el-table-column label="能力 / 规格" min-width="210"><template #default="{ row }"><span>{{ taskNames[(row.next ?? row.previous).task] }}</span><span class="id-line">{{ (row.next ?? row.previous).defaults }}</span></template></el-table-column><el-table-column label="确认 → 期望单价" min-width="170"><template #default="{ row }">{{ row.previous?.rule.unit_price ?? '—' }} → {{ row.next?.rule.unit_price ?? '—' }}</template></el-table-column><el-table-column label="期望映射" min-width="190"><template #default="{ row }"><span>{{ row.next?.rule.price_version ?? '已移除' }}</span><span class="id-line">账户 {{ row.next?.rule.usage_account_id ?? '—' }} · {{ row.next?.bindings.length ?? 0 }} 个项目绑定</span><span class="id-line">分组 {{ row.next?.rule.group_ids.join(', ') ?? '—' }}</span></template></el-table-column></el-table></el-tab-pane>
        <el-tab-pane label="发布历史" name="history"><el-table :data="history?.data ?? []" v-loading="historyLoading" table-layout="auto" empty-text="暂无发布历史"><el-table-column label="版本 / 动作" width="145"><template #default="{ row }">v{{ row.revision }} · {{ actionNames[row.action as keyof typeof actionNames] }}<span v-if="row.source_revision" class="id-line">来源 v{{ row.source_revision }}</span></template></el-table-column><el-table-column label="摘要" min-width="240"><template #default="{ row }"><span class="mono">{{ row.digest }}</span></template></el-table-column><el-table-column label="时间" min-width="175"><template #default="{ row }">{{ time(row.created_at) }}</template></el-table-column><el-table-column label="操作" width="100"><template #default="{ row }"><el-button link :disabled="!active" @click="showRelease(row.revision)">查看版本</el-button></template></el-table-column></el-table><div class="catalog-pagination"><el-button :disabled="historyPage === 1 || historyLoading || !active" @click="loadHistory(-1)">上一页</el-button><span>第 {{ historyPage }} 页</span><el-button :disabled="!history?.has_more || historyLoading || !active" @click="loadHistory(1)">下一页</el-button></div></el-tab-pane>
        <el-tab-pane v-if="session.allows('models:publish') && editing" label="编辑发布" name="edit"><el-alert v-if="changedBase" title="目录版本已被其他操作更新。本次编辑基线保留不变，请核对后重新载入。" type="warning" :closable="false"/><el-alert v-if="formError" :title="formError" type="error" :closable="false"/><div class="catalog-toolbar"><strong>编辑基线 v{{ baseRevision }}</strong><el-button :disabled="blocked || !active" @click="edit()">重新载入期望目录</el-button></div><el-form label-position="top" @submit.prevent="publish"><el-form-item label="Sub2API 逻辑实例标识"><el-input v-model="instance" aria-label="Sub2API 逻辑实例标识" maxlength="64" :disabled="!!state.instance_id || blocked || !active" placeholder="与 Provider 配置的 instance 一致"/></el-form-item><p class="form-help">账户与分组填写目标 Sub2API 中已存在的 ID；用量账户须为不可调度的 OpenAI 账户。此处不填写 API Key。</p>
          <section class="catalog-picker" aria-label="选择已发布模型"><h3>选择已发布模型</h3><div class="catalog-filter"><el-form-item label="模型别名搜索"><el-input v-model="filter.q" aria-label="目录模型搜索" maxlength="128" :disabled="blocked || !active"/></el-form-item><el-form-item label="筛选项目"><ScopeSelect v-model="filter.project" label="目录筛选项目" clearable :active="active && tab === 'edit'" :disabled="blocked"/></el-form-item><el-button :disabled="blocked || !active" :loading="choiceLoading" @click="loadChoices(true)">搜索模型</el-button></div><el-table :data="choices?.data ?? []" v-loading="choiceLoading" table-layout="auto" empty-text="无已发布模型，可调整筛选或先到模型页发布"><el-table-column label="别名 / 能力" min-width="180"><template #default="{ row }"><strong>{{ row.alias }}</strong><span class="id-line">{{ taskNames[row.task_type] }} · r{{ row.revision }}</span></template></el-table-column><el-table-column prop="project_id" label="项目" min-width="260"/><el-table-column label="选择" width="150"><template #default="{ row }"><el-button :disabled="blocked || !active" @click="add(row)">选择 / 更新绑定</el-button></template></el-table-column></el-table><div class="catalog-pagination"><el-button :disabled="choicePage === 1 || choiceLoading || blocked || !active" @click="loadChoices(false, -1)">上一页</el-button><span>第 {{ choicePage }} 页</span><el-button :disabled="!choices?.has_more || choiceLoading || blocked || !active" @click="loadChoices(false, 1)">下一页</el-button></div></section>
          <div class="catalog-toolbar"><h3>期望映射 · {{ rows.length }} 个别名</h3><el-button v-if="rows.length" type="danger" plain :disabled="blocked || !active" @click="rows = []">清空全部映射</el-button></div><p v-if="!rows.length" class="form-help">当前为空目录。发布并确认后，该网关不再接受这些模型的新请求。</p><section v-for="(row, index) in rows" :key="row.alias" class="catalog-rule" :aria-label="'映射 ' + row.alias"><div class="catalog-toolbar"><h3>{{ row.alias }}</h3><el-tag>{{ taskNames[row.task] }}</el-tag><el-button :disabled="blocked || !active" @click="rows.splice(index, 1)">移除此映射</el-button></div><p class="form-help mono">规格 {{ row.defaults }}</p><div class="catalog-fields"><el-form-item label="用量账户 ID"><el-input v-model="row.rule.usage_account_id" :aria-label="row.alias + ' 用量账户 ID'" maxlength="19" inputmode="numeric" :disabled="blocked || !active"/></el-form-item><el-form-item label="价格版本"><el-input v-model="row.rule.price_version" :aria-label="row.alias + ' 价格版本'" maxlength="64" :disabled="blocked || !active"/></el-form-item><el-form-item label="单位价格"><el-input v-model="row.rule.unit_price" :aria-label="row.alias + ' 单位价格'" maxlength="24" inputmode="decimal" :disabled="blocked || !active"/></el-form-item><el-form-item label="分组 ID（逗号或换行分隔）"><el-input v-model="row.groups" :aria-label="row.alias + ' 分组 ID'" type="textarea" :rows="2" maxlength="22000" :disabled="blocked || !active"/></el-form-item></div><div v-for="(binding, bi) in row.bindings" :key="binding.id" class="catalog-binding"><div><strong class="mono">{{ binding.id }} · r{{ binding.revision }}</strong><p class="form-help mono">项目 {{ binding.project }}</p><p class="form-help mono">{{ binding.release }} · {{ binding.channels.length }} 个渠道</p></div><el-button :disabled="blocked || !active" @click="removeBinding(index, bi)">移除绑定</el-button></div></section><el-button type="primary" native-type="submit" :loading="writing" :disabled="blocked || !active || changedBase">发布期望目录</el-button><p class="form-help">图片按张、视频和音频按各自冻结计费单位解释价格。最终计量、倍率与结算由 Sub2API 账本处理。</p></el-form></el-tab-pane>
      </el-tabs>
      <section v-if="detail" class="catalog-detail" aria-label="冻结目录详情"><div class="catalog-toolbar"><h3>冻结版本 v{{ detail.revision }}</h3><el-button @click="detail = null">收起 JSON</el-button></div><div v-if="session.allows('models:publish')" class="catalog-toolbar"><el-button :disabled="blocked || !active" @click="edit(detail)">以此版本编辑</el-button><el-button :disabled="blocked || !active" @click="replay('retry', detail)">重试所选版本</el-button><el-button :disabled="blocked || !active" @click="replay('rollback', detail)">回滚为新版本</el-button></div><p class="form-help mono">{{ detail.digest }}</p><pre class="catalog-json" tabindex="0">{{ detail.configuration_json }}</pre></section>
    </template>
    <el-empty v-else-if="!loading && !error" description="正在读取目录状态"/>
  </el-drawer>
</template>

<style scoped>
.catalog-heading,.catalog-toolbar,.catalog-state-line,.catalog-pagination { display:flex; flex-wrap:wrap; align-items:center; gap:12px; margin:16px 0; }
.catalog-heading { justify-content:space-between; margin-top:0; }
.catalog-heading .mono { margin-top:8px; overflow-wrap:anywhere; }
.catalog-versions { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:16px; margin:20px 0; }
.catalog-versions section,.catalog-rule,.catalog-picker,.catalog-detail,.catalog-request { padding:20px; border:1px solid var(--el-border-color); border-radius:6px; margin:16px 0; }
.catalog-versions section { margin:0; background:var(--el-fill-color-light); }
.catalog-versions strong { display:block; font-size:26px; margin:12px 0; }
.catalog-versions .mono { overflow-wrap:anywhere; line-height:1.6; }
.catalog-fields { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:0 16px; margin-top:16px; }
.catalog-filter { display:grid; grid-template-columns:1fr 1fr auto; gap:12px; align-items:end; margin-top:16px; }
.catalog-filter .el-button { margin-bottom:18px; }
.catalog-pagination { justify-content:flex-end; }
.catalog-binding { display:flex; align-items:center; justify-content:space-between; gap:12px; padding:12px 0; border-top:1px solid var(--el-border-color-lighter); overflow-wrap:anywhere; }
.catalog-binding>div { min-width:0; }
.catalog-json { white-space:pre-wrap; overflow-wrap:anywhere; max-height:360px; overflow:auto; background:var(--el-fill-color-light); padding:16px; font:12px/1.6 Consolas,monospace; }
.catalog-rule h3,.catalog-picker h3,.catalog-detail h3 { font-size:15px; margin:0; }
.catalog-request { background:var(--el-fill-color-light); }
.catalog-request p { margin-top:8px; }
:deep(.el-table .cell) { overflow-wrap:anywhere; }
@media(max-width:650px) { .catalog-versions,.catalog-fields,.catalog-filter { grid-template-columns:minmax(0,1fr); } .catalog-versions section,.catalog-rule,.catalog-picker,.catalog-detail,.catalog-request { padding:14px; } .catalog-binding { align-items:flex-start; flex-direction:column; } .catalog-filter .el-button { margin-bottom:0; } .catalog-toolbar .el-button { margin-left:0; min-height:44px; } .catalog-pagination .el-button { min-height:44px; } }
</style>
