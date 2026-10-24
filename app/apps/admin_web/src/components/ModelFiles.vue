<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api, readableError, time, unwrap, type Channel } from '../api'
import type { components } from '../generated/api'
import { useSession } from '../session'

type Files = components['schemas']['ModelFilesView']
type Entry = components['schemas']['ModelFileRow']
type Read = components['schemas']['ModelFileReadView']
type Kind = components['schemas']['ModelFileRegistration']['kind']
const props = defineProps<{ channel: Channel; active: boolean }>()
const session = useSession(), files = ref<Files | null>(null), read = ref<Read | null>(null), page = ref(1)
const loading = ref(false), checking = ref(false), saving = ref(false), error = ref(''), operationId = ref(''), waiting = ref(false), paused = ref(false)
const registration = ref({ name: '', kind: 'checkpoint' as Kind })
const kinds: Kind[] = ['checkpoint', 'diffusion_model', 'unet', 'vae', 'lora', 'text_encoder', 'clip', 'embedding', 'upscaler', 'controlnet', 'other']
const statuses = { ready: '目录扫描完成', partial: '目录部分可用', unavailable: '目录不可用', unsupported: '此 Worker 尚未支持模型清单', unknown: '尚未扫描模型目录' }
const registrationStatus = { unregistered: '尚未登记', unverified: '已有登记，哈希待核验', size_mismatch: '登记大小不一致' }
const canCheck = computed(() => session.allows('models:write') && files.value && !files.value.stale && ['ready', 'partial'].includes(files.value.status) && !checking.value && !waiting.value)
const canRegister = computed(() => session.allows('models:write') && read.value?.status === 'succeeded' && !read.value.stale && !!registration.value.name.trim())
let controller: AbortController | undefined, pollController: AbortController | undefined, timer: ReturnType<typeof setTimeout> | undefined
let startToken = '', startFingerprint = '', registrationToken = '', registrationFingerprint = '', pollUntil = 0

function stop() { if (timer) clearTimeout(timer); timer = undefined; pollController?.abort() }
async function refresh() {
  controller?.abort(); const request = new AbortController(); controller = request; loading.value = true; error.value = ''
  try {
    const data = unwrap(await api.GET('/admin/api/v1/channels/{identifier}/inventory', { signal: request.signal, params: { path: { identifier: props.channel.id }, query: { model_page: page.value } } }))
    if (!request.signal.aborted) files.value = data.model_files ?? null
  } catch (e) { if (!request.signal.aborted) error.value = readableError(e) }
  finally { if (controller === request) loading.value = false }
}
function schedule() {
  if (!props.active || document.hidden || !waiting.value) return
  if (Date.now() >= pollUntil) { paused.value = true; return }
  timer = setTimeout(() => { void poll() }, 1500)
}
async function poll() {
  stop(); if (!operationId.value || !props.active || document.hidden) return
  const request = new AbortController(); pollController = request
  try {
    const value = unwrap(await api.GET('/admin/api/v1/model-file-reads/{identifier}', { signal: request.signal, params: { path: { identifier: operationId.value } } }))
    if (request.signal.aborted) return
    if (value.channel_id !== props.channel.id) throw new Error('检查操作不属于当前渠道')
    read.value = value; error.value = ''; waiting.value = ['pending', 'running'].includes(value.status)
    if (value.status === 'succeeded' && value.inspection && !registration.value.name) {
      registration.value = { name: value.inspection.relative_path.split('/').at(-1) ?? '', kind: value.inspection.kind }
    }
  } catch (e) { if (!request.signal.aborted) { error.value = readableError(e); paused.value = true; return } }
  finally { if (pollController === request) pollController = undefined }
  if (!request.signal.aborted) schedule()
}
async function start(entry: Entry) {
  if (!canCheck.value || !files.value?.catalog_digest) return
  checking.value = true; error.value = ''; stop()
  const body = { channel_id: props.channel.id, expected_revision: props.channel.desired_revision, catalog_digest: files.value.catalog_digest, relative_path: entry.relative_path }
  const fingerprint = JSON.stringify(body)
  if (fingerprint !== startFingerprint) { startFingerprint = fingerprint; startToken = crypto.randomUUID() }
  controller?.abort(); const request = new AbortController(); controller = request
  try {
    const result = unwrap(await api.POST('/admin/api/v1/model-file-reads', { signal: request.signal, headers: { 'Idempotency-Key': startToken }, body }))
    if (request.signal.aborted) return
    if (typeof result.operation_id !== 'string') throw new Error('服务端没有返回检查操作编号')
    operationId.value = result.operation_id; read.value = null; waiting.value = true; paused.value = false; pollUntil = Date.now() + 300000
    // A known create result closes this intent. A later click starts a new
    // inspection; unknown/failed creates keep their key for safe retries.
    startFingerprint = ''
    registration.value = { name: '', kind: entry.kind }; registrationToken = ''; registrationFingerprint = ''
    await poll()
  } catch (e) { if (!request.signal.aborted) error.value = readableError(e) }
  finally { checking.value = false }
}
async function resume() {
  if (!/^op_[a-f0-9]{32}$/.test(operationId.value.trim())) { error.value = '请填写完整的检查操作编号'; return }
  operationId.value = operationId.value.trim(); pollUntil = Date.now() + 300000; paused.value = false; await poll()
}
async function register() {
  if (!canRegister.value || !read.value) return
  saving.value = true; error.value = ''
  const body = { name: registration.value.name.trim(), kind: registration.value.kind }
  const fingerprint = JSON.stringify([read.value.id, body])
  if (fingerprint !== registrationFingerprint) { registrationFingerprint = fingerprint; registrationToken = crypto.randomUUID() }
  controller?.abort(); const request = new AbortController(); controller = request
  try {
    unwrap(await api.POST('/admin/api/v1/model-file-reads/{identifier}/registrations', { signal: request.signal, params: { path: { identifier: read.value.id } }, headers: { 'Idempotency-Key': registrationToken }, body }))
    if (!request.signal.aborted) { ElMessage.success('模型文件已登记，可在模型文件页面管理'); await refresh(); await poll() }
  } catch (e) { if (!request.signal.aborted) error.value = readableError(e) }
  finally { saving.value = false }
}
function visibility() { if (document.hidden) { stop(); paused.value = waiting.value } else if (props.active && waiting.value && !paused.value) { void poll() } }
document.addEventListener('visibilitychange', visibility)
watch(() => [props.active, props.channel.id] as const, ([active, id], previous) => {
  stop(); controller?.abort()
  if (id !== previous?.[1]) { files.value = null; read.value = null; page.value = 1; operationId.value = ''; waiting.value = false; startFingerprint = ''; registration.value.name = '' }
  if (active) { void refresh(); if (waiting.value) paused.value = true }
}, { immediate: true })
onUnmounted(() => { stop(); controller?.abort(); document.removeEventListener('visibilitychange', visibility) })
</script>

<template>
  <section class="model-files" aria-label="渠道模型文件">
    <div class="model-toolbar"><h3>渠道模型文件</h3><el-button :loading="loading" :disabled="checking || saving" @click="refresh">刷新模型目录</el-button></div>
    <p class="muted">范围为此 ComfyUI 的 models 目录。外部模型路径需另行核对；扫描读取元信息，选中文件后才计算哈希。</p>
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon/>
    <p v-if="files" class="muted">{{ statuses[files.status] }}{{ files.stale ? ' · 已过期，请重新扫描渠道资源' : '' }} · {{ time(files.observed_at) }} · 发现 {{ files.total }} 个文件<span v-if="files.reason"> · {{ files.reason }}</span></p>
    <el-table :data="files?.data ?? []" empty-text="暂无模型文件，请使用渠道的扫描资源操作" table-layout="auto" data-testid="model-files">
      <el-table-column prop="relative_path" label="模型相对路径" min-width="230"/>
      <el-table-column label="大小 / 类型" min-width="145"><template #default="{ row }">{{ row.bytes.toLocaleString() }} B · {{ row.kind }}</template></el-table-column>
      <el-table-column label="中央登记" min-width="165"><template #default="{ row }">{{ registrationStatus[row.registration_status as keyof typeof registrationStatus] }}（{{ row.registered_count }}）</template></el-table-column>
      <el-table-column v-if="session.allows('models:write')" label="核验" width="115"><template #default="{ row }"><el-button text :disabled="!canCheck || saving" :loading="checking" @click="start(row)">检查哈希</el-button></template></el-table-column>
    </el-table>
    <div class="model-toolbar"><el-button :disabled="page === 1 || loading" @click="page--; refresh()">上一页模型</el-button><span>第 {{ page }} 页 · 每页 50 条</span><el-button :disabled="!files?.has_more || loading" @click="page++; refresh()">下一页模型</el-button></div>
    <div class="model-toolbar"><el-input v-model="operationId" aria-label="模型检查操作编号" placeholder="op_…，可从操作记录恢复查询" :disabled="checking || saving || waiting && !paused"/><el-button :disabled="checking || saving || waiting && !paused" @click="resume">查询检查</el-button></div>
    <p v-if="waiting" role="status">{{ paused ? '检查查询已暂停，可使用操作编号继续查询' : '正在等待设备完成模型检查' }}</p>
    <div v-if="read" class="model-proof">
      <p>检查状态：{{ read.status }} · {{ time(read.observed_at) }}{{ read.stale ? ' · 来源或检查已过期，须重新扫描并检查' : '' }}</p>
      <el-alert v-if="read.error_code" :title="'检查失败：' + read.error_code" type="error" :closable="false"/>
      <template v-if="read.inspection">
        <p>{{ read.inspection.relative_path }} · {{ read.inspection.bytes.toLocaleString() }} B</p>
        <p class="model-hash">SHA-256：{{ read.inspection.sha256 }}</p>
        <p>一致登记 {{ read.matching_registrations }} 条 · 同路径其他登记 {{ read.conflicting_registrations }} 条</p>
        <el-form v-if="session.allows('models:write')" label-position="top" @submit.prevent="register">
          <el-form-item label="登记名称"><el-input v-model="registration.name" maxlength="256"/></el-form-item>
          <el-form-item label="登记类型"><el-select v-model="registration.kind"><el-option v-for="kind in kinds" :key="kind" :value="kind" :label="kind"/></el-select></el-form-item>
          <el-button type="primary" native-type="submit" :loading="saving" :disabled="!canRegister">确认登记模型文件</el-button>
        </el-form>
      </template>
    </div>
  </section>
</template>

<style scoped>
.model-files { margin-top: 24px; }
.model-toolbar { display: flex; align-items: center; gap: 12px; margin: 14px 0; }
.model-toolbar h3 { flex: 1; margin: 0; }
.model-proof { margin-top: 18px; padding: 16px; border: 1px solid var(--el-border-color); border-radius: 8px; }
.model-proof p, .model-hash { overflow-wrap: anywhere; }
</style>
