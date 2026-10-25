<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { api, readableError, time, unwrap } from '../api'
import { useSession } from '../session'
import type { components, paths } from '../generated/api'

type Audit = components['schemas']['AuditView']
type Retention = components['schemas']['AuditRetentionView']
type Query = NonNullable<paths['/admin/api/v1/audit-events']['get']['parameters']['query']>
const session = useSession()
const draft = reactive({ actor: '', action: '', resource: '', request_id: '', from: '', before: '' })
const applied = ref<Query>({}), rows = ref<Audit[]>([]), error = ref(''), loading = ref(false), suspended = ref(false)
const page = ref(1), total = ref(0), hasMore = ref(false), snapshot = ref<number | null>(null), selected = ref<Audit | null>(null)
const retention = ref<Retention | null>(null), retentionError = ref('')
let controller: AbortController | undefined, disposed = false, epoch = 0
let requestTimer: ReturnType<typeof setTimeout> | undefined
const detailOpen = computed({ get: () => selected.value !== null, set: (open: boolean) => { if (!open) selected.value = null } })
const actionNames: Record<string, string> = {
  'channel.created': '新增渠道', 'channel.updated': '更新渠道', 'channel.enable': '启用渠道', 'channel.disable': '停用渠道', 'channel.deleted': '删除渠道',
  'workflow.created': '新增 Workflow', 'workflow.updated': '更新 Workflow', 'workflow.imported': '导入 Workflow', 'workflow.released': '发布 Workflow', 'workflow.sync_requested': '同步 Workflow', 'workflow.channel_bound': '绑定 Workflow', 'workflow.runtime_approved': '批准运行画像',
  'model.created': '新增模型', 'model.updated': '更新模型', 'model.published': '上架模型', 'model.unpublished': '下架模型',
  'key.created': '新增业务 Key', 'key.updated': '更新 Key 策略', 'key.rotate': '轮换 Key', 'key.deleted': '删除业务 Key',
  'admin_user.created': '新增管理账号', 'admin_user.updated': '更新管理账号', 'content.tags_changed': '修改内容标签', 'job.priority_changed': '修改任务优先级',
  'audit.retention_pruned': '按保留策略清理审计',
}
const labels: Record<string, string> = { revision: '记录版本', version: '发布版本', enabled: '启用状态', changed: '是否变更', is_active: '是否生效', password_changed: '是否修改密码', role: '角色', target_count: '目标数量', tag_count: '标签数量', count: '数量', next_at: '下次执行时间', channel_id: '关联渠道', release_id: 'Workflow 发布', job_id: '关联任务', validation_job_id: '验证任务', retry_of: '来源任务', preview_id: '导入预览', tenant_id: '租户', source_digest: '源文件摘要', candidate_digest: '导入摘要', evidence_id: '证据标识', error_code: '错误编码', observation: '观测类型', source: '来源', reason: '原因编码', changed_fields: '变更字段', dispatch: '调度记录', priority: '优先级' }
const supplement = computed(() => Object.entries(selected.value?.detail ?? {}).filter(([key]) => !['revision_before', 'revision_after', 'operation_id'].includes(key)))
function valueText(value: unknown, name?: string): string {
  if (name === 'cutoff' && typeof value === 'number') return time(value)
  if (value === null || value === undefined) return '未记录'
  if (typeof value === 'boolean') return value ? '是' : '否'
  if (Array.isArray(value)) return value.join('、') || '无'
  if (typeof value === 'object') return Object.entries(value).map(([key, item]) => `${key}: ${String(item)}`).join(' · ')
  return String(value)
}
function stop() { epoch++; clearTimeout(requestTimer); requestTimer = undefined; controller?.abort(); controller = undefined; loading.value = false; selected.value = null }
async function refresh(revalidate = false) {
  if (document.hidden || disposed || !session.allows('audit:read')) return
  stop(); const current = epoch; const request = new AbortController(); controller = request; loading.value = true
  let recordsPending = true, policyPending = true, policy: Promise<void> | undefined
  const active = () => !disposed && !request.signal.aborted && current === epoch
  requestTimer = setTimeout(() => {
    if (!active()) return
    if (recordsPending) error.value = '审计查询超时，请稍后刷新'
    if (policyPending) retentionError.value = '保留状态读取超时，请稍后刷新'
    request.abort(); loading.value = false
  }, 10_000)
  try {
    if (revalidate) await session.refresh(request.signal)
    if (disposed || request.signal.aborted || current !== epoch || !session.allows('audit:read')) return
    policy = api.GET('/admin/api/v1/audit-retention', { signal: request.signal }).then(unwrap).then(value => {
      if (active()) { retention.value = value; retentionError.value = '' }
    }).catch(e => { if (active()) retentionError.value = readableError(e) }).finally(() => { policyPending = false })
    const result = unwrap(await api.GET('/admin/api/v1/audit-events', { signal: request.signal, params: { query: { ...applied.value, page: page.value } } }))
    if (!active()) return
    rows.value = result.data; total.value = result.total; hasMore.value = result.has_more; snapshot.value = result.snapshot_at; error.value = ''; suspended.value = false
  } catch (e) { if (!request.signal.aborted && current === epoch && !disposed) error.value = readableError(e) }
  finally { recordsPending = false; if (controller === request) loading.value = false; await policy; if (controller === request) { clearTimeout(requestTimer); requestTimer = undefined } }
}
function apply() {
  const query: Query = {}
  for (const field of ['actor', 'action', 'resource', 'request_id'] as const) if (draft[field].trim()) query[field] = draft[field].trim()
  if (draft.from) query.created_from = Math.floor(new Date(draft.from).getTime() / 1000)
  if (draft.before) query.created_before = Math.floor(new Date(draft.before).getTime() / 1000)
  applied.value = query; page.value = 1; void refresh()
}
function reset() { Object.assign(draft, { actor: '', action: '', resource: '', request_id: '', from: '', before: '' }); applied.value = {}; page.value = 1; void refresh() }
function sameRequest(value: string) { resetDraft(); draft.request_id = value; apply() }
function resetDraft() { Object.assign(draft, { actor: '', action: '', resource: '', request_id: '', from: '', before: '' }) }
function goPage(delta: number) { page.value += delta; void refresh() }
function visibility() { if (document.hidden) { suspended.value = true; stop() } else void refresh(true) }
onMounted(() => { document.addEventListener('visibilitychange', visibility); void refresh() })
onUnmounted(() => { disposed = true; stop(); document.removeEventListener('visibilitychange', visibility) })
</script>

<template>
  <div class="page-heading"><div><p class="eyebrow li-lab">GOVERNANCE / AUDIT</p><h1>审计事件</h1><p class="muted">按主体、动作和请求追踪管理变更。</p></div><el-button :icon="Refresh" :loading="loading" @click="refresh(true)">刷新记录</el-button></div>
  <section class="panel li-card audit-retention" aria-label="审计保留策略">
    <div class="panel-heading"><h2>审计保留策略</h2><el-tag v-if="retention" :type="retention.enabled ? 'warning' : 'info'">{{ retention.enabled ? `已配置保留 ${retention.retention_days} 天` : '自动清理未启用' }}</el-tag></div>
    <el-alert v-if="retentionError" title="保留状态读取失败" :description="retentionError" type="warning" :closable="false"/>
    <template v-if="retention"><p class="muted">{{ retention.enabled ? `按部署配置分批清理到期审计，每批最多 ${retention.batch_limit} 条。` : '当前配置不会自动删除管理审计记录。' }}</p>
      <p v-if="retention.last_batch" class="retention-history">最近清理 {{ time(retention.last_batch.completed_at) }} · {{ retention.last_batch.deleted }} 条 · 当时保留 {{ retention.last_batch.retention_days }} 天<br/>清理截止 {{ time(retention.last_batch.cutoff) }}（不含）</p><p v-else class="muted">尚未观察到已提交的清理批次。</p>
      <small class="muted">{{ retentionError || suspended ? '上次成功状态' : '状态快照' }} {{ time(retention.snapshot_at) }}</small>
    </template><p v-else class="muted">尚未取得保留状态。</p>
  </section>
  <section class="panel li-card audit-filters" aria-label="审计筛选">
    <el-form label-position="top" @submit.prevent="apply">
      <div class="audit-filter-grid">
        <el-form-item label="操作主体 ID" for="audit-actor"><el-input id="audit-actor" v-model="draft.actor" maxlength="128" placeholder="usr_… / key_… / system" clearable/></el-form-item>
        <el-form-item label="动作编码" for="audit-action"><el-input id="audit-action" v-model="draft.action" maxlength="128" placeholder="channel.updated" clearable/></el-form-item>
        <el-form-item label="资源 ID" for="audit-resource"><el-input id="audit-resource" v-model="draft.resource" maxlength="49" placeholder="公开 ID 或 UUID" clearable/></el-form-item>
        <el-form-item label="请求 ID" for="audit-request"><el-input id="audit-request" v-model="draft.request_id" maxlength="128" placeholder="完整请求 ID" clearable/></el-form-item>
        <el-form-item label="起始时间（含）" for="audit-from"><input id="audit-from" v-model="draft.from" class="audit-date" type="datetime-local" step="1"/></el-form-item>
        <el-form-item label="结束时间（不含）" for="audit-before"><input id="audit-before" v-model="draft.before" class="audit-date" type="datetime-local" step="1"/></el-form-item>
      </div>
      <div class="toolbar"><el-button type="primary" native-type="submit" :disabled="loading">应用筛选</el-button><el-button :disabled="loading" @click="reset">重置筛选</el-button><span class="muted audit-hint">条件精确匹配并取交集，时间使用本地时区。</span></div>
    </el-form>
  </section>
  <el-alert v-if="error" :title="error" type="error" :closable="false"/>
  <p class="snapshot-line" role="status"><template v-if="snapshot">{{ error || suspended ? '上次成功快照' : '查询快照' }} {{ time(snapshot) }} · 匹配 {{ total.toLocaleString() }} 条</template><template v-else>尚未取得审计快照</template><span v-if="suspended"> · 页面隐藏，已暂停</span><span v-else-if="loading"> · 正在查询</span></p>
  <section class="panel li-card audit-results" aria-label="审计结果">
    <el-table :data="rows" v-loading="loading" empty-text="没有匹配的审计记录" table-layout="auto">
      <el-table-column label="动作" min-width="185"><template #default="{ row }">{{ actionNames[row.action] ?? row.action }}<span class="id-line">{{ row.action }}</span></template></el-table-column>
      <el-table-column label="操作主体" min-width="240"><template #default="{ row }">{{ row.actor_name ?? row.actor }}<span v-if="row.actor_name" class="id-line">{{ row.actor }}</span></template></el-table-column>
      <el-table-column label="资源" min-width="275"><template #default="{ row }"><span class="mono">{{ row.resource }}</span></template></el-table-column>
      <el-table-column label="时间" min-width="180"><template #default="{ row }">{{ time(row.created_at) }}</template></el-table-column>
      <el-table-column label="记录" width="90" fixed="right"><template #default="{ row }"><el-button text :aria-label="`查看 ${row.action} 的审计详情`" @click="selected = row">详情</el-button></template></el-table-column>
    </el-table>
    <div class="pagination"><el-button size="small" :disabled="page === 1 || loading" @click="goPage(-1)">上一页</el-button><span>第 {{ page }} 页 · 每页 50 条</span><el-button size="small" :disabled="!hasMore || page >= 10000 || loading" @click="goPage(1)">下一页</el-button></div>
  </section>
  <el-drawer v-model="detailOpen" title="审计记录详情" size="min(680px, 100vw)">
    <template v-if="selected">
      <p class="eyebrow li-lab">{{ selected.action }}</p><h2>{{ actionNames[selected.action] ?? '审计事件' }}</h2>
      <dl class="audit-detail"><dt>发生时间</dt><dd>{{ time(selected.created_at) }}</dd><dt>操作主体</dt><dd>{{ selected.actor_name ?? '未记录名称' }}<span class="id-line">{{ selected.actor }}</span><small v-if="selected.actor_name" class="muted">名称为当前名称，主体 ID 保持不变。</small></dd><dt>资源 ID</dt><dd class="mono">{{ selected.resource }}</dd><dt>请求 ID</dt><dd><span class="mono">{{ selected.request_id }}</span><br/><el-button text @click="sameRequest(selected.request_id)">筛选同一请求</el-button></dd><dt>记录 ID</dt><dd class="mono">{{ selected.id }}</dd><dt>关联操作</dt><dd><template v-if="selected.operation_id"><span class="mono">{{ selected.operation_id }}</span><br/><router-link v-if="session.allows('operations:read')" :to="{ path: '/operations', query: { operation_id: selected.operation_id } }">查看操作与执行目标</router-link></template><span v-else>未记录</span></dd></dl>
      <h3>变更版本</h3><div class="audit-versions"><div><span>变更前</span><strong>{{ selected.revision_before ?? '未记录' }}</strong></div><span aria-hidden="true">→</span><div><span>变更后</span><strong>{{ selected.revision_after ?? '未记录' }}</strong></div></div>
      <h3>已记录的补充信息</h3><dl v-if="supplement.length" class="audit-detail"><template v-for="[key, value] in supplement" :key="key"><dt>{{ ({ deleted: '清理条数', cutoff: '清理截止', retention_days: '保留天数' } as Record<string, string>)[key] ?? labels[key] ?? key }}</dt><dd class="audit-value">{{ valueText(value, key) }}</dd></template></dl><p v-else class="muted">无补充信息</p>
      <el-alert v-if="selected.detail_redacted" title="部分详情已隐藏，仅展示可公开的管理字段。" type="info" :closable="false"/>
    </template>
  </el-drawer>
</template>

<style scoped>
.audit-filters { margin-bottom: 16px; padding: 20px; }
.audit-filter-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 0 18px; }
.audit-date { width: 100%; min-width: 0; border: 1px solid var(--el-border-color); border-radius: 4px; padding: 7px 10px; color: var(--el-text-color-regular); background: white; font: inherit; }
.audit-date:focus { outline: 2px solid var(--el-color-primary); outline-offset: 1px; }
.audit-hint { font-size: 12px; }
.audit-detail { display: grid; grid-template-columns: 100px minmax(0, 1fr); gap: 14px 18px; margin: 24px 0; }
.audit-detail dt { color: var(--el-text-color-secondary); }
.audit-detail dd { margin: 0; overflow-wrap: anywhere; }
.audit-detail a { color: var(--el-color-primary); }
.audit-value { white-space: pre-wrap; }
.audit-versions { display: flex; align-items: center; gap: 22px; padding: 18px; background: var(--rc-canvas); border: 1px solid var(--rc-line); border-radius: 8px; margin: 16px 0 28px; }
.audit-versions div { display: grid; gap: 8px; }
.audit-versions span { color: var(--el-text-color-secondary); font-size: 13px; }
.audit-versions strong { font: 600 20px ui-monospace, monospace; }
@media(max-width: 1000px) { .audit-filter-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media(max-width: 550px) { .audit-filter-grid { grid-template-columns: minmax(0, 1fr); } .audit-filters { padding: 16px; } .audit-hint { flex-basis: 100%; } .audit-detail { grid-template-columns: minmax(0, 1fr); gap: 6px; } .audit-detail dd { margin-bottom: 12px; } .pagination { gap: 6px; } }
</style>
