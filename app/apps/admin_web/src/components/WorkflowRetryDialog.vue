<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { api, ApiError, readableError, time, unwrap } from '../api'
import type { components } from '../generated/api'
import { useSession } from '../session'

const props = defineProps<{ modelValue: boolean; operationId: string; active: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: boolean]; accepted: [] }>()
const session = useSession()
type Options = components['schemas']['WorkflowSyncRetryOptions']
type Body = components['schemas']['WorkflowSyncRetry']
type Pending = { token: string; body: Body }
const options = ref<Options | null>(null), selected = ref<string[]>([]), error = ref(''), reading = ref(false), writing = ref(false), accepted = ref(''), uncertain = ref(false)
const pending = new Map<string, Pending>()
let alive = true, read: AbortController | null = null, write: AbortController | null = null, readTimer: ReturnType<typeof setTimeout> | null = null
const eligible = computed(() => options.value?.targets.filter(t => t.eligible) ?? [])
const reasons: Record<string, string> = { operation_incomplete: '等待原操作全部结束', not_failed: '此目标没有失败，无需重试', failure_evidence_required: '缺少确定失败证据，请核对原操作', revalidation_required: '旧完成结果需重新应用配置并验证', already_retried: '已创建后续操作，请查看其结果', channel_unavailable: '渠道已删除或接入节点变化', configuration_changed: '渠道运行配置已变化，请重新同步当前版本', superseded: '已有更新的 Workflow 同步，请查看后续操作', management_busy: '渠道仍有管理指令，请等待或核对', release_unavailable: '原发布版本或绑定已不可用' }
const states: Record<string, string> = { pending: '待执行', running: '执行中', succeeded: '已完成', failed: '失败', partial_failed: '部分失败' }
const taskNames: Record<string, string> = { text_to_image: '文生图', image_to_image: '图生图', references_to_image: '参考图生图', text_to_video: '文生视频', references_to_video: '参考图生视频', text_to_audio: '音频生成' }
function visible(id: string) { return alive && props.modelValue && props.operationId === id && props.active && !document.hidden && !!session.info && !session.ending && session.allows('workflows:publish') }
function stopRead() { read?.abort(); read = null; if (readTimer) clearTimeout(readTimer); readTimer = null; reading.value = false }
function suspend() { stopRead(); if (write) { uncertain.value = true; write.abort(); write = null; writing.value = false }; if (props.modelValue) emit('update:modelValue', false) }
async function load() {
  const id = props.operationId
  if (!visible(id) || writing.value) return
  stopRead(); const own = new AbortController(); read = own; reading.value = true; error.value = ''
  selected.value = []; options.value = null; accepted.value = ''; uncertain.value = pending.has(id)
  readTimer = setTimeout(() => own.abort(new DOMException('重试目标读取超时，请刷新', 'TimeoutError')), 10000)
  try {
    const value = unwrap(await api.GET('/admin/api/v1/workflow-syncs/{identifier}/retry-options', { params: { path: { identifier: id } }, signal: own.signal }))
    if (!visible(id) || read !== own || own.signal.aborted) return
    options.value = value
    const submission = pending.get(id)
    if (submission) {
      const successors = submission.body.targets.map(t => value.targets.find(item => item.command_id === t.command_id)?.retry_operation_id)
      if (successors[0] && successors.every(item => item === successors[0])) { accepted.value = successors[0]; pending.delete(id); uncertain.value = false; emit('accepted') }
    }
  } catch (e) { if (visible(id) && read === own) error.value = own.signal.reason instanceof DOMException && own.signal.reason.name === 'TimeoutError' ? '重试目标读取超时，请刷新' : readableError(e) }
  finally { if (read === own) { if (readTimer) clearTimeout(readTimer); readTimer = null; read = null; reading.value = false } }
}
async function confirm(replay = false) {
  const id = props.operationId, snapshot = options.value
  if (!visible(id) || writing.value || reading.value || accepted.value || !snapshot) return
  let submission = pending.get(id)
  if (replay) { if (!submission) return }
  else {
    if (submission || !selected.value.length) return
    const targets = snapshot.targets.filter(t => selected.value.includes(t.command_id))
    if (targets.length !== selected.value.length || targets.some(t => !t.eligible || t.current_revision === null)) return
    if (pending.size >= 100) { error.value = '请先核对尚未确认的重试结果'; return }
    submission = { token: crypto.randomUUID(), body: { release_id: snapshot.release_id, release_digest: snapshot.release_digest, targets: targets.map(t => ({ command_id: t.command_id, channel_id: t.channel_id, expected_revision: t.current_revision! })) } }
    pending.set(id, submission)
  }
  const own = new AbortController(); write = own; writing.value = true; error.value = ''; uncertain.value = false
  const timer = setTimeout(() => own.abort(), 10000)
  try {
    const result = unwrap(await api.POST('/admin/api/v1/workflow-syncs/{identifier}/retries', { params: { path: { identifier: id } }, headers: { 'Idempotency-Key': submission!.token }, body: submission!.body, signal: own.signal }))
    if (visible(id) && write === own && !own.signal.aborted) { accepted.value = result.operation_id; pending.delete(id); emit('accepted') }
  } catch (e) {
    if (visible(id) && write === own) {
      // An explicit rejection rolls the transaction back. A lost response has
      // no such guarantee: preserve the exact body/key for an explicit replay.
      const rejected = e instanceof ApiError && e.status >= 400 && e.status < 500 && e.status !== 408
      uncertain.value = !rejected
      if (rejected) { pending.delete(id); options.value = null; selected.value = [] }
      error.value = rejected ? readableError(e) + '；请刷新目标后再选择。' : '提交结果尚未确认。可刷新目标查看后续操作，或用原请求核对提交结果。'
    }
  } finally { clearTimeout(timer); if (write === own) { write = null; writing.value = false } }
}
function visibility() { if (document.hidden) suspend() }
document.addEventListener('visibilitychange', visibility)
watch(() => [props.modelValue, props.operationId, props.active] as const, ([open, , active]) => {
  stopRead(); options.value = null; selected.value = []; accepted.value = ''; error.value = ''
  if (write) { write.abort(); write = null; writing.value = false }
  uncertain.value = pending.has(props.operationId)
  if (open && active) void load()
}, { immediate: true })
watch(() => [session.info?.csrf_token, session.ending] as const, ([token, ending], [old]) => { if (!token || ending || token !== old) { pending.clear(); suspend(); options.value = null; selected.value = [] } })
onBeforeUnmount(() => { alive = false; stopRead(); write?.abort(); write = null; pending.clear(); document.removeEventListener('visibilitychange', visibility) })
</script>
<template>
  <el-dialog :model-value="modelValue" title="重试失败渠道" width="min(760px, 94vw)" top="5vh" :show-close="!writing" :close-on-click-modal="!writing" :close-on-press-escape="!writing" @update:model-value="emit('update:modelValue', $event)">
    <p class="form-help">仅重试原冻结版本中已确认失败的渠道。选中渠道将排空并撤销旧运行批准，完成后仍需样本验证。</p>
    <p class="retry-origin"><span class="muted">来源操作</span><router-link :to="{ path: '/operations', query: { operation_id: operationId } }">{{ operationId }}</router-link></p>
    <el-alert v-if="error" :title="error" type="error" :closable="false"/>
    <el-alert v-if="uncertain" title="正在核对的请求保持原选择，暂不能更改目标。关闭或超时不表示已回滚。" type="warning" :closable="false"/>
    <el-alert v-if="accepted" title="重试已提交，仅创建了所选失败渠道的管理指令。" type="success" :closable="false"/>
    <p v-if="accepted"><router-link :to="{ path: '/operations', query: { operation_id: accepted } }" @click="emit('update:modelValue', false)">查看本次重试操作</router-link></p>
    <div class="toolbar"><el-button :loading="reading" :disabled="writing" @click="load">刷新重试目标</el-button><span v-if="options" class="muted">采样于 {{ time(options.snapshot_at) }}</span></div>
    <div v-loading="reading" class="retry-preview" aria-live="polite">
      <template v-if="options && !accepted">
        <div class="retry-release"><strong>{{ taskNames[options.task_type ?? ''] ?? 'Workflow' }} · {{ states[options.source_status] ?? options.source_status }}</strong><code>{{ options.release_id }}</code><span class="muted retry-digest">摘要 {{ options.release_digest }}</span></div>
        <div class="toolbar"><el-button size="small" :disabled="uncertain || writing || !eligible.length" @click="selected = eligible.map(t => t.command_id)">选择可重试渠道</el-button><span>{{ eligible.length }} 个可重试 · 已选 {{ selected.length }} 个</span></div>
        <el-checkbox-group v-model="selected" :disabled="writing || uncertain" class="retry-targets" aria-label="选择失败渠道">
          <article v-for="target in options.targets" :key="target.command_id" class="retry-target" :data-command="target.command_id">
            <el-checkbox :value="target.command_id" :disabled="!target.eligible || writing || uncertain"><strong>{{ target.channel_name ?? '渠道不可用' }}</strong></el-checkbox>
            <el-tag :type="target.status === 'succeeded' ? 'success' : target.eligible ? 'warning' : 'info'" size="small">{{ states[target.status] ?? target.status }}</el-tag>
            <code>{{ target.channel_id }}</code>
            <p>{{ target.eligible ? '可重试 · 当前渠道版本 ' + target.current_revision : reasons[target.reason ?? ''] ?? '请核对目标状态' }}</p>
            <router-link v-if="target.retry_operation_id" :to="{ path: '/operations', query: { operation_id: target.retry_operation_id } }" @click="emit('update:modelValue', false)">查看已有重试操作</router-link>
          </article>
        </el-checkbox-group>
        <el-empty v-if="!options.targets.length" description="没有可查看的目标"/>
      </template>
    </div>
    <template #footer><el-button :disabled="writing" @click="emit('update:modelValue', false)">关闭</el-button><el-button v-if="uncertain" type="primary" :loading="writing" :disabled="reading || !options" @click="confirm(true)">核对提交结果</el-button><el-button v-else-if="!accepted" type="primary" :loading="writing" :disabled="reading || !options || !selected.length || !active" @click="confirm()">确认重试 {{ selected.length }} 个渠道</el-button></template>
  </el-dialog>
</template>
<style scoped>
.retry-origin { display: grid; gap: 6px; }
.retry-origin a, .retry-release code, .retry-digest, .retry-target code { overflow-wrap: anywhere; }
.retry-release { display: grid; gap: 8px; padding: 16px; margin-bottom: 16px; background: var(--el-fill-color-light); border-left: 3px solid var(--el-color-primary); }
.retry-digest, .retry-target code { font-size: 12px; }
.retry-targets { display: grid; gap: 12px; }
.retry-target { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 6px 12px; padding: 14px; border: 1px solid var(--el-border-color-light); border-radius: 6px; font-size: 14px; line-height: 1.6; }
.retry-target code, .retry-target p, .retry-target a { grid-column: 1 / -1; }
.retry-target p { margin: 0; line-height: 1.6; font-size: 13px; color: var(--el-text-color-secondary); }
.retry-target :deep(.el-checkbox) { height: auto; white-space: normal; min-width: 0; }
.retry-target :deep(.el-checkbox__label) { white-space: normal; overflow-wrap: anywhere; }
@media (max-width: 600px) { .retry-release { padding: 12px; } .retry-target { padding: 10px; } .retry-origin a { font-size: 12px; } }
</style>
