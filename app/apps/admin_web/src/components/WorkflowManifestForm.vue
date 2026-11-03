<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { ElMessageBox } from 'element-plus'
import { readableError } from '../api'
import { ExactNumber, UnfinishedNumber, exactObject, formatExactDraft, isRecord, numericText, parseExactJson, stringifyExactJson, type JsonObject, type JsonValue } from '../workflowJson'
import WorkflowScalarValues from './WorkflowScalarValues.vue'

const props = defineProps<{ modelValue: string; taskType: string; readonly: boolean; available: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [string]; dirty: [boolean] }>()
type List = 'bindings' | 'presets' | 'models' | 'custom_nodes' | 'runtime_profiles' | 'input_slots' | 'text_slots'
type Tab = List | 'output' | 'supported_parameters' | 'workflow_api' | 'audio_policy' | 'context'
type Field = { key: string; label: string; kind?: 'numeric' | 'csv' | 'select' | 'node' | 'input' | 'boolean'; choices?: string[]; optional?: boolean; numeric?: boolean }
const labels: Record<Tab, string> = { bindings: '参数绑定', input_slots: '素材槽位', text_slots: '文本槽位', presets: '规格预设', output: '输出', audio_policy: '视频声音', context: '续拍上下文', models: '模型依赖', custom_nodes: '自定义节点', runtime_profiles: '运行画像', supported_parameters: '允许参数', workflow_api: 'API 图' }
const doc = ref<JsonObject | null>(null), loadedText = ref(''), loadedType = ref(''), dirty = ref(false), error = ref(''), notice = ref(''), activeTab = ref<Tab>('bindings'), graphText = ref('{}'), graphChanged = ref(false), valuesExpanded = ref<number | null>(null)
const pages = reactive<Record<List, number>>({ bindings: 1, presets: 1, models: 1, custom_nodes: 1, runtime_profiles: 1, input_slots: 1, text_slots: 1 })
const modern = computed(() => doc.value?.format_version === '1.3')
const model3d = computed(() => props.taskType === 'image_to_3d')
const audio = computed(() => props.taskType === 'text_to_audio'), video = computed(() => props.taskType.includes('video'))
const stale = computed(() => !!doc.value && (props.modelValue !== loadedText.value || props.taskType !== loadedType.value))
const packaged = computed(() => !!doc.value && (Object.hasOwn(doc.value, 'format_version') || Object.hasOwn(doc.value, 'workflow_api')))
const lock = computed(() => doc.value && isRecord(doc.value.dependency_lock) ? doc.value.dependency_lock : null)
const apiOnly = computed(() => lock.value?.mode === 'comfy-api-only-v1')
const output = computed(() => doc.value && isRecord(doc.value.output) ? doc.value.output : null)
const context = computed(() => doc.value && isRecord(doc.value.context) ? doc.value.context : null)
const audioPolicy = computed(() => output.value && isRecord(output.value.audio_policy) ? output.value.audio_policy : null)
const visibleLabels = computed(() => Object.fromEntries(Object.entries(labels).filter(([key]) => !['audio_policy', 'context'].includes(key) || modern.value && video.value)))
function initializeContext() { if (doc.value && writable()) put(doc.value, 'context', { family: 'h3-av-v1', compatibility: '', load: false }) }
function initializeAudioPolicy() { if (output.value && writable()) put(output.value, 'audio_policy', { mode: 'generated', description: '' }) }
function audioMode(value: string) { if (!audioPolicy.value || !writable()) return; put(audioPolicy.value, 'mode', value); if (value !== 'source_video' && value !== 'reference_audio') unset(audioPolicy.value, 'source_slot') }
const graph = computed(() => doc.value && isRecord(doc.value.workflow_api) ? doc.value.workflow_api : null)
const nodeOptions = computed(() => Object.entries(graph.value ?? {}).map(([id, node]) => ({ value: id, label: `${id} · ${isRecord(node) && typeof node.class_type === 'string' ? node.class_type : '未知节点'}` })))
const listTab = computed(() => (['bindings', 'presets', 'models', 'custom_nodes', 'runtime_profiles', 'input_slots', 'text_slots'] as string[]).includes(activeTab.value) ? activeTab.value as List : null)
let disposed = false, confirming = false, epoch = 0
function writable() { return props.available && !props.readonly && !disposed }
function changed() { if (writable()) { dirty.value = true; notice.value = '' } }
function text(row: JsonObject, key: string) { return typeof row[key] === 'boolean' ? String(row[key]) : typeof row[key] === 'string' ? row[key] as string : numericText(row[key]) }
function setCsv(row: JsonObject, key: string, value: string) { put(row, key, value.split(',').map((part: string) => part.trim()).filter(Boolean)) }
function csv(row: JsonObject, key: string) { const value = row[key]; return Array.isArray(value) ? value.map(v => typeof v === 'string' ? v : numericText(v)).join(', ') : '' }
function put(row: JsonObject, key: string, value: JsonValue) { if (!writable()) return; row[key] = value; changed() }
function unset(row: JsonObject, key: string) { if (!writable()) return; delete row[key]; changed() }
function numeric(row: JsonObject, key: string, value: string, optional = false) { if (!writable()) return; if (!value.trim() && optional) { unset(row, key); return }; try { const next = parseExactJson(value, 8192); if (!(next instanceof ExactNumber)) throw new Error(); put(row, key, next) } catch { put(row, key, new UnfinishedNumber(value)) } }
function list(kind: List): JsonObject[] | null { const value = kind === 'models' && apiOnly.value ? lock.value?.model_inputs : kind === 'models' || kind === 'custom_nodes' ? lock.value?.[kind] : doc.value?.[kind]; return Array.isArray(value) && value.every(isRecord) ? value : null }
const items = computed(() => listTab.value ? list(listTab.value) : null)
const shown = computed(() => !items.value || !listTab.value ? [] : items.value.slice((pages[listTab.value] - 1) * 50, pages[listTab.value] * 50).map((value, index) => ({ value, index: (pages[listTab.value!] - 1) * 50 + index })))
function inputOptions(row: JsonObject) { const node = graph.value?.[text(row, 'node_id')]; return isRecord(node) && isRecord(node.inputs) ? Object.keys(node.inputs).map(name => ({ label: name, value: name })) : [] }
function invalidateProfile(row: JsonObject) { if (!writable()) return; row.approved = false; for (const key of ['evidence_id', 'peak_vram_bytes', 'warm_p95_milliseconds']) delete row[key] }
function update(row: JsonObject, field: Field, value: string) {
  if (!writable()) return
  if (activeTab.value === 'runtime_profiles') invalidateProfile(row)
  if (field.optional && !value.trim()) { unset(row, field.key); return }
  if (field.kind === 'numeric' || field.numeric) numeric(row, field.key, value, field.optional)
  else if (field.kind === 'boolean') put(row, field.key, value === 'true')
  else if (field.kind === 'csv') put(row, field.key, value.split(',').map(v => v.trim()).filter(Boolean))
  else put(row, field.key, value)
}
const namedSources = computed(() => [...(list('input_slots') ?? []).map(row => `media:${text(row, 'name')}`), ...(list('text_slots') ?? []).map(row => `text:${text(row, 'name')}`)])
const slotFields: Field[] = [{ key: 'name', label: '槽位标识' }, { key: 'label', label: '显示名称' }, { key: 'description', label: '功能与用法' }, { key: 'required', label: '必填', kind: 'boolean', choices: ['true', 'false'] }]
const fields = computed<Field[]>(() => {
  if (activeTab.value === 'bindings') return [{ key: 'source', label: '来源', kind: 'select', choices: [...(model3d.value ? ['source_image', 'seed', 'output_prefix'] : ['prompt', 'negative_prompt', 'seed', 'output_prefix', 'seconds', ...(!audio.value ? ['width', 'height', 'fps', 'strength', 'source_image', 'mask', ...Array.from({ length: 4 }, (_, i) => `reference_${i}`), ...Array.from({ length: 4 }, (_, i) => `reference_weight_${i}`)] : [])]), ...(modern.value ? ['output_basename', ...(context.value?.load === true ? ['context_latent'] : [])] : []), ...namedSources.value] }, { key: 'node_id', label: '节点', kind: 'node' }, { key: 'input_name', label: '输入', kind: 'input' }, ...(modern.value ? [{ key: 'pointer', label: 'JSON Pointer（可选）', optional: true }] : [])]
  if (activeTab.value === 'input_slots') return [...slotFields, { key: 'kind', label: '素材类型', kind: 'select', choices: ['image', 'video', 'audio', 'model'] }, { key: 'max_bytes', label: '最大字节数', kind: 'numeric' }, { key: 'mime_types', label: '允许 MIME（逗号分隔）', kind: 'csv' }, { key: 'path_mode', label: '节点路径模式', kind: 'select', choices: ['comfy_relative', 'comfy_absolute'] }]
  if (activeTab.value === 'text_slots') return [...slotFields, { key: 'max_length', label: '最大字符数', kind: 'numeric' }]
  if (activeTab.value === 'presets') return [{ key: 'quality', label: '质量', kind: 'select', choices: ['draft', 'standard', 'high'] }, ...(!audio.value && !model3d.value ? [{ key: 'size', label: '尺寸' }] : []), ...(audio.value || video.value ? [{ key: 'seconds', label: '时长秒', kind: 'numeric' as const }] : []), ...(audio.value ? [{ key: 'audio_format', label: '音频格式', kind: 'select' as const, choices: ['wav', 'flac', 'mp3', 'opus'] }, { key: 'sample_rate', label: '采样率', kind: 'numeric' as const }, { key: 'channels', label: '声道', kind: 'select' as const, choices: ['1', '2'], numeric: true }] : [])]
  if (activeTab.value === 'models') return apiOnly.value ? [{ key: 'class_type', label: '节点类' }, { key: 'input_name', label: '模型输入名' }, { key: 'value', label: '模型选项' }] : [{ key: 'path', label: '相对路径' }, { key: 'sha256', label: 'SHA-256' }, { key: 'bytes', label: '文件字节数', kind: 'numeric' }]
  if (activeTab.value === 'custom_nodes') return [{ key: 'directory', label: '目录' }, { key: 'commit', label: '提交哈希' }]
  if (activeTab.value === 'runtime_profiles') return [{ key: 'id', label: '画像标识' }, { key: 'gpu_hardware_id', label: apiOnly.value ? 'GPU 资源键' : '物理 GPU 标识' }, { key: 'execution_timeout_seconds', label: '执行超时秒', kind: 'numeric' }]
  return []
})
const outputFields: Field[] = [{ key: 'adapter', label: '适配器', kind: 'select', choices: ['save-image-v1', 'video-files-v1', 'audio-files-v1', 'model3d-glb-v1'] }, { key: 'final_node_ids', label: '最终节点（逗号分隔）', kind: 'csv' }, { key: 'history_field', label: 'History 结果字段', kind: 'select', choices: ['images', 'gifs', 'videos', 'audio', '3d'] }, { key: 'expected_count', label: '主产物数量（冻结仅允许 1）', kind: 'select', choices: ['1'], numeric: true }, { key: 'max_bytes', label: '单产物最大字节数', kind: 'numeric' }, { key: 'mime_types', label: 'MIME（逗号分隔）', kind: 'csv' }]
function defaultEntry(kind: List): JsonObject {
  if (kind === 'input_slots') return { name: '', kind: 'image', label: '', description: '', required: true, max_bytes: new ExactNumber('20971520'), mime_types: ['image/png', 'image/jpeg', 'image/webp'], path_mode: 'comfy_relative' }
  if (kind === 'text_slots') return { name: '', label: '', description: '', required: true, max_length: new ExactNumber('8000') }
  if (kind === 'bindings') return { source: model3d.value ? 'source_image' : 'prompt', node_id: '', input_name: '' }
  if (kind === 'presets') return { quality: 'standard', ...(!audio.value && !model3d.value ? { size: '' } : {}), ...(audio.value || video.value ? { seconds: new ExactNumber('2') } : {}), ...(audio.value ? { audio_format: 'wav', sample_rate: new ExactNumber('24000'), channels: new ExactNumber('1') } : {}), values: {} }
  if (kind === 'models') return apiOnly.value ? { class_type: '', input_name: '', value: '' } : { path: '', sha256: '' }
  if (kind === 'custom_nodes') return { directory: '', commit: '' }
  return { id: '', gpu_hardware_id: '', execution_timeout_seconds: new ExactNumber('300'), approved: false }
}
function add(kind: List) { if (!writable()) return; const rows = list(kind); if (!rows) return; rows.push(defaultEntry(kind)); pages[kind] = Math.ceil(rows.length / 50); valuesExpanded.value = null; changed() }
function remove(kind: List, index: number) { if (!writable()) return; const rows = list(kind); if (!rows) return; rows.splice(index, 1); pages[kind] = Math.max(1, Math.min(pages[kind], Math.ceil(rows.length / 50))); valuesExpanded.value = null; changed() }
function defaults(apiGraph: JsonObject): JsonObject { return { format_version: model3d.value ? '1.2' : audio.value ? '1.1' : '1.0', task_type: props.taskType, workflow_api: apiGraph, bindings: [], output: { adapter: model3d.value ? 'model3d-glb-v1' : audio.value ? 'audio-files-v1' : video.value ? 'video-files-v1' : 'save-image-v1', final_node_ids: [], expected_count: new ExactNumber('1'), history_field: model3d.value ? '3d' : audio.value ? 'audio' : video.value ? 'videos' : 'images', max_bytes: new ExactNumber(model3d.value ? '536870912' : audio.value ? '268435456' : video.value ? '2147483648' : '67108864'), mime_types: [model3d.value ? 'model/gltf-binary' : audio.value ? 'audio/wav' : video.value ? 'video/mp4' : 'image/png'] }, presets: [], dependency_lock: { comfy_commit: '', models: [], custom_nodes: [] }, runtime_profiles: [], supported_parameters: ['quality', 'seed', 'n', ...(!audio.value && !model3d.value ? ['size'] : []), ...(audio.value || video.value ? ['seconds'] : [])] } }
const canInitialize = computed(() => !!doc.value && !packaged.value && (Object.keys(doc.value).length === 0 || Object.values(doc.value).every(value => isRecord(value) && typeof value.class_type === 'string' && isRecord(value.inputs))))
function initialize() { if (!writable() || !doc.value || !canInitialize.value) return; doc.value = defaults(doc.value); graphText.value = stringifyExactJson(doc.value.workflow_api, 2); graphChanged.value = false; changed(); notice.value = '已创建本地发布包结构，请补齐参数绑定、规格、输出和依赖。' }
function enableInputs() { if (!writable() || !doc.value || modern.value) return; doc.value.format_version = '1.3'; doc.value.input_slots = []; doc.value.text_slots = []; for (const profile of list('runtime_profiles') ?? []) invalidateProfile(profile); changed(); notice.value = '已启用 1.3 命名输入。请配置槽位及对应绑定，保存后重新冻结与验证。' }
async function load() {
  if (!props.available || disposed || confirming) return
  const ticket = ++epoch, source = props.modelValue, task = props.taskType
  if (dirty.value) { confirming = true; try { await ElMessageBox.confirm('重新载入会放弃尚未应用的表单修改。', '重新载入配置', { confirmButtonText: '放弃并载入', cancelButtonText: '继续编辑' }) } catch { return } finally { confirming = false } }
  if (disposed || ticket !== epoch || !props.available) return
  if (source !== props.modelValue || task !== props.taskType) { error.value = '完整 JSON 或能力已变化，请重新载入'; return }
  try { const value = exactObject(source); const graphValue = isRecord(value.workflow_api) ? value.workflow_api : {}; graphText.value = formatExactDraft(graphValue); doc.value = value; loadedText.value = source; loadedType.value = task; dirty.value = false; error.value = ''; notice.value = ''; graphChanged.value = false; valuesExpanded.value = null; for (const key of Object.keys(pages) as List[]) pages[key] = 1 }
  catch (e) { error.value = readableError(e) }
}
function apply() {
  if (!writable() || !doc.value || stale.value) return
  error.value = ''
  try {
    if (doc.value.task_type !== props.taskType || ![model3d.value ? '1.2' : audio.value ? '1.1' : '1.0', '1.3'].includes(String(doc.value.format_version))) throw new Error('发布包的能力或版本与当前选择不匹配，请在完整 JSON 中修正')
    if (graphChanged.value) doc.value.workflow_api = exactObject(graphText.value)
    let next: string
    try { next = stringifyExactJson(doc.value, 2); exactObject(next) } catch { next = stringifyExactJson(doc.value); exactObject(next) }
    loadedText.value = next; loadedType.value = props.taskType; emit('update:modelValue', next); dirty.value = false; graphChanged.value = false; notice.value = '表单已应用到本地草稿，点击保存草稿提交。'
  } catch (e) { error.value = readableError(e) }
}
function discard() { epoch++; doc.value = null; loadedText.value = ''; loadedType.value = ''; graphText.value = '{}'; graphChanged.value = false; dirty.value = false; error.value = ''; notice.value = ''; valuesExpanded.value = null }
watch(dirty, value => emit('dirty', value), { immediate: true })
onBeforeUnmount(() => { disposed = true; if (confirming) ElMessageBox.close(); discard(); emit('dirty', false) })
</script>
<template>
  <section class="manifest-form" aria-label="Workflow 参数配置">
    <div class="manifest-heading"><div><h3>参数与元数据</h3><p class="form-help">从完整 JSON 载入配置，应用后再保存草稿。</p></div><el-button :disabled="!available" @click="load">{{ doc ? '重新载入配置' : '载入当前 JSON' }}</el-button></div>
    <el-alert v-if="error" :title="error" type="error" :closable="false"/>
    <el-alert v-if="stale" title="完整 JSON 或能力已变化。当前表单已过期，请重新载入后再应用。" type="warning" :closable="false"/>
    <el-alert v-if="notice" :title="notice" type="info" :closable="false"/>
    <template v-if="doc">
      <p class="form-help">能力 {{ taskType }} · 发布格式 {{ typeof doc.format_version === 'string' ? doc.format_version : '尚未初始化' }} <el-tag v-if="dirty" type="warning" size="small">尚未应用</el-tag><el-tag v-if="readonly" type="info" size="small">只读</el-tag></p>
      <el-button v-if="canInitialize && !readonly" :disabled="!available || stale" @click="initialize">{{ Object.keys(doc).length ? '将 API 图包装为发布包' : '初始化空白发布包' }}</el-button>
      <el-alert v-else-if="!packaged" title="当前 JSON 不是发布包或可识别的 API 图，请在完整 JSON 中修正。" type="warning" :closable="false"/>
      <template v-if="packaged">
        <el-button v-if="!modern && !readonly" :disabled="!available || stale" @click="enableInputs">启用命名素材与文本输入</el-button>
        <p v-if="modern" class="form-help">素材通过固定槽位上传，文本槽用于脚本或曲谱；每类最多 16 个。发布后仅支持新输入协议的 Worker 可领取。</p>
        <el-tabs v-model="activeTab"><el-tab-pane v-for="(label, key) in visibleLabels" :key="key" :name="key" :label="key === 'custom_nodes' && apiOnly ? '节点目录' : label"/></el-tabs>
        <el-form label-position="top">
          <template v-if="listTab">
            <template v-if="apiOnly && lock && (listTab === 'models' || listTab === 'custom_nodes')"><p class="form-help">此渠道按 ComfyUI API 目录接入，尚未核验主机文件。配置实际版本、节点类和模型选项后，仍须完成真实生成验证。</p><el-form-item label="ComfyUI API 版本"><el-input :disabled="readonly || !available" :model-value="text(lock, 'comfy_version')" aria-label="ComfyUI API 版本" @update:model-value="put(lock, 'comfy_version', $event)"/></el-form-item></template>
            <el-form-item v-if="apiOnly && lock && listTab === 'custom_nodes'" label="API 节点类（逗号分隔）"><el-input :disabled="readonly || !available" :model-value="csv(lock, 'node_classes')" aria-label="API 节点类" type="textarea" :rows="5" @update:model-value="setCsv(lock, 'node_classes', $event)"/></el-form-item>
            <el-alert v-else-if="!items" title="本节 JSON 结构不支持表单编辑，原数据已保留，请在完整 JSON 中修正。" type="warning" :closable="false"/>
            <template v-else>
              <template v-if="!apiOnly && (listTab === 'models' || listTab === 'custom_nodes')"><el-form-item v-if="lock" label="ComfyUI 提交哈希"><el-input :disabled="readonly || !available" :model-value="text(lock, 'comfy_commit')" aria-label="ComfyUI 提交哈希" @update:model-value="put(lock, 'comfy_commit', $event)"/></el-form-item><p class="form-help">填入已核对的版本和文件信息；此处不会下载模型或安装节点。</p></template>
              <p v-if="listTab === 'bindings'" class="form-help">参数 → 节点输入。1.3 可填写 JSON Pointer，只替换冻结 JSON 内已有的单个字段；留空表示整个输入。必需绑定及图连通性由冻结校验核对。</p>
              <p v-if="listTab === 'runtime_profiles'" class="form-help">修改画像标识、GPU 或预算会清除该条草稿的测量/批准引用。运行批准需在渠道验证中另行完成。</p>
              <article v-for="entry in shown" :key="entry.index" class="manifest-entry" :data-entry="listTab" :data-index="entry.index">
                <div class="entry-heading"><strong>{{ labels[listTab] }} {{ entry.index + 1 }}</strong><el-button :disabled="readonly || !available" text type="danger" @click="remove(listTab, entry.index)">移除条目</el-button></div>
                <div class="metadata-grid" :class="{ binding: listTab === 'bindings' }">
                  <el-form-item v-for="field in fields" :key="field.key" :label="field.label">
                    <el-select-v2 :disabled="readonly || !available" v-if="field.kind === 'node' || field.kind === 'input'" :model-value="text(entry.value, field.key)" :aria-label="labels[listTab] + ' ' + (entry.index + 1) + ' ' + field.label" :options="field.kind === 'node' ? nodeOptions : inputOptions(entry.value)" filterable class="full" @update:model-value="update(entry.value, field, String($event))"/>
                    <el-select :disabled="readonly || !available" v-else-if="field.kind === 'select' || field.kind === 'boolean'" :model-value="text(entry.value, field.key)" :aria-label="labels[listTab] + ' ' + (entry.index + 1) + ' ' + field.label" class="full" @update:model-value="update(entry.value, field, $event)"><el-option v-for="choice in field.choices" :key="choice" :label="field.kind === 'boolean' ? choice === 'true' ? '必填' : '可选（使用冻结默认值）' : choice" :value="choice"/></el-select>
                    <el-input :disabled="readonly || !available" v-else :model-value="field.kind === 'csv' ? csv(entry.value, field.key) : field.kind === 'numeric' ? numericText(entry.value[field.key]) : text(entry.value, field.key)" :aria-label="labels[listTab] + ' ' + (entry.index + 1) + ' ' + field.label" @update:model-value="update(entry.value, field, $event)"/>
                  </el-form-item>
                </div>
                <template v-if="listTab === 'presets'"><el-button :disabled="!available" @click="valuesExpanded = valuesExpanded === entry.index ? null : entry.index">{{ valuesExpanded === entry.index ? '收起固定参数' : '编辑固定参数' }}</el-button><WorkflowScalarValues v-if="valuesExpanded === entry.index && isRecord(entry.value.values)" :model-value="entry.value.values" :disabled="readonly || !available" :label="'预设 ' + (entry.index + 1)" @update:model-value="put(entry.value, 'values', $event)"/><p v-else-if="!isRecord(entry.value.values)" class="form-help">固定参数不是 JSON 对象，原值已保留，请在完整 JSON 中修正。</p></template>
                <p v-if="listTab === 'runtime_profiles'" class="form-help">批准声明：{{ entry.value.approved === true ? '已标记，渠道运行批准另行核对' : '待验证' }} · 证据 {{ typeof entry.value.evidence_id === 'string' ? entry.value.evidence_id : '无' }} · 峰值显存 {{ numericText(entry.value.peak_vram_bytes) || '未知' }} · warm p95 {{ numericText(entry.value.warm_p95_milliseconds) || '未知' }}</p>
              </article>
              <el-button v-if="!readonly" :disabled="!available" @click="add(listTab)">添加{{ labels[listTab] }}</el-button>
              <div class="pagination metadata-pages"><el-button :disabled="pages[listTab] === 1" @click="pages[listTab]--; valuesExpanded = null">{{ labels[listTab] }}上一页</el-button><span>第 {{ pages[listTab] }} 页 / 共 {{ items.length }} 条</span><el-button :disabled="pages[listTab] * 50 >= items.length" @click="pages[listTab]++; valuesExpanded = null">{{ labels[listTab] }}下一页</el-button></div>
            </template>
          </template>
          <template v-else-if="activeTab === 'output'"><el-alert v-if="!output" title="输出不是 JSON 对象，原值已保留，请在完整 JSON 中修正。" type="warning" :closable="false"/><div v-else class="metadata-grid"><el-form-item v-for="field in outputFields" :key="field.key" :label="field.label"><el-select :disabled="readonly || !available" v-if="field.kind === 'select'" :model-value="text(output, field.key)" :aria-label="'输出 ' + field.label" class="full" @update:model-value="update(output, field, $event)"><el-option v-for="choice in field.choices" :key="choice" :label="choice" :value="choice"/></el-select><el-input :disabled="readonly || !available" v-else :model-value="field.kind === 'csv' ? csv(output, field.key) : numericText(output[field.key])" :aria-label="'输出 ' + field.label" @update:model-value="update(output, field, $event)"/></el-form-item></div></template>
          <template v-else-if="activeTab === 'audio_policy'">
            <p class="form-help">说明最终视频的声音来源，并在执行图中连接对应音频。发布验证会检查实际音轨。</p>
            <el-button v-if="!audioPolicy && !readonly" :disabled="!available" @click="initializeAudioPolicy">配置视频声音</el-button>
            <template v-if="audioPolicy"><el-form-item label="声音策略"><el-select :disabled="readonly || !available" :model-value="text(audioPolicy, 'mode')" aria-label="视频声音策略" @update:model-value="audioMode"><el-option label="工作流生成声音" value="generated"/><el-option label="保留源视频声音" value="source_video"/><el-option label="使用参考音频" value="reference_audio"/><el-option label="静音视频" value="silent"/></el-select></el-form-item><el-form-item v-if="['source_video', 'reference_audio'].includes(text(audioPolicy, 'mode'))" label="必填素材槽位"><el-input :disabled="readonly || !available" :model-value="text(audioPolicy, 'source_slot')" aria-label="声音来源槽位" @update:model-value="put(audioPolicy, 'source_slot', $event)"/></el-form-item><el-form-item label="声音功能说明"><el-input :disabled="readonly || !available" :model-value="text(audioPolicy, 'description')" aria-label="声音功能说明" type="textarea" :rows="3" maxlength="1000" @update:model-value="put(audioPolicy, 'description', $event)"/></el-form-item><el-button v-if="!readonly && output" :disabled="!available" @click="unset(output, 'audio_policy')">移除声音声明</el-button></template>
          </template>
          <template v-else-if="activeTab === 'context'">
            <p class="form-help">由成功父任务提供 H3 画面与声音上下文；有效期 8 小时。只允许同一个 API Key 和兼容版本引用。</p>
            <el-button v-if="!context && !readonly" :disabled="!available" @click="initializeContext">配置续拍上下文</el-button>
            <template v-if="context"><el-form-item label="兼容性摘要"><el-input :disabled="readonly || !available" :model-value="text(context, 'compatibility')" aria-label="上下文兼容性摘要" maxlength="64" @update:model-value="put(context, 'compatibility', $event)"/><p class="form-help">填写已核对模型、分辨率和上下文格式的 SHA-256 摘要。</p></el-form-item><el-form-item label="需要父任务"><el-switch :disabled="readonly || !available" :model-value="context.load === true" aria-label="需要父任务上下文" @update:model-value="put(context, 'load', Boolean($event))"/></el-form-item><el-form-item label="保存上下文节点"><el-input :disabled="readonly || !available" :model-value="text(context, 'save_node_id')" aria-label="保存上下文节点" @update:model-value="$event.trim() ? put(context, 'save_node_id', $event) : unset(context, 'save_node_id')"/><p class="form-help">可留空；填写时必须选择图中的正式上下文保存节点并配置输出绑定。</p></el-form-item><el-button v-if="!readonly" :disabled="!available" @click="unset(doc, 'context')">移除上下文声明</el-button></template>
          </template>
          <template v-else-if="activeTab === 'supported_parameters'"><el-form-item label="允许的标准参数（逗号分隔）"><el-input :disabled="readonly || !available" :model-value="csv(doc, 'supported_parameters')" aria-label="允许的标准参数" type="textarea" :rows="4" @update:model-value="setCsv(doc, 'supported_parameters', $event)"/></el-form-item><p class="form-help">此清单不允许客户提交任意节点图或路径；请求仍受公开模型和 API Key 策略约束。</p></template>
          <template v-else-if="activeTab === 'workflow_api'"><el-form-item label="API-format 执行图"><el-input :disabled="readonly || !available" v-model="graphText" aria-label="表单 API 图 JSON" type="textarea" :rows="16" spellcheck="false" class="json-editor" @update:model-value="graphChanged = true; changed()"/></el-form-item><p class="form-help">应用时解析最新执行图；节点选择器以载入时的图为准。完整绑定校验在冻结时执行。</p></template>
        </el-form>
        <div class="manifest-actions"><el-button v-if="!readonly" type="primary" :disabled="!available || stale || !dirty" @click="apply">应用到本地草稿</el-button><el-button @click="discard">放弃表单修改</el-button></div>
      </template>
    </template>
  </section>
</template>
<style scoped>
.manifest-form { margin: 20px 0; padding: 16px; border: 1px solid var(--el-border-color); border-radius: 8px; }
.manifest-heading, .entry-heading, .manifest-actions { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
.manifest-heading h3 { margin: 0; font-size: 16px; }
.manifest-heading .form-help { margin-bottom: 0; }
.manifest-entry { padding: 14px; margin: 12px 0; border: 1px solid var(--el-border-color-lighter); background: var(--el-fill-color-lighter); border-radius: 6px; }
.metadata-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 12px; }
.metadata-grid.binding { grid-template-columns: minmax(0, 1fr) minmax(0, 1.1fr) minmax(0, 1.1fr); }
.entry-heading { margin-bottom: 12px; }
.manifest-actions { justify-content: flex-start; padding-top: 12px; }
.manifest-form :deep(.el-tabs__item) { padding: 0 12px; }
@media (max-width: 600px) { .manifest-form { padding: 12px; } .metadata-grid, .metadata-grid.binding { grid-template-columns: minmax(0, 1fr); } }
</style>
