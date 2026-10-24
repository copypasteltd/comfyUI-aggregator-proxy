<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, toRaw, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Plus, Refresh } from '@element-plus/icons-vue'
import { api, readableError, revisionHeaders, time, unwrap, type ProjectRecord, type ScopeLimits, type TenantRecord } from '../api'
import { useSession } from '../session'
import ResourceLimitsEditor from '../components/ResourceLimitsEditor.vue'

const session = useSession(), kind = ref<'tenants' | 'projects'>('tenants'), rows = ref<(TenantRecord | ProjectRecord)[]>([])
const context = ref<TenantRecord | null>(null), query = ref(''), applied = ref(''), state = ref<boolean | undefined>(), page = ref(1), hasMore = ref(false)
const loading = ref(false), saving = ref(false), error = ref(''), formError = ref(''), drawer = ref(false), mode = ref<'tenant' | 'project'>('tenant')
const original = ref<TenantRecord | ProjectRecord | null>(null), defaults = ref<ScopeLimits | null>(null), draftLimits = ref<ScopeLimits | null>(null)
const form = ref({ name: '', enabled: true }), writeKey = ref(''), observedAt = ref<number | null>(null)
let request: AbortController | null = null, detailRequest: AbortController | null = null, sequence = 0
const writable = computed(() => session.allows('scopes:manage'))
const parentLimits = computed(() => mode.value === 'project' ? (original.value && 'tenant_limits' in original.value ? original.value.tenant_limits : context.value?.limits) : undefined)
const title = computed(() => `${original.value ? writable.value ? '编辑' : '查看' : '新建'}${mode.value === 'tenant' ? '租户' : '项目'}`)

async function refresh() {
  request?.abort(); request = new AbortController()
  const active = ++sequence
  loading.value = true; error.value = ''
  try {
    const params = { page: page.value, q: applied.value || undefined, enabled: state.value, tenant_id: kind.value === 'projects' ? context.value?.id : undefined }
    const result = kind.value === 'tenants'
      ? unwrap(await api.GET('/admin/api/v1/tenants', { params: { query: params }, signal: request.signal }))
      : unwrap(await api.GET('/admin/api/v1/projects', { params: { query: params }, signal: request.signal }))
    if (active !== sequence) return
    rows.value = result.data; hasMore.value = result.has_more; defaults.value = result.default_limits; observedAt.value = result.observed_at
  } catch (e) { if (active === sequence && !(e instanceof DOMException && e.name === 'AbortError')) error.value = readableError(e) }
  finally { if (active === sequence) loading.value = false }
}
function apply() { applied.value = query.value.trim(); page.value = 1; void refresh() }
function projects(row: TenantRecord) { context.value = row; kind.value = 'projects'; query.value = ''; applied.value = ''; state.value = undefined; page.value = 1; void refresh() }
function allProjects() { context.value = null; page.value = 1; void refresh() }
function create(value: 'tenant' | 'project', tenant?: TenantRecord) {
  detailRequest?.abort()
  if (value === 'project' && !tenant && !context.value) { kind.value = 'tenants'; ElMessage.info('请在租户目录中选择“添加项目”'); return }
  if (!defaults.value) return
  if (tenant) context.value = tenant
  mode.value = value; original.value = null; form.value = { name: '', enabled: true }; draftLimits.value = structuredClone(toRaw(defaults.value)); formError.value = ''; writeKey.value = crypto.randomUUID(); drawer.value = true
}
async function open(row: TenantRecord | ProjectRecord) {
  detailRequest?.abort(); detailRequest = new AbortController()
  const active = detailRequest
  try {
    const value = 'tenant_id' in row
      ? unwrap(await api.GET('/admin/api/v1/projects/{identifier}', { params: { path: { identifier: row.id } }, signal: active.signal }))
      : unwrap(await api.GET('/admin/api/v1/tenants/{identifier}', { params: { path: { identifier: row.id } }, signal: active.signal }))
    if (active.signal.aborted) return
    original.value = value; mode.value = 'tenant_id' in value ? 'project' : 'tenant'; form.value = { name: value.name, enabled: value.enabled }; draftLimits.value = structuredClone(toRaw(value.limits)); formError.value = ''; writeKey.value = crypto.randomUUID(); drawer.value = true
  } catch (e) { if (!active.signal.aborted) error.value = readableError(e) }
}
async function save() {
  if (!draftLimits.value || !writable.value) return
  saving.value = true; formError.value = ''
  try {
    const name = form.value.name.trim(), limits = structuredClone(toRaw(draftLimits.value))
    if (!name) throw new Error('请填写名称')
    const headers = { 'Idempotency-Key': writeKey.value }
    if (original.value) {
      const previous = original.value, changes: { expected_revision: number; name?: string; enabled?: boolean; limits?: ScopeLimits } = { expected_revision: previous.revision }
      if (name !== previous.name) changes.name = name
      if (form.value.enabled !== previous.enabled) changes.enabled = form.value.enabled
      if (JSON.stringify(limits) !== JSON.stringify(previous.limits)) changes.limits = limits
      if (Object.keys(changes).length === 1) { drawer.value = false; return }
      const params = { path: { identifier: previous.id } }, options = { params, body: changes, headers: { ...headers, ...revisionHeaders(previous.revision) } }
      if (mode.value === 'tenant') {
        const value = unwrap(await api.PATCH('/admin/api/v1/tenants/{identifier}', options))
        if (context.value?.id === value.id) context.value = value
      } else unwrap(await api.PATCH('/admin/api/v1/projects/{identifier}', options))
    } else if (mode.value === 'tenant') unwrap(await api.POST('/admin/api/v1/tenants', { body: { name, limits, enabled: form.value.enabled }, headers }))
    else {
      if (!context.value) throw new Error('请先选择所属租户')
      unwrap(await api.POST('/admin/api/v1/projects', { body: { tenant_id: context.value.id, name, limits, enabled: form.value.enabled }, headers }))
    }
    ElMessage.success('配置已保存'); drawer.value = false; await refresh()
  } catch (e) { formError.value = readableError(e) }
  finally { saving.value = false }
}
function close(next: () => void) { if (!saving.value) next() }
watch(kind, () => { page.value = 1; query.value = ''; applied.value = ''; state.value = undefined; void refresh() })
onMounted(refresh)
onBeforeUnmount(() => { sequence++; request?.abort(); detailRequest?.abort() })
</script>

<template>
  <div class="page-heading"><div><p class="eyebrow">ACCESS / RESOURCE LIMITS</p><h1>租户与项目</h1><p class="muted">管理业务归属与资源上限，查看当前已用与预留。</p></div><div class="toolbar"><el-button :icon="Refresh" :loading="loading" @click="refresh">刷新</el-button><el-button v-if="writable" type="primary" :icon="Plus" @click="create(kind === 'tenants' ? 'tenant' : 'project')">{{ kind === 'tenants' ? '新建租户' : context ? '添加项目' : '选择租户添加项目' }}</el-button></div></div>
  <el-alert v-if="error" :title="error" type="error" :closable="false"/>
  <div class="panel scope-directory"><el-tabs v-model="kind"><el-tab-pane label="租户" name="tenants"/><el-tab-pane label="项目" name="projects"/></el-tabs>
    <div v-if="kind === 'projects' && context" class="scope-context"><div><span class="muted">所属租户</span><strong>{{ context.name }}</strong><span v-if="!context.enabled" class="muted">已停用</span></div><el-button size="small" @click="allProjects">查看全部项目</el-button></div>
    <form class="scope-search" @submit.prevent="apply"><el-input v-model="query" aria-label="搜索租户或项目" placeholder="搜索名称或完整 ID" clearable/><el-select v-model="state" aria-label="启停状态" placeholder="全部状态" clearable><el-option label="启用" :value="true"/><el-option label="停用" :value="false"/></el-select><el-button type="primary" native-type="submit">查询</el-button></form>
    <p class="form-help scope-policy">租户、项目与 Key 的限制共同生效。收紧上限保留在途任务；停用后拒绝新的业务调用。</p>
    <el-table v-loading="loading" :data="rows" row-key="id" empty-text="暂无记录；可新建租户后添加项目。"><el-table-column label="名称 / 标识" min-width="230"><template #default="{ row }"><strong>{{ row.name }}</strong><div class="mono muted">{{ row.id }}</div><small v-if="'tenant_name' in row" class="muted">租户 {{ row.tenant_name }}</small></template></el-table-column><el-table-column label="状态" width="130"><template #default="{ row }"><el-tag :type="row.enabled ? 'success' : 'info'">{{ row.enabled ? '启用' : '停用' }}</el-tag><div v-if="'tenant_enabled' in row && !row.tenant_enabled" class="scope-parent-off">租户已停用</div></template></el-table-column><el-table-column label="未结束 / 生成中" width="155"><template #default="{ row }"><span class="mono">{{ row.usage.unfinished_jobs }} / {{ row.usage.generating_jobs }}</span></template></el-table-column><el-table-column label="当日任务 / 图片" width="155"><template #default="{ row }"><span class="mono">{{ row.usage.daily_jobs }} / {{ row.usage.daily_image_count }}</span></template></el-table-column><el-table-column label="操作" min-width="210" fixed="right"><template #default="{ row }"><el-button link type="primary" @click="open(row)">{{ writable ? '配置与用量' : '查看用量' }}</el-button><template v-if="kind === 'tenants'"><el-button link @click="projects(row)">查看项目</el-button><el-button v-if="writable" link type="primary" @click="create('project', row)">添加项目</el-button></template></template></el-table-column></el-table>
    <div class="pagination"><span class="muted">第 {{ page }} 页 · 每页最多 50 条 · 快照 {{ time(observedAt) }}</span><el-button :disabled="page === 1 || loading" @click="page--; refresh()">上一页</el-button><el-button :disabled="!hasMore || loading" @click="page++; refresh()">下一页</el-button></div>
  </div>
  <el-drawer v-model="drawer" :title="title" size="min(700px, 100vw)" :before-close="close" :close-on-click-modal="!saving"><el-alert v-if="formError" :title="formError" type="error" :closable="false"/><el-form v-if="draftLimits" label-position="top" @submit.prevent="save"><p v-if="mode === 'project'" class="scope-parent-label">所属租户：{{ original && 'tenant_name' in original ? original.tenant_name : context?.name }}</p><el-form-item :label="mode === 'tenant' ? '租户名称' : '项目名称'" required><el-input v-model="form.name" :disabled="!writable || saving" :aria-label="mode === 'tenant' ? '租户名称' : '项目名称'" maxlength="128"/></el-form-item><el-form-item label="启用状态"><el-switch v-model="form.enabled" :disabled="!writable || saving" aria-label="启用当前作用域"/><span class="form-help">{{ form.enabled ? '允许业务请求，仍受上级与 Key 策略限制' : '拒绝新的业务调用；保留已接收任务和历史内容' }}</span></el-form-item><h2 class="scope-limit-title">资源限制</h2><p class="form-help">日额度按 Asia/Taipei 统计。无限制只取消本层上限；项目仍受租户限制。</p><ResourceLimitsEditor v-model="draftLimits" :parent="parentLimits" :usage="original?.usage" :readonly="!writable || saving"/><p v-if="original" class="form-help scope-snapshot">版本 {{ original.revision }} · 用量快照 {{ time(original.observed_at) }} · 当日 {{ original.usage.period }}<br/>用量包含已用与预留，不代表余额；配置修改不会清零历史用量或抢占在途任务。</p><div class="drawer-footer"><el-button :disabled="saving" @click="drawer = false">关闭</el-button><el-button v-if="original" :disabled="saving" @click="open(original)">重新读取</el-button><el-button v-if="writable" type="primary" native-type="submit" :loading="saving">保存配置</el-button></div></el-form></el-drawer>
</template>

<style scoped>
.scope-directory { padding: 18px 20px 0; }
.scope-context { display: flex; justify-content: space-between; gap: 16px; align-items: center; padding: 12px 16px; margin-bottom: 16px; background: #f3f6f8; border-left: 3px solid #087e8b; }
.scope-context strong { margin: 0 12px; }
.scope-search { display: grid; grid-template-columns: minmax(160px, 1fr) 150px auto; gap: 12px; max-width: 740px; }
.scope-policy { margin: 16px 0; }
:deep(.el-switch) { margin-right: 12px; }
.scope-parent-off { color: #bf5e31; font-size: 11px; margin-top: 6px; }
.scope-parent-label { margin-bottom: 18px; color: #617482; }
.scope-limit-title { margin: 20px 0 8px; }
.scope-limit-title + p { margin-bottom: 14px; }
.scope-snapshot { margin-top: 20px; line-height: 1.8; }
.scope-directory :deep(.el-table .cell) { word-break: normal; }
@media (max-width: 600px) { .scope-directory { padding: 12px 12px 0; } .scope-search { grid-template-columns: minmax(0, 1fr) 110px; } .scope-search > .el-button { grid-column: 1 / -1; justify-self: end; } .scope-context { align-items: flex-start; flex-direction: column; } }
</style>
