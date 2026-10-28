<script setup lang="ts">
import { toRaw } from 'vue'
import type { ScopeLimits, TenantRecord } from '../api'

const props = defineProps<{ modelValue: ScopeLimits; parent?: ScopeLimits; usage?: TenantRecord['usage']; readonly?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: ScopeLimits] }>()
type Name = keyof ScopeLimits
const rows: { key: Name; label: string; maximum: number }[] = [
  { key: 'rpm', label: '每分钟请求数', maximum: 60000 }, { key: 'burst', label: '突发请求容量', maximum: 10000 },
  { key: 'daily_jobs', label: '每日接收任务', maximum: Number.MAX_SAFE_INTEGER }, { key: 'unfinished_jobs', label: '未结束任务', maximum: Number.MAX_SAFE_INTEGER },
  { key: 'generating_jobs', label: '生成并发数', maximum: Number.MAX_SAFE_INTEGER }, { key: 'daily_image_count', label: '每日图片额度', maximum: Number.MAX_SAFE_INTEGER },
  { key: 'daily_video_milliseconds', label: '每日视频额度（毫秒）', maximum: Number.MAX_SAFE_INTEGER },
  { key: 'daily_audio_milliseconds', label: '每日音频额度（毫秒）', maximum: Number.MAX_SAFE_INTEGER },
  { key: 'daily_mesh_count', label: '每日 3D 模型额度', maximum: Number.MAX_SAFE_INTEGER },
]
function currentLimit(key: Name) { return props.modelValue[key] ?? { mode: 'bounded' as const, value: 0 } }
function update(key: Name, mode: 'bounded' | 'unlimited', value?: number) {
  const limits = structuredClone(toRaw(props.modelValue))
  limits[key] = { mode, value: mode === 'unlimited' ? null : value ?? limits[key]?.value ?? 0 }
  emit('update:modelValue', limits)
}
function parentValue(key: Name) { const value = props.parent?.[key] ?? (['daily_audio_milliseconds', 'daily_mesh_count'].includes(key) ? { mode: 'bounded', value: 0 } : undefined); return value?.mode === 'bounded' ? value.value?.toLocaleString() : '无限制' }
function used(key: Name) { return props.usage && key in props.usage ? Number(props.usage[key as keyof typeof props.usage]).toLocaleString() : '—' }
</script>

<template>
  <div class="limit-editor" aria-label="资源配额">
    <div v-for="row in rows" :key="row.key" class="limit-editor-row">
      <div class="limit-editor-label"><strong>{{ row.label }}</strong><small v-if="parent">租户上限 {{ parentValue(row.key) }}</small><small v-if="usage">已用与预留 {{ used(row.key) }}</small></div>
      <div class="limit-editor-controls"><el-select :model-value="currentLimit(row.key).mode" :aria-label="row.label + '限制方式'" :disabled="readonly" @update:model-value="(value: 'bounded' | 'unlimited') => update(row.key, value)"><el-option label="设置上限" value="bounded"/><el-option label="无限制" value="unlimited"/></el-select><el-input-number v-if="currentLimit(row.key).mode === 'bounded'" :model-value="currentLimit(row.key).value ?? 0" :min="0" :max="row.maximum" :precision="0" controls-position="right" :aria-label="row.label + '上限'" :disabled="readonly" @update:model-value="(value: number | undefined) => update(row.key, 'bounded', value)"/><span v-else class="muted limit-unlimited">不设本层上限</span></div>
    </div>
  </div>
</template>

<style scoped>
.limit-editor { border-top: 1px solid var(--rc-line); }
.limit-editor-row { display: grid; grid-template-columns: minmax(150px, 1fr) minmax(240px, 1.1fr); align-items: center; gap: 18px; padding: 15px 0; border-bottom: 1px solid var(--rc-line); }
.limit-editor-label strong { display: block; font-size: 13px; font-weight: 500; }
.limit-editor-label small { display: block; color: var(--rc-text-muted); line-height: 1.8; font-size: 11px; }
.limit-editor-controls { display: grid; grid-template-columns: 110px minmax(120px, 1fr); gap: 8px; }
.limit-editor-controls :deep(.el-input-number) { width: 100%; }
.limit-unlimited { align-self: center; }
@media (max-width: 600px) { .limit-editor-row { grid-template-columns: 1fr; gap: 8px; } .limit-editor-controls { grid-template-columns: 105px minmax(0, 1fr); } }
</style>
