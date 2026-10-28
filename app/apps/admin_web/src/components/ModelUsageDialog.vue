<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api, ApiError, readableError, revisionHeaders, unwrap } from '../api'
import type { components } from '../generated/api'
import { useSession } from '../session'
import ModelRequestStatus from './ModelRequestStatus.vue'

type Model = components['schemas']['ModelView']
type Usage = components['schemas']['ModelUsage']
type Patch = components['schemas']['ModelDocumentationPatch']
const props = defineProps<{ modelValue: boolean; target: Model | null; active: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: boolean]; saved: [] }>()
const session = useSession(), usage = ref<Usage | null>(null), current = ref<Model | null>(null)
const loading = ref(false), busy = ref(false), error = ref(''), uncertain = ref(false), tab = ref('usage')
const form = reactive({ display_name: '', description: '', example_prompt: '', notes: '', tags: '' })
const pending = ref<{ id: string; key: string; body: Patch } | null>(null)
const editable = computed(() => session.allows('models:write') && props.active && !busy.value && !pending.value)
let alive = true, read: AbortController | null = null, write: AbortController | null = null
function active(id?: string) { return alive && props.modelValue && props.active && !document.hidden && !!session.info && !session.ending && (!id || props.target?.id === id) }
function stopRead() { read?.abort(); read = null; loading.value = false }
function fill(model: Model) {
  current.value = model
  const text = model.documentation
  Object.assign(form, { display_name: text?.display_name ?? '', description: text?.description ?? '', example_prompt: text?.example_prompt ?? '', notes: text?.notes?.join('\n') ?? '', tags: text?.tags?.join(', ') ?? '' })
}
async function refreshUsage() {
  stopRead()
  const id = props.target?.id
  if (!id || !active(id)) return
  const own = new AbortController(); read = own; loading.value = true
  const timer = setTimeout(() => own.abort(), 12000)
  try {
    const value = unwrap(await api.GET('/admin/api/v1/public-models/{identifier}/usage', { params: { path: { identifier: id } }, signal: own.signal }))
    if (active(id) && read === own) usage.value = value
  } catch (e) { if (active(id) && read === own) error.value = own.signal.aborted ? '读取调用说明超时，请重试。' : readableError(e) }
  finally { clearTimeout(timer); if (read === own) { read = null; loading.value = false } }
}
async function save() {
  if (!current.value || !editable.value || !active(current.value.id)) return
  pending.value = { id: current.value.id, key: crypto.randomUUID(), body: { expected_revision: current.value.revision, documentation: { display_name: form.display_name, description: form.description, example_prompt: form.example_prompt, notes: form.notes.split('\n').map(v => v.trim()).filter(Boolean), tags: form.tags.split(/[,，]/).map(v => v.trim()).filter(Boolean) } } }
  await submit()
}
async function submit() {
  const action = pending.value
  if (!action || busy.value || !active(action.id)) return
  const own = new AbortController(); write = own; busy.value = true; error.value = ''
  const timer = setTimeout(() => own.abort(), 12000)
  try {
    const value = unwrap(await api.PATCH('/admin/api/v1/public-models/{identifier}/documentation', { params: { path: { identifier: action.id } }, headers: { ...revisionHeaders(action.body.expected_revision), 'Idempotency-Key': action.key }, body: action.body, signal: own.signal }))
    if (active(action.id) && write === own) { fill(value); pending.value = null; uncertain.value = false; emit('saved'); ElMessage.success('使用说明已保存，发布状态和映射保持'); void refreshUsage() }
  } catch (e) {
    if (active(action.id) && write === own) {
      uncertain.value = !(e instanceof ApiError && e.status < 500)
      if (!uncertain.value) pending.value = null
      error.value = own.signal.aborted ? '保存结果待核对，请使用原请求重试；超时不表示保存失败。' : readableError(e)
    }
  } finally { clearTimeout(timer); if (write === own) { write = null; busy.value = false } }
}
function clearPending() { if (!busy.value) { pending.value = null; uncertain.value = false; emit('saved'); error.value = '已关闭本地重试记录，请刷新模型目录后再编辑。'; emit('update:modelValue', false) } }
watch(() => [props.modelValue, props.target?.id, props.active, session.info?.csrf_token, session.ending] as const, ([open, , enabled, token, ending], previous) => {
  if (!open || !enabled || !token || ending) { stopRead(); write?.abort(); if (pending.value) uncertain.value = true; if (!token || ending) { usage.value = null; current.value = null; pending.value = null; uncertain.value = false; Object.assign(form, { display_name: '', description: '', example_prompt: '', notes: '', tags: '' }); if (open) emit('update:modelValue', false) } return }
  if (previous && (!previous[0] || previous[1] !== props.target?.id || previous[3] !== token)) {
    stopRead(); usage.value = null; error.value = ''; tab.value = 'usage'
    if (pending.value?.id !== props.target?.id || previous[3] !== token) { pending.value = null; uncertain.value = false }
    if (props.target) fill(props.target)
    void refreshUsage()
  } else if (previous && !previous[2]) void refreshUsage()
})
onBeforeUnmount(() => { alive = false; stopRead(); write?.abort() })
</script>

<template>
  <el-dialog :model-value="modelValue" title="模型使用说明" width="min(940px, 96vw)" top="4vh" :close-on-click-modal="false" :show-close="!busy" :close-on-press-escape="!busy" @update:model-value="emit('update:modelValue', $event)">
    <div class="usage-heading"><div><p class="eyebrow li-lab">MODEL / USAGE</p><h2>{{ usage?.display_name || target?.alias }}</h2><p class="mono">{{ target?.alias }}</p></div><el-tag :type="current?.published ? 'success' : 'info'">{{ current?.published ? '已发布' : '未发布' }}</el-tag></div>
    <el-alert v-if="error && !pending" :title="error" type="error" :closable="false"/>
    <ModelRequestStatus :description="pending ? '保存模型公开说明 · ' + target?.alias : null" :uncertain="uncertain" :busy="busy" :active="active()" :error="pending ? error : ''" @retry="submit" @clear="clearPending"/>
    <el-tabs v-model="tab">
      <el-tab-pane label="能力与调用" name="usage">
        <div v-loading="loading" class="usage-body">
          <template v-if="usage">
            <p class="usage-description">{{ usage.description }}</p><ul v-if="usage.notes.length"><li v-for="(note, index) in usage.notes" :key="index">{{ note }}</li></ul>
            <div class="usage-tags"><el-tag v-for="tag in usage.tags" :key="tag" type="info">{{ tag }}</el-tag></div>
            <el-descriptions :column="1" border><el-descriptions-item label="任务类型">{{ usage.task_type }}</el-descriptions-item><el-descriptions-item label="异步生成">POST {{ usage.endpoints?.create ?? "/v1/jobs" }} → GET {{ usage.endpoints?.poll ?? "/v1/jobs/{job_id}" }}</el-descriptions-item><el-descriptions-item label="生成与读取权限">{{ usage.required_scopes.join(' · ') }}</el-descriptions-item><el-descriptions-item label="产物">{{ usage.output.media_types.join(' / ') }} · {{ usage.output.expected_count }} 份主产物 · 成功后保留 8 小时</el-descriptions-item></el-descriptions>
            <h3>允许的规格组合</h3><el-table :data="usage.presets" size="small"><el-table-column prop="quality" label="质量"/><el-table-column prop="size" label="尺寸"/><el-table-column prop="seconds" label="时长（秒）"/><el-table-column prop="audio_format" label="音频格式"/><el-table-column prop="sample_rate" label="采样率"/><el-table-column prop="channels" label="声道"/></el-table>
            <h3>参考素材</h3><p>{{ usage.references.supported ? usage.references.note : '此模型不接受参考素材。' }}</p><p v-if="usage.references.supported" class="form-help">静态 PNG / JPEG / WebP，每份不超过 20 MiB、2500 万像素；上传字段 file，与生成使用同一个 Key。</p><el-table v-if="usage.references.slots.length" :data="usage.references.slots" size="small"><el-table-column prop="path" label="输入位置" min-width="160"/><el-table-column prop="description" label="用法" min-width="240"/><el-table-column label="权重" width="100"><template #default="{ row }">{{ row.weight_supported ? '可传' : '不支持' }}</template></el-table-column></el-table>
            <h3>参数规则</h3><el-table :data="usage.parameter_details" size="small"><el-table-column prop="path" label="参数" min-width="180"/><el-table-column prop="description" label="说明" min-width="260"/><el-table-column label="取值规则" min-width="180"><template #default="{ row }"><code>{{ JSON.stringify(row.rules) }}</code></template></el-table-column></el-table>
            <h3>请求示例</h3><p class="form-help">参考任务须先上传素材，并替换示例中的 asset_id；尺寸、质量和时长必须属于同一条规格。</p><pre class="usage-json" tabindex="0">{{ JSON.stringify(usage.example_request, null, 2) }}</pre>
            <el-collapse><el-collapse-item title="完整请求 JSON Schema" name="schema"><pre class="usage-json" tabindex="0">{{ JSON.stringify(usage.request_schema, null, 2) }}</pre></el-collapse-item></el-collapse>
            <p class="form-help">{{ usage.availability_note }}</p>
          </template><el-empty v-else-if="!loading" description="暂无完整调用说明"/>
        </div>
      </el-tab-pane>
      <el-tab-pane v-if="session.allows('models:write')" label="编辑公开说明" name="documentation">
        <p class="form-help">以下内容会通过业务模型 API 对外公开。仅修改说明不下架模型；规格、参考槽位和参数由已发布 Workflow 自动生成。</p>
        <el-form label-position="top" @submit.prevent="save"><el-form-item label="展示名称"><el-input v-model="form.display_name" aria-label="模型展示名称" maxlength="160" :disabled="!editable"/></el-form-item><el-form-item label="能力说明"><el-input v-model="form.description" aria-label="模型能力说明" type="textarea" :rows="4" maxlength="4000" show-word-limit :disabled="!editable"/></el-form-item><el-form-item label="示例提示词"><el-input v-model="form.example_prompt" aria-label="模型示例提示词" type="textarea" :rows="4" maxlength="8000" :disabled="!editable"/><p class="form-help">填写可实际使用的提示词。结构化音乐谱面等特殊格式可在此提供完整示例。</p></el-form-item><el-form-item label="使用备注（每行一条，最多 12 条）"><el-input v-model="form.notes" aria-label="模型使用备注" type="textarea" :rows="4" maxlength="6011" :disabled="!editable"/></el-form-item><el-form-item label="标签（逗号分隔，最多 12 个）"><el-input v-model="form.tags" aria-label="模型标签" maxlength="6011" :disabled="!editable"/></el-form-item><div class="drawer-footer"><el-button type="primary" native-type="submit" :disabled="!editable" :loading="busy">保存公开说明</el-button></div></el-form>
      </el-tab-pane>
    </el-tabs>
    <template #footer><el-button :disabled="busy" @click="emit('update:modelValue', false)">关闭</el-button><el-button :disabled="loading || busy || !active()" @click="error = ''; refreshUsage()">刷新调用说明</el-button></template>
  </el-dialog>
</template>

<style scoped>
.usage-heading { display: flex; justify-content: space-between; align-items: center; gap: 20px; }
.usage-heading h2 { margin: 8px 0; overflow-wrap: anywhere; }
.usage-body { min-height: 160px; }
.usage-body h3 { margin: 24px 0 12px; }
.usage-description { white-space: pre-wrap; line-height: 1.7; }
.usage-tags { display: flex; gap: 8px; flex-wrap: wrap; margin: 12px 0; }
.usage-json { padding: 16px; border: 1px solid var(--rc-line); border-radius: 12px; background: var(--rc-canvas); overflow: auto; max-height: 440px; font-size: 12px; line-height: 1.6; }
.usage-body code { overflow-wrap: anywhere; white-space: normal; }
.usage-body li { white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.7; }
</style>
