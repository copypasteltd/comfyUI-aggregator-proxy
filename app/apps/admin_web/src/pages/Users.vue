<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh } from '@element-plus/icons-vue'
import { api, readableError, revisionHeaders, time, unwrap } from '../api'
import type { components } from '../generated/api'
import { useSession } from '../session'

type User = components['schemas']['AdminUserView']
type Role = components['schemas']['AdminUserCreate']['role']
const session = useSession(), rows = ref<User[]>([]), page = ref(1), hasMore = ref(false), loading = ref(false), saving = ref(false), error = ref(''), formError = ref(''), drawer = ref(false), editing = ref<User | null>(null)
const roles: { value: Role; label: string; description: string }[] = [{ value: 'administrator', label: '管理员', description: '管理账号、渠道、工作流、密钥与内容' }, { value: 'operator', label: '运营人员', description: '按预设权限查看运行情况并处置任务' }, { value: 'workflow_maintainer', label: 'Workflow 维护人员', description: '维护工作流与模型发布配置' }, { value: 'observer', label: '只读观察者', description: '查看获准的运行信息' }]
const form = ref({ username: '', password: '', role: 'observer' as Role, enabled: true })
let token = ''
async function refresh() {
  loading.value = true
  try { const data = unwrap(await api.GET('/admin/api/v1/admin-users', { params: { query: { page: page.value } } })); rows.value = data.data; hasMore.value = data.has_more; error.value = '' }
  catch (e) { error.value = readableError(e) } finally { loading.value = false }
}
function open(row?: User) { editing.value = row ?? null; form.value = { username: row?.username ?? '', password: '', role: (row?.role as Role) ?? 'observer', enabled: row?.enabled ?? true }; formError.value = ''; token = crypto.randomUUID(); drawer.value = true }
async function save() {
  saving.value = true; formError.value = ''
  try {
    if ((!editing.value || form.value.password) && form.value.password.length < 12) throw new Error('密码至少需要 12 个字符')
    if (!editing.value && !form.value.username.trim()) throw new Error('请填写用户名')
    if (editing.value) {
      await ElMessageBox.confirm(editing.value.id === session.info?.user.id ? '正在修改当前账号。保存后当前会话可能立即失效，需要重新登录。' : '角色、启停状态或密码变化后，该账号的旧会话会立即失效。', '保存账号变更', { type: 'warning', confirmButtonText: '确认保存', cancelButtonText: '返回' })
      unwrap(await api.PATCH('/admin/api/v1/admin-users/{identifier}', { params: { path: { identifier: editing.value.id } }, headers: { ...revisionHeaders(editing.value.revision), 'Idempotency-Key': token }, body: { expected_revision: editing.value.revision, role: form.value.role, enabled: form.value.enabled, ...(form.value.password ? { password: form.value.password } : {}) } }))
    } else unwrap(await api.POST('/admin/api/v1/admin-users', { headers: { 'Idempotency-Key': token }, body: { username: form.value.username.trim(), password: form.value.password, role: form.value.role } }))
    drawer.value = false; form.value.password = ''; ElMessage.success('管理账号已保存'); await refresh()
  } catch (e) { if (e !== 'cancel' && e !== 'close') formError.value = readableError(e) } finally { saving.value = false }
}
async function goPage(delta: number) { page.value += delta; await refresh() }
onMounted(refresh)
</script>

<template>
  <div class="page-heading"><div><p class="eyebrow">GOVERNANCE / USERS</p><h1>管理账号</h1><p class="muted">管理后台访问角色、账号状态与密码。</p></div><div class="toolbar"><el-button :icon="Refresh" :loading="loading" @click="refresh">刷新</el-button><el-button type="primary" :icon="Plus" @click="open()">添加管理账号</el-button></div></div>
  <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon/>
  <section class="panel"><el-table :data="rows" v-loading="loading" empty-text="暂无管理账号" table-layout="auto"><el-table-column label="用户名" min-width="210"><template #default="{ row }"><strong>{{ row.username }}</strong><el-tag v-if="row.id === session.info?.user.id" size="small" class="account-current">当前账号</el-tag><span class="id-line">{{ row.id }}</span></template></el-table-column><el-table-column label="角色" min-width="160"><template #default="{ row }">{{ roles.find(item => item.value === row.role)?.label ?? row.role }}</template></el-table-column><el-table-column label="状态" width="110"><template #default="{ row }"><el-tag :type="row.enabled ? 'success' : 'info'" size="small">{{ row.enabled ? '已启用' : '已停用' }}</el-tag></template></el-table-column><el-table-column label="创建时间" min-width="180"><template #default="{ row }">{{ time(row.created_at) }}</template></el-table-column><el-table-column label="操作" width="90"><template #default="{ row }"><el-button text @click="open(row)">编辑</el-button></template></el-table-column></el-table><div class="pagination"><el-button size="small" :disabled="page === 1 || loading" @click="goPage(-1)">上一页</el-button><span>第 {{ page }} 页</span><el-button size="small" :disabled="!hasMore || loading" @click="goPage(1)">下一页</el-button></div></section>
  <el-drawer v-model="drawer" :title="editing ? '编辑管理账号' : '添加管理账号'" size="min(520px, 100vw)" :close-on-click-modal="!saving" @closed="form.password = ''"><el-alert v-if="formError" :title="formError" type="error" :closable="false" show-icon/><el-form label-position="top" @submit.prevent="save"><el-form-item label="用户名" required><el-input v-model="form.username" :disabled="!!editing" maxlength="128" autocomplete="off"/><p v-if="!editing" class="form-help">创建后不可更改；英文字母统一保存为小写。</p></el-form-item><el-form-item label="角色" required><el-select v-model="form.role" class="full"><el-option v-for="role in roles" :key="role.value" :label="role.label" :value="role.value"/></el-select><p class="form-help">{{ roles.find(role => role.value === form.role)?.description }}</p></el-form-item><el-form-item v-if="editing" label="账号状态"><el-switch v-model="form.enabled" active-text="启用" inactive-text="停用"/></el-form-item><el-form-item :label="editing ? '新密码（留空保留）' : '初始密码'" :required="!editing"><el-input v-model="form.password" type="password" show-password autocomplete="new-password" maxlength="1024"/><p class="form-help">至少 12 个字符。密码变更将使旧会话失效。</p></el-form-item><div class="drawer-footer"><el-button :disabled="saving" @click="drawer = false">取消</el-button><el-button type="primary" native-type="submit" :loading="saving">保存账号</el-button></div></el-form></el-drawer>
</template>
