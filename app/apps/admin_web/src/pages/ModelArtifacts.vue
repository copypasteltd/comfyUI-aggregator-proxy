<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh } from '@element-plus/icons-vue'
import { api, readableError, revisionHeaders, time, unwrap } from '../api'
import type { components } from '../generated/api'
import { useSession } from '../session'
import ScopeSelect from '../components/ScopeSelect.vue'

type FileRecord = components['schemas']['ModelArtifactView']
type Kind = components['schemas']['ModelArtifactCreate']['kind']
const session = useSession(), rows = ref<FileRecord[]>([]), page = ref(1), hasMore = ref(false), includeArchived = ref(false), loading = ref(false), saving = ref(false), drawer = ref(false), editing = ref<FileRecord | null>(null), error = ref(''), formError = ref('')
const kinds: { value: Kind; label: string }[] = [{ value: 'checkpoint', label: 'Checkpoint' }, { value: 'diffusion_model', label: '扩散模型' }, { value: 'unet', label: 'UNet' }, { value: 'vae', label: 'VAE' }, { value: 'lora', label: 'LoRA' }, { value: 'text_encoder', label: '文本编码器' }, { value: 'clip', label: 'CLIP' }, { value: 'embedding', label: 'Embedding' }, { value: 'upscaler', label: '放大模型' }, { value: 'controlnet', label: 'ControlNet' }, { value: 'other', label: '其他' }]
const form = ref({ project_id: '', name: '', kind: 'checkpoint' as Kind, relative_path: '', sha256: '', bytes: 1, enabled: true })
let token = ''
async function refresh() {
  loading.value = true
  try { const data = unwrap(await api.GET('/admin/api/v1/model-artifacts', { params: { query: { page: page.value, include_archived: includeArchived.value } } })); rows.value = data.data; hasMore.value = data.has_more; error.value = '' }
  catch (e) { error.value = readableError(e) } finally { loading.value = false }
}
function open(row?: FileRecord) { editing.value = row ?? null; form.value = { project_id: row?.project_id ?? session.info?.scopes[0]?.project_id ?? '', name: row?.name ?? '', kind: (row?.kind as Kind) ?? 'checkpoint', relative_path: row?.relative_path ?? '', sha256: row?.sha256 ?? '', bytes: row?.bytes ?? 1, enabled: row?.enabled ?? true }; token = crypto.randomUUID(); formError.value = ''; drawer.value = true }
async function save() {
  saving.value = true; formError.value = ''
  try {
    if (editing.value) unwrap(await api.PATCH('/admin/api/v1/model-artifacts/{identifier}', { params: { path: { identifier: editing.value.id } }, headers: { ...revisionHeaders(editing.value.revision), 'Idempotency-Key': token }, body: { expected_revision: editing.value.revision, name: form.value.name, enabled: form.value.enabled } }))
    else {
      const scope = session.info?.scopes.find(item => item.project_id === form.value.project_id)
      if (!scope) throw new Error('请选择所属项目')
      if (!/^[a-f0-9]{64}$/.test(form.value.sha256)) throw new Error('请填写 64 位小写 SHA-256')
      unwrap(await api.POST('/admin/api/v1/model-artifacts', { headers: { 'Idempotency-Key': token }, body: { tenant_id: scope.tenant_id, project_id: scope.project_id, name: form.value.name.trim(), kind: form.value.kind, relative_path: form.value.relative_path.trim(), sha256: form.value.sha256, bytes: form.value.bytes } }))
    }
    drawer.value = false; ElMessage.success('模型文件登记已保存'); await refresh()
  } catch (e) { formError.value = readableError(e) } finally { saving.value = false }
}
async function archive(row: FileRecord) {
  try {
    await ElMessageBox.confirm('归档将隐藏这条模型文件登记。真实设备上的文件继续保留；冻结 Workflow 引用的内容会拒绝归档。', '归档模型文件登记', { type: 'warning', confirmButtonText: '确认归档', cancelButtonText: '返回' })
    unwrap(await api.DELETE('/admin/api/v1/model-artifacts/{identifier}', { params: { path: { identifier: row.id }, query: { revision: row.revision } }, headers: revisionHeaders(row.revision) })); ElMessage.success('登记已归档'); await refresh()
  } catch (e) { if (e !== 'cancel' && e !== 'close') ElMessage.error(readableError(e)) }
}
async function search() { page.value = 1; await refresh() }
async function goPage(delta: number) { page.value += delta; await refresh() }
onMounted(refresh)
</script>

<template>
  <p class="muted">可在 <RouterLink to="/channels">Windows 渠道</RouterLink> 的详情中扫描本机模型，核验 SHA-256 后直接登记。</p>
  <div class="page-heading"><div><p class="eyebrow li-lab">CAPABILITIES / MODEL FILES</p><h1>模型文件目录</h1><p class="muted">登记权重版本，并查看冻结 Workflow 的引用情况。</p></div><div class="toolbar"><el-button :icon="Refresh" :loading="loading" @click="refresh">刷新</el-button><el-button v-if="session.allows('models:write')" type="primary" :icon="Plus" @click="open()">登记模型文件</el-button></div></div>
  <el-alert v-if="error" :title="error" type="error" :closable="false"/>
  <section class="panel li-card"><div class="toolbar"><el-checkbox v-model="includeArchived" @change="search">显示已归档</el-checkbox><span class="form-help">登记后仍需在目标渠道验证文件与运行环境。</span></div><el-table :data="rows" v-loading="loading" empty-text="暂无模型文件登记" table-layout="auto"><el-table-column label="文件 / 路径" min-width="270"><template #default="{ row }"><strong>{{ row.name }}</strong><span class="id-line">{{ row.relative_path }}</span><span class="id-line">{{ row.id }}</span></template></el-table-column><el-table-column label="类别" min-width="120"><template #default="{ row }">{{ kinds.find(item => item.value === row.kind)?.label ?? row.kind }}</template></el-table-column><el-table-column label="内容标识" min-width="250"><template #default="{ row }"><span class="id-line mono">{{ row.sha256 ?? '未完整登记' }}</span><span class="id-line">{{ row.bytes?.toLocaleString() ?? '未知' }} 字节</span></template></el-table-column><el-table-column label="冻结引用" width="95"><template #default="{ row }">{{ row.referenced_release_count }} 个</template></el-table-column><el-table-column label="状态" width="95"><template #default="{ row }"><el-tag :type="row.deleted_at ? 'info' : row.enabled ? 'success' : 'warning'" size="small">{{ row.deleted_at ? '已归档' : row.enabled ? '已启用' : '已停用' }}</el-tag></template></el-table-column><el-table-column v-if="session.allows('models:write')" label="操作" min-width="135" fixed="right"><template #default="{ row }"><template v-if="!row.deleted_at"><el-button text @click="open(row)">编辑</el-button><el-button text type="danger" :disabled="row.referenced_release_count > 0" title="冻结引用存在时保留登记" @click="archive(row)">归档</el-button></template><span v-else>{{ time(row.deleted_at) }}</span></template></el-table-column></el-table><div class="pagination"><el-button size="small" :disabled="page === 1 || loading" @click="goPage(-1)">上一页</el-button><span>第 {{ page }} 页</span><el-button size="small" :disabled="!hasMore || loading" @click="goPage(1)">下一页</el-button></div></section>
  <el-drawer v-model="drawer" :title="editing ? '编辑模型文件登记' : '登记模型文件'" size="min(600px, 100vw)"><el-alert v-if="formError" :title="formError" type="error" :closable="false"/><el-form label-position="top" @submit.prevent="save"><el-form-item label="显示名称" required><el-input v-model="form.name" maxlength="256"/></el-form-item><el-form-item label="所属项目" required><ScopeSelect v-model="form.project_id" :disabled="!!editing" class="full"/></el-form-item><el-form-item label="文件类别" required><el-select v-model="form.kind" :disabled="!!editing" class="full"><el-option v-for="kind in kinds" :key="kind.value" :value="kind.value" :label="kind.label"/></el-select></el-form-item><el-form-item label="models 目录内相对路径" required><el-input v-model="form.relative_path" :disabled="!!editing" placeholder="checkpoints/model.safetensors"/></el-form-item><el-form-item label="SHA-256" required><el-input v-model="form.sha256" :disabled="!!editing" maxlength="64" placeholder="文件的 64 位小写摘要"/></el-form-item><el-form-item label="文件字节数" required><el-input-number v-model="form.bytes" :disabled="!!editing" :min="1" :max="1099511627776" :precision="0" controls-position="right"/></el-form-item><p v-if="editing" class="form-help">文件内容或路径变化时请登记新记录，保留原发布版本的文件标识。</p><el-form-item v-if="editing" label="启用登记"><el-checkbox v-model="form.enabled">启用</el-checkbox></el-form-item><div class="drawer-footer"><el-button @click="drawer = false">取消</el-button><el-button type="primary" native-type="submit" :loading="saving">保存登记</el-button></div></el-form></el-drawer>
</template>
