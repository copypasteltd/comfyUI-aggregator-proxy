<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'
import { api, readableError, time, unwrap, type Channel } from '../api'
import type { components } from '../generated/api'
import WorkflowCatalogBrowser from './WorkflowCatalogBrowser.vue'

type Workflow = components['schemas']['WorkflowView']
type Preview = components['schemas']['WorkflowImportPreviewView']
type TaskType = components['schemas']['NewImportTarget']['task_type']
const visible = defineModel<boolean>({ required: true })
const props = defineProps<{ target: Workflow | null }>()
const emit = defineEmits<{ completed: [] }>()
const formRef = ref<FormInstance>(), channels = ref<Channel[]>([]), busy = ref(false), error = ref('')
const catalogBusy = ref(false), expectedDigest = ref(''), expectedRevision = ref<number | null>(null)
const preview = ref<Preview | null>(null), sourceDigest = ref(''), sourceFormat = ref(''), operationId = ref(''), resumeId = ref(''), status = ref('')
const form = reactive({ channel_id: '', relative_path: '', name: '', task_type: 'text_to_image' as TaskType })
const rules: FormRules = {
  channel_id: [{ required: true, message: '请选择来源渠道', trigger: 'change' }],
  relative_path: [{ required: true, message: '请输入相对 JSON 文件路径', trigger: 'blur' }, { max: 512, message: '路径最多 512 个字符', trigger: 'blur' }],
  name: [{ required: true, whitespace: true, message: '请输入 Workflow 名称', trigger: 'blur' }, { max: 128, message: '名称最多 128 个字符', trigger: 'blur' }],
}
const taskNames: Record<TaskType, string> = { text_to_image: '文生图', image_to_image: '图生图', references_to_image: '多参考文图生图', text_to_video: '文生视频', references_to_video: '多参考文图生视频', text_to_audio: '音频生成', image_to_3d: '单图生成 3D' }
const sourceErrors: Record<string, string> = {
  workflow_export_not_configured: '此渠道未配置 Workflow 导出目录，请先在该 Windows Worker 中登记目录。',
  workflow_export_not_allowed: '文件不存在、不可读或不在渠道允许的导出目录内。',
  workflow_source_too_large: '导出文件超过 1 MiB，请缩小工作流文件后重试。',
  ui_workflow_not_supported: '当前文件是 UI 格式，请在 ComfyUI 中导出 API 格式后重试。',
  invalid_workflow_source: '导出 JSON 无效，请核对文件格式、数值精度与工作流结构。',
  workflow_export_changed: '读取期间文件发生变化，请重新导出后重试。',
  unsupported_command: '此 Worker 尚不支持读取 Workflow，请更新 Worker 后重试。',
}
const selectedChannel = computed(() => channels.value.find(c => c.id === form.channel_id))
const diffGroups = computed(() => ['added', 'removed', 'changed'].map((key, i) => {
  const group = preview.value?.diff.nodes?.[key]
  return { key, label: ['新增节点', '删除节点', '变化节点'][i], count: Number(group?.count ?? 0), ids: (group?.ids ?? []) as string[], truncated: Boolean(group?.truncated) }
}))
let active: AbortController | null = null, pullToken = crypto.randomUUID(), previewToken = crypto.randomUUID(), commitToken = crypto.randomUUID()

function stop() { active?.abort(); active = null; busy.value = false }
async function run(action: (signal: AbortSignal) => Promise<void>) {
  stop(); const controller = new AbortController(); active = controller; busy.value = true; error.value = ''
  const timeout = window.setTimeout(() => controller.abort(), 90_000)
  try { await action(controller.signal) }
  catch (e) {
    if (visible.value && active === controller) error.value = controller.signal.aborted ? '读取已暂停或超时。可用操作 ID 重新检查；关闭窗口不会取消渠道读取。' : readableError(e)
  } finally { window.clearTimeout(timeout); if (active === controller) { active = null; busy.value = false } }
}
function pauseWhenHidden() { if (document.hidden && active) { stop(); status.value = '页面已隐藏，读取已暂停；返回后可重新检查操作。' } }
function delay(signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    if (signal.aborted) { reject(new DOMException('Aborted', 'AbortError')); return }
    const abort = () => { window.clearTimeout(timer); signal.removeEventListener('abort', abort); reject(new DOMException('Aborted', 'AbortError')) }
    const timer = window.setTimeout(() => { signal.removeEventListener('abort', abort); resolve() }, 1500)
    signal.addEventListener('abort', abort, { once: true })
  })
}
async function poll(identifier: string, signal: AbortSignal) {
  sourceDigest.value = ''; preview.value = null; status.value = '等待渠道读取导出文件…'
  while (!signal.aborted) {
    const operation = unwrap(await api.GET('/admin/api/v1/operations/{identifier}', { signal, params: { path: { identifier } } }))
    if (operation.command !== 'pull_workflow' || operation.targets.length !== 1) throw new Error('请选择单个渠道的 Workflow 读取操作')
    const target = operation.targets[0]!
    if (!channels.value.some(c => c.id === target.channel_id)) throw new Error('来源渠道不属于当前可选项目，或渠道已被删除')
    if (operation.status === 'succeeded' && target.receipt?.source_digest) {
      form.channel_id = target.channel_id
      operationId.value = identifier
      sourceDigest.value = target.receipt.source_digest; sourceFormat.value = target.receipt.export_format ?? ''
      status.value = '来源快照已保存，可以预览导入差异'; return
    }
    if (['failed', 'partial_failed'].includes(operation.status)) throw new Error(sourceErrors[target.receipt?.error_code ?? ''] ?? '渠道读取失败，请在操作中心检查结果后重试。')
    await delay(signal)
  }
}
async function readSource() {
  if (catalogBusy.value) return
  if (!(await formRef.value?.validateField(['channel_id', 'relative_path']).catch(() => false))) return
  await run(async signal => {
    if (!operationId.value) {
      const channel = selectedChannel.value; if (!channel) throw new Error('来源渠道已变化，请重新打开窗口')
      const accepted = unwrap(await api.POST('/admin/api/v1/workflow-pulls', { signal, headers: { 'Idempotency-Key': pullToken }, body: { channel_id: channel.id, expected_revision: expectedRevision.value ?? channel.revision, relative_path: form.relative_path, ...(expectedDigest.value ? { expected_source_digest: expectedDigest.value } : {}) } }))
      operationId.value = accepted.operation_id; resumeId.value = accepted.operation_id
    }
    await poll(operationId.value, signal)
  })
}
async function resume() {
  if (!/^op_[a-f0-9]{32}$/.test(resumeId.value)) { error.value = '请输入操作中心显示的完整读取操作 ID'; return }
  await run(async signal => { operationId.value = resumeId.value; await poll(operationId.value, signal) })
}
async function makePreview() {
  if (!props.target && !(await formRef.value?.validateField(['name']).catch(() => false))) return
  await run(async signal => {
    const result = unwrap(await api.POST('/admin/api/v1/workflow-import-previews', { signal, headers: { 'Idempotency-Key': previewToken }, body: { source_operation_id: operationId.value, source_digest: sourceDigest.value, target: props.target ? { mode: 'update', workflow_id: props.target.id, expected_revision: props.target.revision } : { mode: 'create', name: form.name, task_type: form.task_type } } }))
    preview.value = result
    if (result.status !== 'pending') throw new Error('此预览已过期或已导入，请重新读取来源并预览')
  })
}
async function commit() {
  const selected = preview.value; if (!selected || selected.status !== 'pending') return
  await run(async signal => {
    const result = unwrap(await api.POST('/admin/api/v1/workflow-import-previews/{identifier}/commit', { signal, params: { path: { identifier: selected.id } }, headers: { 'Idempotency-Key': commitToken }, body: { candidate_digest: selected.candidate_digest } }))
    ElMessage.success(result.changed ? 'Workflow 草稿已导入，可在编辑器中继续完善' : '草稿内容一致，已保留当前版本')
    emit('completed'); visible.value = false
  })
}
function clearPreview() { preview.value = null; previewToken = crypto.randomUUID(); commitToken = crypto.randomUUID() }
function clearSource() { clearPreview(); sourceDigest.value = ''; operationId.value = ''; status.value = ''; expectedDigest.value = ''; expectedRevision.value = null; pullToken = crypto.randomUUID() }
function chooseFile(entry: components['schemas']['WorkflowCatalogEntry'], revision: number) {
  clearSource(); form.relative_path = entry.relative_path; expectedDigest.value = entry.source_digest ?? ''; expectedRevision.value = revision
  if (!props.target && entry.task_type) form.task_type = entry.task_type
  status.value = '已选择目录文件，读取时会核对内容摘要'
}
watch(() => [form.relative_path, form.channel_id], clearSource, { flush: 'sync' })
watch(() => [form.name, form.task_type], clearPreview)
watch(visible, async opened => {
  stop(); if (!opened) return
  channels.value = []; error.value = ''; preview.value = null; sourceDigest.value = ''; sourceFormat.value = ''; operationId.value = ''; resumeId.value = ''; status.value = ''
  form.channel_id = ''; form.relative_path = ''; form.name = props.target?.name ?? ''; form.task_type = (props.target?.task_type ?? 'text_to_image') as TaskType
  pullToken = crypto.randomUUID(); previewToken = crypto.randomUUID(); commitToken = crypto.randomUUID()
  await run(async signal => {
    const collected: Channel[] = []
    for (let page = 1; page <= 100; page++) {
      const result = unwrap(await api.GET('/admin/api/v1/channels', { signal, params: { query: { page } } }))
      collected.push(...result.data.filter(c => !c.deleted_at && (!props.target || c.project_id === props.target.project_id)))
      if (!result.has_more) { channels.value = collected; return }
    }
    throw new Error('渠道列表超过界面限额，请先缩小管理范围')
  })
})
document.addEventListener('visibilitychange', pauseWhenHidden)
onBeforeUnmount(() => { stop(); document.removeEventListener('visibilitychange', pauseWhenHidden) })
</script>

<template>
  <el-dialog v-model="visible" :title="target ? '从渠道更新 Workflow 草稿' : '从渠道导入 Workflow'" width="min(760px, 96vw)" :close-on-click-modal="false" destroy-on-close>
    <el-alert v-if="error" :title="error" type="error" :closable="false" class="import-message" role="alert" />
    <el-form ref="formRef" :model="form" :rules="rules" label-position="top" :disabled="busy" @submit.prevent="makePreview">
      <div class="policy-grid">
        <el-form-item label="来源渠道" prop="channel_id"><el-select v-model="form.channel_id" filterable class="full" placeholder="选择 Windows / ComfyUI 渠道"><el-option v-for="c in channels" :key="c.id" :value="c.id" :label="c.name" /></el-select></el-form-item>
        <el-form-item label="导出文件相对路径" prop="relative_path"><el-input v-model.trim="form.relative_path" placeholder="例如 images/portrait.json" maxlength="512" /></el-form-item>
      </div>
      <WorkflowCatalogBrowser :channel="selectedChannel" :open="visible" :blocked="busy" @selected="chooseFile" @busy="catalogBusy = $event" />
      <p class="form-help">读取渠道已配置的本地导出目录，支持 API 格式图或完整发布包，文件上限 1 MiB。</p>
      <div class="toolbar"><el-button :loading="busy && !preview" :disabled="catalogBusy" @click="readSource">{{ operationId ? '重新检查读取' : '读取来源' }}</el-button><span v-if="status" role="status" aria-live="polite" class="muted">{{ status }}</span></div>
      <p v-if="operationId" class="id-line">读取操作：{{ operationId }}</p>
      <el-collapse class="import-resume"><el-collapse-item title="已有读取操作：恢复检查"><div class="toolbar"><el-input v-model.trim="resumeId" aria-label="读取操作 ID" placeholder="op_…" maxlength="35" /><el-button @click="resume">检查操作</el-button></div></el-collapse-item></el-collapse>
      <template v-if="sourceDigest">
        <el-divider content-position="left">导入目标</el-divider>
        <p v-if="target">更新「{{ target.name }}」的草稿，当前版本 {{ target.revision }}。</p>
        <div v-else class="policy-grid"><el-form-item label="Workflow 名称" prop="name"><el-input v-model.trim="form.name" maxlength="128" /></el-form-item><el-form-item label="能力类型"><el-select v-model="form.task_type" class="full"><el-option v-for="(label, value) in taskNames" :key="value" :value="value" :label="label" /></el-select></el-form-item></div>
        <p class="form-help">来源格式：{{ sourceFormat === 'manifest-v1' ? '完整发布包' : 'API 格式图' }}。导入后保存在来源渠道所属项目。</p>
        <el-button native-type="submit" :loading="busy && !preview">预览差异</el-button>
      </template>
    </el-form>
    <section v-if="preview" class="import-preview" aria-label="导入预览">
      <el-alert :title="preview.validation.message" :type="preview.validation.publishable ? 'success' : 'warning'" :closable="false" />
      <div class="import-diff"><div v-for="group in diffGroups" :key="group.key"><strong>{{ group.count }}</strong><span>{{ group.label }}</span><p class="form-help">{{ group.ids.join('、') || '无' }}{{ group.truncated ? '…（仅展示前 50 项）' : '' }}</p></div></div>
      <p class="form-help">变化字段：{{ preview.diff.changed_fields.join('、') || '无' }}。预览有效至 {{ time(preview.expires_at) }}。</p>
      <p class="form-help">确认后保存草稿。发布版本、渠道绑定和对外模型需继续按发布流程配置。</p>
    </section>
    <template #footer><el-button @click="visible = false">关闭</el-button><el-button v-if="preview?.status === 'pending'" type="primary" :loading="busy" @click="commit">确认导入草稿</el-button></template>
  </el-dialog>
</template>

<style scoped>
.import-message, .import-resume, .import-preview { margin-bottom: 18px; }
.import-resume, .import-preview { margin-top: 22px; }
.import-diff { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; margin: 20px 0; }
.import-diff > div { min-width: 0; border: 1px solid var(--el-border-color); border-radius: 8px; padding: 14px; }
.import-diff strong { display: block; font-size: 26px; }
.import-diff p { overflow-wrap: anywhere; }
@media (max-width: 540px) { .import-diff { grid-template-columns: 1fr; } }
</style>
