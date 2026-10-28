<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api, ApiError, readableError, revisionHeaders, time, unwrap } from '../api'
import type { components } from '../generated/api'
import { useSession } from '../session'
type View = components['schemas']['MemoryPolicyView']
type Patch = components['schemas']['MemoryPolicyPatch']
const props = defineProps<{ modelValue: boolean; channelId: string; active: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: boolean] }>()
const session = useSession(), info = ref<View | null>(null), enabled = ref(false), seconds = ref(600)
const loading = ref(false), busy = ref(false), error = ref(''), dirty = ref(false)
const pending = ref<{ id: string; body: Patch; key: string } | null>(null)
const canWrite = computed(() => session.allows('channels:write') && !busy.value && !pending.value && !!info.value)
let alive = true, timer: ReturnType<typeof setTimeout> | null = null, read: AbortController | null = null, write: AbortController | null = null
function active() { return alive && props.modelValue && props.active && !document.hidden && !!session.info && !session.ending }
function stop() { if (timer) clearTimeout(timer); timer = null; read?.abort(); read = null; loading.value = false }
const labels: Record<string, string> = { not_configured: '尚未配置', disabled: '已停用', observing: '开始观测', idle_countdown: '空闲计时中', busy_or_unknown: '忙碌或状态待确认', observation_unknown: '观测失败，暂停计时', releasing: '正在释放', released: '释放流程已确认', release_unknown: '执行结果未知，保留保护', release_failed: '执行失败，保留保护', unsupported_comfy_version: 'ComfyUI 版本暂不支持', channel_changed: '渠道地址已变更' }
async function refresh() {
  stop(); if (!active()) return
  const id = props.channelId, request = new AbortController(); read = request; loading.value = true
  const deadline = setTimeout(() => request.abort(), 10000)
  try {
    const data = unwrap(await api.GET('/admin/api/v1/channels/{identifier}/memory', { signal: request.signal, params: { path: { identifier: id } } }))
    if (active() && read === request && id === props.channelId) {
      info.value = data; error.value = ''
      if (!dirty.value && !pending.value) { enabled.value = data.enabled; seconds.value = data.idle_seconds }
    }
  } catch (e) { if (active() && read === request && !request.signal.aborted) error.value = readableError(e) }
  finally { clearTimeout(deadline); if (read === request) { read = null; loading.value = false; if (active()) timer = setTimeout(refresh, 10000) } }
}
async function save(retry = false) {
  if (!active() || busy.value || !session.allows('channels:write') || !info.value) return
  if (!retry) pending.value = { id: props.channelId, key: crypto.randomUUID(), body: { enabled: enabled.value, idle_seconds: seconds.value, expected_revision: info.value.revision || 1 } }
  const attempt = pending.value; if (!attempt || attempt.id !== props.channelId) return
  busy.value = true; error.value = ''; const request = new AbortController(); write = request
  const deadline = setTimeout(() => request.abort(), 15000)
  try {
    unwrap(await api.PATCH('/admin/api/v1/channels/{identifier}/memory', { signal: request.signal, params: { path: { identifier: attempt.id } }, headers: { ...revisionHeaders(attempt.body.expected_revision), 'Idempotency-Key': attempt.key }, body: attempt.body }))
    if (active() && pending.value === attempt) { pending.value = null; dirty.value = false; ElMessage.success('空闲回收策略已保存'); void refresh() }
  } catch (e) {
    if (active() && pending.value === attempt) {
      error.value = readableError(e)
      if (e instanceof ApiError && e.status >= 400 && e.status < 500 && e.status !== 408) { pending.value = null; dirty.value = false; void refresh() }
    }
  } finally { clearTimeout(deadline); if (write === request) { write = null; busy.value = false } }
}
watch(() => [props.modelValue, props.channelId, props.active, session.info, session.ending], () => {
  stop()
  if (!session.info || session.ending) { info.value = null; pending.value = null; dirty.value = false; write?.abort(); busy.value = false; return }
  if (!props.modelValue) { info.value = null; dirty.value = false; return }
  if (pending.value && pending.value.id !== props.channelId) { pending.value = null; dirty.value = false }
  if (active()) void refresh()
}, { immediate: true })
onBeforeUnmount(() => { alive = false; stop(); write?.abort() })
</script>
<template>
  <el-dialog :model-value="modelValue" title="空闲内存 / 显存回收" width="900px" class="memory-dialog" @update:model-value="emit('update:modelValue', $event)">
    <el-alert type="info" :closable="false" title="通过 ComfyUI API 卸载模型缓存。默认连续空闲 10 分钟后释放，新生成请求会重新计时。"/>
    <el-alert v-if="error" type="error" :closable="false" :title="error" class="space"/>
    <div v-if="pending && !busy" class="space"><el-alert type="warning" :closable="false" title="上次保存的响应未确认。请用同一请求核对结果。"/><el-button class="space" @click="save(true)">核对保存结果</el-button></div>
    <div v-loading="loading" class="space">
      <template v-if="info">
        <div class="facts"><div><span>当前状态</span><strong>{{ labels[info.status] ?? info.status }}</strong></div><div><span>开始空闲</span><strong>{{ time(info.idle_since) }}</strong></div><div><span>最近观测</span><strong>{{ time(info.observed_at) }}</strong></div><div><span>共享保护渠道</span><strong>{{ info.channel_ids.length }} 个</strong></div></div>
        <el-form label-position="top" class="space" @submit.prevent="save()">
          <el-form-item label="启用自动回收"><el-switch v-model="enabled" :disabled="!canWrite" @change="dirty = true"/></el-form-item>
          <el-form-item label="连续空闲秒数（600～86400）"><el-input-number v-model="seconds" :min="600" :max="86400" :step="60" :disabled="!canWrite" @change="dirty = true"/></el-form-item>
          <el-button v-if="session.allows('channels:write')" type="primary" :disabled="!canWrite" :loading="busy" native-type="submit">保存策略</el-button>
          <el-button :disabled="busy" @click="refresh">刷新状态</el-button>
        </el-form>
        <p class="note">释放期间暂缓这些渠道领取生成任务。结果未知时保持保护；停用策略不会解除在途保护。内存观测来自整机和设备，不承诺归零。</p>
        <el-table :data="info.operations" empty-text="尚无回收记录" class="space">
          <el-table-column label="阶段" prop="stage" min-width="160"/>
          <el-table-column label="开始时间" min-width="180"><template #default="{ row }">{{ time(row.created_at) }}</template></el-table-column>
          <el-table-column label="结果 / 原因" min-width="190"><template #default="{ row }">{{ row.error_code ?? row.evidence.memory_result ?? '—' }}</template></el-table-column>
          <el-table-column type="expand"><template #default="{ row }"><pre>{{ JSON.stringify(row.evidence, null, 2) }}</pre></template></el-table-column>
        </el-table>
      </template>
    </div>
  </el-dialog>
</template>
<style scoped>
.space { margin-top: 18px; }.facts { display: grid; grid-template-columns: repeat(2, minmax(0,1fr)); gap: 14px; }.facts div { padding: 14px; border: 1px solid var(--rc-line); border-radius: 12px; }.facts span { display: block; font-size: 12px; margin-bottom: 7px; }.facts strong { overflow-wrap: anywhere; }.note { font-size: 13px; line-height: 1.8; color: var(--rc-text-secondary); } pre { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 360px; overflow: auto; padding: 16px; background: var(--rc-canvas); }@media(max-width:600px) { .facts { grid-template-columns: 1fr; } }
</style>
<style>.memory-dialog { max-width: 96vw; }</style>
