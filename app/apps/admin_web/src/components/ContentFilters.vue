<script setup lang="ts">
import { onMounted, onUnmounted, reactive, ref } from 'vue'
import { api, readableError, unwrap } from '../api'
import { contentStates, type ContentFilters } from '../content'
import type { components } from '../generated/api'

const emit = defineEmits<{ apply: [filters: ContentFilters] }>()
const draft = reactive<ContentFilters>({}), dates = ref<[Date, Date] | null>(null), error = ref('')
type Kind = 'tenant' | 'project' | 'key' | 'model' | 'workflow' | 'release'
type Option = components['schemas']['QueueOption']
const fields: { kind: Kind; field: 'tenant_id' | 'project_id' | 'key_id' | 'model' | 'workflow_id' | 'release_id'; label: string }[] = [
  { kind: 'tenant', field: 'tenant_id', label: '租户' }, { kind: 'project', field: 'project_id', label: '项目' },
  { kind: 'key', field: 'key_id', label: '创建密钥' }, { kind: 'model', field: 'model', label: '模型' },
  { kind: 'workflow', field: 'workflow_id', label: 'Workflow' }, { kind: 'release', field: 'release_id', label: '发布版本' },
]
const choices = reactive<Record<Kind, Option[]>>({ tenant: [], project: [], key: [], model: [], workflow: [], release: [] })
const loading = reactive<Record<Kind, boolean>>({ tenant: false, project: false, key: false, model: false, workflow: false, release: false })
const more = reactive<Record<Kind, boolean>>({ tenant: false, project: false, key: false, model: false, workflow: false, release: false })
const pages = new Map<Kind, number>(), searches = new Map<Kind, string>(), requests = new Map<Kind, AbortController>()
let alive = true
function stop() { for (const request of requests.values()) request.abort(); requests.clear(); for (const field of fields) loading[field.kind] = false }
async function load(kind: Kind, q = '', append = false) {
  if (!alive || document.hidden) return
  requests.get(kind)?.abort()
  const current = new AbortController(); requests.set(kind, current); loading[kind] = true
  const page = append ? (pages.get(kind) ?? 1) + 1 : 1
  if (!append) searches.set(kind, q)
  try {
    const result = unwrap(await api.GET('/admin/api/v1/content/filter-options', { signal: current.signal, params: { query: { kind, q: append ? searches.get(kind) : q, page, tenant_id: draft.tenant_id || undefined, project_id: draft.project_id || undefined, workflow_id: ['release', 'model'].includes(kind) ? draft.workflow_id || undefined : undefined } } }))
    if (alive && requests.get(kind) === current && !current.signal.aborted) {
      choices[kind] = [...(append ? choices[kind].slice(-50) : []), ...result.data]; more[kind] = result.has_more && page < 10000; pages.set(kind, page); error.value = ''
    }
  } catch (e) { if (alive && !current.signal.aborted && requests.get(kind) === current) error.value = readableError(e) }
  finally { if (requests.get(kind) === current) { requests.delete(kind); loading[kind] = false } }
}
function parentChanged(kind: Kind) {
  if (!['tenant', 'project', 'workflow'].includes(kind)) return
  stop()
  const cleared: Kind[] = kind === 'tenant' ? ['project', 'key', 'model', 'workflow', 'release'] : kind === 'project' ? ['key', 'model', 'workflow', 'release'] : ['release']
  for (const field of fields) if (cleared.includes(field.kind)) { delete draft[field.field]; choices[field.kind] = []; pages.delete(field.kind); more[field.kind] = false }
}
function apply() {
  const next: ContentFilters = {}
  for (const field of fields) if (draft[field.field]) next[field.field] = draft[field.field]
  if (draft.job_id?.trim()) {
    next.job_id = draft.job_id.trim()
    if (!/^job_[a-f0-9]{32}$/.test(next.job_id)) { error.value = '请输入完整的任务 ID'; return }
  }
  if (draft.tag?.trim()) next.tag = draft.tag.trim().normalize('NFC')
  if (next.tag && (Array.from(next.tag).length > 64 || /[\u0000-\u001f\u007f-\u009f]/.test(next.tag))) { error.value = '标签最多 64 字符，不能包含控制字符'; return }
  if (draft.media_kind) next.media_kind = draft.media_kind
  if (draft.state) next.state = draft.state
  if (dates.value) {
    const [from, before] = dates.value.map(date => Math.floor(date.getTime() / 1000))
    if (from == null || before == null || !Number.isFinite(from) || !Number.isFinite(before) || from >= before || from < 0 || before > 4102444800) { error.value = '请选择有效的创建时间范围，结束时间不包含在内'; return }
    next.created_from = from; next.created_before = before
  }
  error.value = ''; emit('apply', next)
}
function reset() { for (const key of Object.keys(draft) as (keyof ContentFilters)[]) delete draft[key]; dates.value = null; stop(); error.value = ''; emit('apply', {}) }
function visibility() { if (document.hidden) stop() }
onMounted(() => document.addEventListener('visibilitychange', visibility))
onUnmounted(() => { alive = false; stop(); document.removeEventListener('visibilitychange', visibility) })
</script>

<template>
  <section class="panel li-card content-filters" aria-label="内容筛选">
    <el-form label-position="top" @submit.prevent="apply">
      <div class="content-filter-grid">
        <el-form-item v-for="field in fields" :key="field.kind" :label="field.label">
          <el-select v-model="draft[field.field]" clearable filterable remote :remote-method="(text: string) => load(field.kind, text)" :loading="loading[field.kind]" :placeholder="`全部${field.label}`" @visible-change="(open: boolean) => { if (open) load(field.kind) }" @change="parentChanged(field.kind)">
            <el-option v-for="option in choices[field.kind]" :key="option.value" :label="option.label" :value="option.value"/>
            <template #footer><el-button v-if="more[field.kind]" text :loading="loading[field.kind]" @click.stop="load(field.kind, '', true)">加载更多选项</el-button><span v-else class="muted">可输入名称搜索</span></template>
          </el-select>
        </el-form-item>
        <el-form-item label="文件类型"><el-select v-model="draft.media_kind" clearable placeholder="全部类型"><el-option label="图片" value="image"/><el-option label="视频" value="video"/><el-option label="音频" value="audio"/></el-select></el-form-item>
        <el-form-item label="内容状态"><el-select v-model="draft.state" clearable placeholder="全部状态"><el-option v-for="(label, state) in contentStates" :key="state" :label="label" :value="state"/></el-select></el-form-item>
        <el-form-item label="关联任务"><el-input v-model="draft.job_id" clearable placeholder="job_…" :maxlength="36"/></el-form-item>
        <el-form-item label="标签"><el-input v-model="draft.tag" clearable placeholder="单个完整标签"/></el-form-item>
        <el-form-item label="创建时间（结束时间不含）" class="content-date-filter"><el-date-picker v-model="dates" type="datetimerange" start-placeholder="开始时间" end-placeholder="结束时间" range-separator="至" format="YYYY-MM-DD HH:mm:ss"/></el-form-item>
      </div>
      <div class="content-filter-actions"><span class="muted">条件取交集 · 时间按本地时区输入</span><el-button @click="reset">重置筛选</el-button><el-button native-type="submit" type="primary">应用筛选</el-button></div>
    </el-form>
    <el-alert v-if="error" :title="error" type="error" :closable="false"/>
  </section>
</template>

<style scoped>
.content-filters { padding: 18px 20px; margin-bottom: 20px; }
.content-filter-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 0 14px; }
.content-filter-grid :deep(.el-select), .content-filter-grid :deep(.el-date-editor) { width: 100%; }
.content-date-filter { grid-column: span 2; min-width: 0; }
.content-filter-actions { display: flex; justify-content: flex-end; align-items: center; gap: 10px; flex-wrap: wrap; }
.content-filter-actions > span { margin-right: auto; font-size: 11px; }
@media(max-width: 1050px) { .content-filter-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media(max-width: 550px) { .content-filter-grid { grid-template-columns: minmax(0, 1fr); } .content-date-filter { grid-column: span 1; } }
</style>
