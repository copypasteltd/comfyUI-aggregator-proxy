<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh } from '@element-plus/icons-vue'
import { api, readableError, revisionHeaders, unwrap, type KeyPolicy } from '../api'
import type { components } from '../generated/api'
import { useSession } from '../session'
import ScopeSelect from '../components/ScopeSelect.vue'
import GatewayCatalogDrawer from '../components/GatewayCatalogDrawer.vue'

type Gateway = components['schemas']['GatewayView']
type Binding = components['schemas']['generation_contracts__gateway__BindingView']
const session = useSession(), gateways = ref<Gateway[]>([]), bindings = ref<Binding[]>([]), error = ref(''), loading = ref(false), saving = ref(false), gatewayPage = ref(1), bindingPage = ref(1), moreGateways = ref(false), moreBindings = ref(false), defaultPolicy = ref<KeyPolicy | null>(null)
const serviceOpen = ref(false), bindingOpen = ref(false), editing = ref<Gateway | null>(null), secret = ref(''), secretOpen = ref(false)
const service = ref({ name: '', issuer: 'sub2api', cidrs: '' })
const catalogOpen = ref(false), catalogGateway = ref<Gateway | null>(null)
function openCatalog(row: Gateway) { catalogGateway.value = row; catalogOpen.value = true }
const binding = ref({ gateway_id: '', project_id: '', name: '', external_tenant_ref: '', external_key_ref: '' })
const split = (value: string) => [...new Set(value.split(/[\n,，]/).map(v => v.trim()).filter(Boolean))]
async function refresh() {
  loading.value = true
  try {
    const [g, b, k] = await Promise.all([api.GET('/admin/api/v1/gateways', { params: { query: { page: gatewayPage.value } } }), api.GET('/admin/api/v1/gateway-bindings', { params: { query: { page: bindingPage.value } } }), api.GET('/admin/api/v1/api-keys', { params: { query: { page: 1 } } })])
    const services = unwrap(g), mappings = unwrap(b)
    gateways.value = services.data; moreGateways.value = services.has_more
    bindings.value = mappings.data; moreBindings.value = mappings.has_more
    defaultPolicy.value = unwrap(k).default_policy; error.value = ''
  } catch (e) { error.value = readableError(e) } finally { loading.value = false }
}
function openService(row?: Gateway) { editing.value = row ?? null; service.value = { name: row?.name ?? '', issuer: row?.issuer ?? 'sub2api', cidrs: row?.allowed_cidrs.join('\n') ?? '' }; serviceOpen.value = true }
function openBinding() { binding.value = { gateway_id: gateways.value.find(g => g.enabled)?.id ?? '', project_id: session.info?.scopes[0]?.project_id ?? '', name: '', external_tenant_ref: '', external_key_ref: '' }; bindingOpen.value = true }
function showSecret(value?: string | null) { if (value) { secret.value = value; secretOpen.value = true } }
async function saveService() {
  saving.value = true
  try {
    const body = { name: service.value.name.trim(), allowed_cidrs: split(service.value.cidrs) }
    if (!body.name || !body.allowed_cidrs.length) throw new Error('请填写名称与来源网段')
    if (editing.value) unwrap(await api.PATCH('/admin/api/v1/gateways/{identifier}', { params: { path: { identifier: editing.value.id } }, headers: revisionHeaders(editing.value.revision), body: { ...body, expected_revision: editing.value.revision, rotate_credential: false } }))
    else showSecret(unwrap(await api.POST('/admin/api/v1/gateways', { body: { ...body, issuer: service.value.issuer.trim() } })).secret)
    serviceOpen.value = false; ElMessage.success('网关服务已保存'); await refresh()
  } catch (e) { error.value = readableError(e) } finally { saving.value = false }
}
async function action(row: Gateway, rotate = false) {
  try { await ElMessageBox.confirm(rotate ? '轮换会立即撤销旧服务凭证。请同步更新网关配置。' : row.enabled ? '停用将阻止此网关的全部调用，包括后台对账。' : '启用后将按来源网段和主体绑定授权访问。', rotate ? '轮换网关凭证' : '修改网关状态', { type: 'warning', confirmButtonText: '确认', cancelButtonText: '取消' }) }
  catch { return }
  try {
    const result = unwrap(await api.PATCH('/admin/api/v1/gateways/{identifier}', { params: { path: { identifier: row.id } }, headers: revisionHeaders(row.revision), body: { expected_revision: row.revision, rotate_credential: rotate, ...(!rotate ? { enabled: !row.enabled } : {}) } }))
    showSecret(result.secret); await refresh()
  } catch (e) { error.value = readableError(e) }
}
async function saveBinding() {
  if (!defaultPolicy.value) return
  saving.value = true
  try {
    const scope = session.info?.scopes.find(s => s.project_id === binding.value.project_id)
    if (!scope || !binding.value.gateway_id || !binding.value.name.trim() || !binding.value.external_tenant_ref.trim() || !binding.value.external_key_ref.trim()) throw new Error('请完整填写网关、项目和外部主体标识')
    unwrap(await api.POST('/admin/api/v1/gateway-bindings', { body: { ...binding.value, tenant_id: scope.tenant_id, policy: defaultPolicy.value } }))
    bindingOpen.value = false; ElMessage.success('绑定已建立，请在 API 密钥页配置其能力范围'); await refresh()
  } catch (e) { error.value = readableError(e) } finally { saving.value = false }
}
async function toggleBinding(row: Binding) {
  try { await ElMessageBox.confirm(row.enabled ? '停用后，该外部主体不能提交任务或读取内容。既有终态仍可由网关对账。' : '启用后按对应逻辑 Key 的当前策略授权。', '修改绑定状态', { confirmButtonText: '确认', cancelButtonText: '取消' }) }
  catch { return }
  try { unwrap(await api.PATCH('/admin/api/v1/gateway-bindings/{identifier}', { params: { path: { identifier: row.id } }, headers: revisionHeaders(row.revision), body: { expected_revision: row.revision, enabled: !row.enabled } })); await refresh() }
  catch (e) { error.value = readableError(e) }
}
async function goPage(kind: 'gateway' | 'binding', delta: number) { if (kind === 'gateway') gatewayPage.value += delta; else bindingPage.value += delta; await refresh() }
onMounted(refresh); onUnmounted(() => { secret.value = '' })
</script>

<template>
  <div class="page-heading"><div><p class="eyebrow li-lab">INTEGRATION / GATEWAYS</p><h1>网关接入</h1><p class="muted">管理 Sub2API 服务身份、外部客户映射及模型目录发布。</p></div><div class="actions"><el-button :icon="Refresh" :loading="loading" @click="refresh">刷新</el-button><el-button type="primary" :icon="Plus" @click="openService()">添加网关</el-button></div></div>
  <el-alert v-if="error" :title="error" type="error" :closable="false"/>
  <section class="panel li-card"><h2>网关服务</h2><el-table :data="gateways" v-loading="loading" empty-text="尚未添加网关服务" table-layout="auto"><el-table-column label="名称 / ID" min-width="250"><template #default="{ row }"><strong>{{ row.name }}</strong><span class="id-line">{{ row.id }}</span></template></el-table-column><el-table-column prop="issuer" label="来源" width="120"/><el-table-column label="状态" width="90"><template #default="{ row }"><el-tag :type="row.enabled ? 'success' : 'info'">{{ row.enabled ? '已启用' : '已停用' }}</el-tag></template></el-table-column><el-table-column label="允许来源" min-width="180"><template #default="{ row }">{{ row.allowed_cidrs.join(', ') }}</template></el-table-column><el-table-column label="操作" min-width="300" fixed="right"><template #default="{ row }"><el-button v-if="session.allows('models:read')" link @click="openCatalog(row)">模型目录</el-button><el-button link @click="openService(row)">编辑</el-button><el-button link @click="action(row)">{{ row.enabled ? '停用' : '启用' }}</el-button><el-button link @click="action(row, true)">轮换凭证</el-button></template></el-table-column></el-table><div class="pagination"><el-button :disabled="gatewayPage === 1" size="small" @click="goPage('gateway', -1)">上一页</el-button><span>第 {{ gatewayPage }} 页</span><el-button :disabled="!moreGateways" size="small" @click="goPage('gateway', 1)">下一页</el-button></div></section>
  <section class="panel li-card"><div class="page-heading"><div><h2>外部主体绑定</h2><p class="muted">每个主体使用独立逻辑 Key，在 API 密钥页设置权限和额度。</p></div><el-button :icon="Plus" :disabled="!gateways.some(g => g.enabled)" @click="openBinding">添加绑定</el-button></div><el-table :data="bindings" v-loading="loading" empty-text="暂无外部主体绑定" table-layout="auto"><el-table-column prop="external_tenant_ref" label="外部租户" min-width="150"/><el-table-column prop="external_key_ref" label="外部 Key 标识" min-width="150"/><el-table-column prop="key_id" label="本地逻辑 Key" min-width="260"/><el-table-column prop="gateway_id" label="网关 ID" min-width="280"/><el-table-column label="状态" width="90"><template #default="{ row }">{{ row.enabled ? '已启用' : '已停用' }}</template></el-table-column><el-table-column label="操作" width="90" fixed="right"><template #default="{ row }"><el-button link @click="toggleBinding(row)">{{ row.enabled ? '停用' : '启用' }}</el-button></template></el-table-column></el-table><div class="pagination"><el-button :disabled="bindingPage === 1" size="small" @click="goPage('binding', -1)">上一页</el-button><span>第 {{ bindingPage }} 页</span><el-button :disabled="!moreBindings" size="small" @click="goPage('binding', 1)">下一页</el-button></div></section>
  <el-drawer v-model="serviceOpen" :title="editing ? '编辑网关' : '添加网关'" size="min(550px, 100vw)"><el-form label-position="top" @submit.prevent="saveService"><el-form-item label="名称"><el-input v-model="service.name" required maxlength="128"/></el-form-item><el-form-item label="来源标识"><el-input v-model="service.issuer" :disabled="!!editing" required maxlength="64"/></el-form-item><el-form-item label="允许来源网段"><el-input v-model="service.cidrs" type="textarea" :rows="5" placeholder="每行一个 CIDR，例如 192.0.2.10/32" required/></el-form-item><el-button type="primary" native-type="submit" :loading="saving">保存网关</el-button></el-form></el-drawer>
  <el-drawer v-model="bindingOpen" title="添加外部主体绑定" size="min(550px, 100vw)"><el-form label-position="top" @submit.prevent="saveBinding"><el-form-item label="网关服务"><el-select v-model="binding.gateway_id"><el-option v-for="g in gateways.filter(g => g.enabled)" :key="g.id" :value="g.id" :label="g.name"/></el-select></el-form-item><el-form-item label="所属项目"><ScopeSelect v-model="binding.project_id"/></el-form-item><el-form-item label="本地 Key 名称"><el-input v-model="binding.name" required maxlength="128"/></el-form-item><el-form-item label="外部租户标识"><el-input v-model="binding.external_tenant_ref" required maxlength="128"/></el-form-item><el-form-item label="外部 Key 标识"><el-input v-model="binding.external_key_ref" required maxlength="128"/></el-form-item><p class="muted">绑定后须在 API 密钥页授权模型、Workflow 和渠道。</p><el-button type="primary" native-type="submit" :loading="saving">保存绑定</el-button></el-form></el-drawer>
  <el-dialog v-model="secretOpen" title="请保存网关凭证" width="min(560px, 94vw)" :close-on-click-modal="false" @closed="secret = ''"><p class="muted">此凭证仅显示一次，请保存到网关的私有配置。</p><div class="secret-box" aria-label="新网关凭证">{{ secret }}</div><template #footer><el-button type="primary" @click="secretOpen = false">已保存，关闭</el-button></template></el-dialog>
  <GatewayCatalogDrawer v-model="catalogOpen" :gateway="catalogGateway"/>
</template>

<style scoped>
@media (max-width: 700px) {
  .page-heading { flex-wrap: wrap; }
  .page-heading > div { max-width: 100%; }
  .actions { flex-wrap: wrap; }
}
</style>
