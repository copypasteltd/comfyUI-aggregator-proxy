<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { api, readableError, unwrap } from '../api'
import type { components } from '../generated/api'
import { useSession } from '../session'

const props = defineProps<{ modelValue: boolean; target: components['schemas']['ModelView'] | null; active: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: boolean] }>()
const session = useSession(), snapshot = ref<components['schemas']['DevicePoolView'] | null>(null), loading = ref(false), error = ref('')
let alive = true, read: AbortController | null = null, timer: ReturnType<typeof setTimeout> | null = null
function stop() { read?.abort(); read = null; if (timer) clearTimeout(timer); timer = null; loading.value = false }
function active(id?: string) { return alive && props.modelValue && props.active && !document.hidden && !!session.info && !session.ending && session.allows('models:read') && (!id || props.target?.id === id) }
async function refresh() {
  stop()
  const id = props.target?.id
  if (!id || !active(id)) return
  const own = new AbortController(), token = session.info?.csrf_token
  read = own; loading.value = true; error.value = ''
  timer = setTimeout(() => own.abort(new DOMException('读取超时', 'TimeoutError')), 10000)
  try {
    const result = unwrap(await api.GET('/admin/api/v1/public-models/{identifier}/device-pool', { signal: own.signal, params: { path: { identifier: id } } }))
    if (active(id) && read === own && !own.signal.aborted && session.info?.csrf_token === token) snapshot.value = result
  } catch (e) { if (active(id) && read === own && session.info?.csrf_token === token) error.value = own.signal.aborted ? '读取超时，请重试。' : readableError(e) }
  finally { if (read === own) stop() }
}
watch(() => [props.modelValue, props.target?.id, props.active, session.info?.csrf_token, session.ending] as const, (value, previous) => {
  stop()
  if (!previous || value[1] !== previous[1] || value[3] !== previous[3] || !value[0] || !value[3] || value[4]) snapshot.value = null
  if (!value[3] || value[4]) { emit('update:modelValue', false); return }
  if (active()) void refresh()
}, { immediate: true })
onBeforeUnmount(() => { alive = false; stop() })
</script>

<template>
  <el-dialog :model-value="modelValue" title="设备分配统计" width="min(820px, 96vw)" @update:model-value="emit('update:modelValue', $event)">
    <p>{{ target?.alias }} · 当前配置版本 {{ snapshot?.model_revision ?? '—' }}</p>
    <el-alert v-if="error" :title="error" type="error" :closable="false"/>
    <div v-loading="loading" class="device-pool-body">
      <template v-if="snapshot?.policy === 'balanced_ready'">
        <p class="form-help">均衡池累计分配 {{ snapshot.sequence }} 次；仅包含当前池版本。分配成功表示 Worker 已领取，不等于生成成功。</p>
        <p class="id-line">池版本 {{ snapshot.pool_id }}</p>
        <el-table :data="snapshot.members" table-layout="auto"><el-table-column prop="gpu_id" label="物理 GPU" min-width="290"/><el-table-column prop="weight" label="权重" min-width="70"/><el-table-column prop="assigned_count" label="已分配" min-width="80"/><el-table-column prop="last_sequence" label="最后序号" min-width="90"/></el-table>
      </template>
      <p v-else-if="snapshot" class="form-help">当前采用原领取策略。此策略不累计设备均衡统计；历史任务和既有运行保护继续生效。</p>
      <p v-else-if="!error" role="status">正在读取当前设备池…</p>
    </div>
    <template #footer><el-button :disabled="loading || !active()" @click="refresh">刷新统计</el-button><el-button @click="emit('update:modelValue', false)">关闭</el-button></template>
  </el-dialog>
</template>

<style scoped>
.device-pool-body { min-height: 100px; }
.id-line { overflow-wrap: anywhere; }
</style>
