<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api, readableError, revisionHeaders, time, unwrap, type Channel } from '../api'
import type { components } from '../generated/api'

type Check = components['schemas']['ChannelDeletionCheck']
type Kind = components['schemas']['ChannelDeletionBlocker']['kind']
const props = defineProps<{ modelValue: boolean; target: Channel | null; active: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: boolean]; deleted: [] }>()
const check = ref<Check | null>(null), loading = ref(false), deleting = ref(false), readError = ref(''), deleteError = ref('')
const labels: Record<Kind, string> = { active_jobs: '未结束任务', open_attempts: '尚未关闭的执行尝试', pending_commands: '待完成或待核对的管理指令', published_models: '仍在发布的模型', gpu_occupancy: '物理 GPU 仍有任务占用', gpu_management_hold: '物理 GPU 仍有管理保留' }
let alive = true
type Request = { controller: AbortController; timer: ReturnType<typeof setTimeout> }
let read: Request | null = null, write: Request | null = null
function visible(identifier: string) { return alive && props.active && !document.hidden && props.modelValue && props.target?.id === identifier }
function stopRead() { const old = read; read = null; if (old) { clearTimeout(old.timer); old.controller.abort() }; loading.value = false }
function budget(message: string): Request { const controller = new AbortController(); return { controller, timer: setTimeout(() => controller.abort(new DOMException(message, 'TimeoutError')), 10000) } }
const canConfirm = computed(() => !!check.value?.can_delete && !loading.value && !deleting.value && !readError.value && !deleteError.value)

async function refresh(manual = true) {
  stopRead(); check.value = null; readError.value = ''
  if (manual) deleteError.value = ''
  const identifier = props.target?.id
  if (!identifier || !visible(identifier)) return
  const own = budget('删除检查超时，请重试'); read = own; loading.value = true
  try {
    const value = unwrap(await api.GET('/admin/api/v1/channels/{identifier}/deletion-check', { params: { path: { identifier } }, signal: own.controller.signal }))
    if (read === own && visible(identifier) && !own.controller.signal.aborted) check.value = value
  } catch (e) {
    if (read === own && visible(identifier)) readError.value = own.controller.signal.reason instanceof DOMException && own.controller.signal.reason.name === 'TimeoutError' ? '删除检查超时，请重试' : readableError(e)
  } finally { clearTimeout(own.timer); if (read === own) { read = null; loading.value = false } }
}
function resourceLink(kind: Kind, identifier: string) {
  if (kind === 'published_models') return { path: '/models' }
  if (kind === 'pending_commands') return { path: '/operations' }
  if (kind === 'gpu_management_hold') return { path: '/worker-nodes', query: { node: check.value?.node_id } }
  return { path: '/jobs', query: { job: identifier } }
}
async function confirm() {
  const snapshot = check.value
  if (!snapshot || !canConfirm.value || !visible(snapshot.channel_id)) return
  const identifier = snapshot.channel_id, own = budget('删除请求未确认，请刷新检查确认当前状态')
  write = own; deleting.value = true; deleteError.value = ''
  try {
    unwrap(await api.DELETE('/admin/api/v1/channels/{identifier}', { params: { path: { identifier }, query: { revision: snapshot.revision } }, headers: revisionHeaders(snapshot.revision), signal: own.controller.signal }))
    if (alive) {
      emit('deleted')
      if (visible(identifier)) { emit('update:modelValue', false); ElMessage.success('渠道登记已删除，历史和文件已保留') }
    }
  } catch (e) {
    if (visible(identifier)) {
      deleteError.value = own.controller.signal.aborted ? '删除请求未确认，请刷新检查确认当前状态；超时不代表操作已回滚。' : readableError(e)
      void refresh(false)
    }
  } finally { clearTimeout(own.timer); if (write === own) { write = null; deleting.value = false } }
}
watch(() => [props.modelValue, props.target?.id, props.active] as const, ([open, identifier, active]) => {
  stopRead(); check.value = null; readError.value = ''; deleteError.value = ''
  if (open && active && identifier && !document.hidden) void refresh()
  else if (open && !active) emit('update:modelValue', false)
}, { immediate: true })
onBeforeUnmount(() => { alive = false; stopRead(); if (write) { clearTimeout(write.timer); write.controller.abort(); write = null } })
</script>

<template>
  <el-dialog :model-value="modelValue" title="删除渠道登记" width="min(620px, 94vw)" top="5vh" :show-close="!deleting" :close-on-click-modal="!deleting" :close-on-press-escape="!deleting" @update:model-value="emit('update:modelValue', $event)">
    <div class="deletion-content">
    <strong>{{ target?.name }}</strong><p class="id-line mono">{{ target?.id }}</p>
    <p class="form-help">仅删除渠道登记，保留任务历史与文件。确认时会再次检查当前引用和 GPU 状态。</p>
    <el-alert v-if="readError" :title="readError" type="error" :closable="false"/>
    <el-alert v-if="deleteError" :title="deleteError" type="error" :closable="false"/>
    <p v-if="loading" role="status" class="muted">正在检查删除约束…</p>
    <template v-if="check">
      <p class="muted">检查时间 {{ time(check.snapshot_at) }} · 登记修订 {{ check.revision }}</p>
      <el-alert v-if="check.deleted" title="渠道登记已删除，无需再次删除。" type="info" :closable="false"/>
      <el-alert v-else-if="check.can_delete" title="当前检查未发现阻断，可以确认删除。" type="success" :closable="false"/>
      <el-alert v-else title="当前存在以下引用或占用，请先处理后刷新检查。" type="warning" :closable="false"/>
      <section v-for="blocker in check.blockers" :key="blocker.kind" class="deletion-blocker">
        <h3>{{ labels[blocker.kind] }}</h3>
        <ul><li v-for="identifier in blocker.resource_ids" :key="identifier"><router-link :to="resourceLink(blocker.kind, identifier)" class="mono">{{ identifier }}</router-link></li></ul>
        <p v-if="blocker.has_more" class="form-help">仅列出前 10 项，请到对应管理页面继续查看。</p>
      </section>
    </template>
    </div>
    <template #footer><div class="deletion-actions"><el-button :disabled="deleting" @click="emit('update:modelValue', false)">取消</el-button><el-button :loading="loading" :disabled="deleting" @click="refresh()">刷新检查</el-button><el-button type="danger" :disabled="!canConfirm" :loading="deleting" @click="confirm">确认删除</el-button></div></template>
  </el-dialog>
</template>

<style scoped>
.deletion-content { max-height: 60vh; overflow-y: auto; padding-right: 4px; }
.deletion-blocker { margin-top: 16px; padding: 12px 16px; border: 1px solid var(--el-border-color); border-radius: 6px; }
.deletion-blocker h3 { margin: 0 0 8px; font-size: 14px; }
.deletion-blocker ul { margin: 0; padding-left: 18px; }
.deletion-blocker li { margin: 6px 0; overflow-wrap: anywhere; }
.deletion-actions { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 8px; }
.deletion-actions :deep(.el-button + .el-button) { margin-left: 0; }
@media (max-width: 640px) { .deletion-blocker .mono { font-size: 12px; } }
</style>
