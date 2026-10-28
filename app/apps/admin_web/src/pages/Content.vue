<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import { api, ApiError, readableError, revisionHeaders, time, unwrap } from '../api'
import ContentFilters from '../components/ContentFilters.vue'
import { contentStatus, type ContentFilters as Filters } from '../content'
import type { components } from '../generated/api'
import { useSession } from '../session'

type Content = components['schemas']['ContentView']
type Page = components['schemas']['AdminContentPage']
const session = useSession(), kind = ref<'input' | 'output'>('output'), data = ref<Page | null>(null), loading = ref(false), error = ref(''), page = ref(1), filters = ref<Filters>({})
const selected = ref<Content | null>(null), detailsOpen = ref(false), detailBusy = ref(false), detailError = ref(''), tagDraft = ref<string[]>([]), saving = ref(false)
const previewActive = ref(false), previewError = ref(''), video = ref<HTMLVideoElement | null>(null), audio = ref<HTMLAudioElement | null>(null)
const rows = computed(() => data.value?.data ?? []), hasMore = computed(() => data.value?.has_more ?? false)
const canDownload = computed(() => session.allows('content:download') && (kind.value === 'output' || session.allows('content:sensitive')))
const tagsDirty = computed(() => JSON.stringify(tagDraft.value) !== JSON.stringify(selected.value?.tags ?? []))
const previewUrl = computed(() => previewActive.value && selected.value ? `/admin/api/v1/content/${encodeURIComponent(selected.value.id)}/preview` : undefined)
let alive = true, listRequest: AbortController | null = null, detailRequest: AbortController | null = null, tagRequest: AbortController | null = null
let tagAttempt: { body: string; token: string } | null = null, visibilityEpoch = 0
function bytes(size: number) { return size >= 1048576 ? `${(size / 1048576).toFixed(1)} MB` : `${(size / 1024).toFixed(1)} KB` }
function clearPreview() {
  for (const media of [video.value, audio.value]) { if (media) { media.pause(); media.removeAttribute('src'); media.load() } }
  previewActive.value = false; previewError.value = ''
}
function stopReads() { listRequest?.abort(); listRequest = null; loading.value = false; detailRequest?.abort(); detailRequest = null; detailBusy.value = false }
function stop() { stopReads(); tagRequest?.abort(); tagRequest = null; saving.value = false; clearPreview() }
async function refresh() {
  if (!alive || document.hidden || !session.info) return
  listRequest?.abort()
  const request = new AbortController(); listRequest = request; loading.value = true
  try {
    const result = unwrap(await api.GET('/admin/api/v1/content', { signal: request.signal, params: { query: { ...filters.value, kind: kind.value, page: page.value } } }))
    if (alive && listRequest === request && !request.signal.aborted) { data.value = result; error.value = '' }
  } catch (e) { if (alive && listRequest === request && !request.signal.aborted) error.value = readableError(e) }
  finally { if (listRequest === request) { listRequest = null; loading.value = false } }
}
async function inspect(row: Content, preserveDraft = false) {
  if (!alive || document.hidden || !session.info) return
  clearPreview(); detailRequest?.abort(); tagRequest?.abort(); tagRequest = null; saving.value = false
  const request = new AbortController(); detailRequest = request
  if (!preserveDraft) { selected.value = null; tagDraft.value = []; tagAttempt = null }
  detailsOpen.value = true; detailBusy.value = true; detailError.value = ''
  try {
    const result = unwrap(await api.GET('/admin/api/v1/content/{identifier}', { signal: request.signal, params: { path: { identifier: row.id } } }))
    if (alive && detailRequest === request && !request.signal.aborted) { selected.value = result; if (!preserveDraft) tagDraft.value = [...(result.tags ?? [])] }
  } catch (e) { if (alive && detailRequest === request && !request.signal.aborted) detailError.value = readableError(e) }
  finally { if (detailRequest === request) { detailRequest = null; detailBusy.value = false } }
}
async function saveTags() {
  const row = selected.value
  if (!row || saving.value || document.hidden || !session.allows('content:manage')) return
  const tags = tagDraft.value.map(tag => tag.trim().normalize('NFC'))
  if (tags.length > 16 || new Set(tags).size !== tags.length || tags.some(tag => !tag || Array.from(tag).length > 64 || /[\u0000-\u001f\u007f-\u009f]/.test(tag))) { detailError.value = '最多 16 个不重复标签，每个 1～64 字符，不能包含控制字符'; return }
  tagDraft.value = tags
  const body = { expected_revision: row.metadata_revision ?? 1, tags }, fingerprint = JSON.stringify(body)
  if (tagAttempt?.body !== fingerprint) tagAttempt = { body: fingerprint, token: crypto.randomUUID() }
  const request = new AbortController(); tagRequest = request; saving.value = true; detailError.value = ''
  try {
    const result = unwrap(await api.PATCH('/admin/api/v1/content/{identifier}', { signal: request.signal, params: { path: { identifier: row.id } }, body, headers: { ...revisionHeaders(body.expected_revision), 'Idempotency-Key': tagAttempt.token } }))
    if (alive && tagRequest === request && !request.signal.aborted) { selected.value = result; tagDraft.value = [...(result.tags ?? [])]; tagAttempt = null; ElMessage.success('标签已保存'); void refresh() }
  } catch (e) {
    if (alive && tagRequest === request && !request.signal.aborted) detailError.value = e instanceof ApiError && e.status === 409 ? '内容版本或幂等请求已变化。请重新加载详情，核对标签后保存。当前草稿已保留。' : readableError(e)
  } finally { if (tagRequest === request) { tagRequest = null; saving.value = false } }
}
async function remove(row: Content) {
  try { await ElMessageBox.confirm('删除后将立即停止读取，并由后台回收文件。任务记录和用量记录会保留。', '删除内容', { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' }) }
  catch { return }
  if (!alive || !session.info || document.hidden) return
  try {
    unwrap(await api.DELETE('/admin/api/v1/content/{identifier}', { params: { path: { identifier: row.id } } }))
    if (!alive) return
    ElMessage.success('删除申请已接收'); if (selected.value?.id === row.id) detailsOpen.value = false; await refresh()
  } catch (e) { if (alive) error.value = readableError(e) }
}
function download(row: Content) {
  const link = document.createElement('a')
  link.href = `/admin/api/v1/content/${encodeURIComponent(row.id)}/download`
  link.download = row.id; link.click(); link.remove()
}
function preview() { if (selected.value?.preview_supported && canDownload.value && !document.hidden) { clearPreview(); previewActive.value = true } }
function previewFailed() { if (previewActive.value) { clearPreview(); previewError.value = '文件读取失败，可能已到期、被删除或无读取权限。请刷新详情后重试。' } }
function apply(next: Filters) { stopReads(); filters.value = { ...next }; page.value = 1; data.value = null; detailsOpen.value = false; error.value = ''; void refresh() }
function goPage(delta: number) { stopReads(); page.value = Math.max(1, Math.min(10000, page.value + delta)); data.value = null; error.value = ''; void refresh() }
async function visibility() {
  const epoch = ++visibilityEpoch; stop()
  if (!document.hidden && alive) {
    try { await session.refresh(); if (alive && epoch === visibilityEpoch && !document.hidden) void refresh() }
    catch (e) { if (alive && epoch === visibilityEpoch) error.value = readableError(e) }
  }
}
watch(detailsOpen, open => { if (!open) { clearPreview(); detailRequest?.abort(); detailRequest = null; detailBusy.value = false; tagRequest?.abort(); tagRequest = null; saving.value = false } })
watch(kind, () => apply(filters.value))
watch(() => session.info, info => { if (!info) { ++visibilityEpoch; stop(); detailsOpen.value = false; selected.value = null; data.value = null; tagDraft.value = []; tagAttempt = null } })
onMounted(() => { void refresh(); document.addEventListener('visibilitychange', visibility) })
onBeforeUnmount(() => { alive = false; ++visibilityEpoch; stop(); document.removeEventListener('visibilitychange', visibility) })
</script>

<template>
  <div class="page-heading"><div><p class="eyebrow li-lab">CONTENT / ASSETS</p><h1>内容管理</h1><p class="muted">查找输入素材与生成产物，整理标签并按权限预览。</p></div><el-button :icon="Refresh" :loading="loading" @click="refresh">刷新</el-button></div>
  <el-alert v-if="error" :title="error" type="error" :closable="false"/>
  <ContentFilters @apply="apply"/>
  <section class="panel li-card">
    <el-tabs v-model="kind"><el-tab-pane label="生成产物" name="output"/><el-tab-pane label="输入素材" name="input"/></el-tabs>
    <p class="muted content-snapshot">第 {{ page }} 页 · 每页最多 50 条 · 查询时间 {{ time(data?.snapshot_at) }}</p>
    <el-table :data="rows" v-loading="loading" :empty-text="kind === 'input' ? '暂无输入素材' : '暂无生成产物'" table-layout="auto">
      <el-table-column label="内容" min-width="255"><template #default="{ row }"><el-button link type="primary" @click="inspect(row)">{{ row.media_type }}</el-button><span class="id-line">{{ row.id }}</span><small>{{ bytes(row.bytes) }}</small></template></el-table-column>
      <el-table-column label="标签" min-width="180"><template #default="{ row }"><div class="content-tags"><el-tag v-for="tag in row.tags" :key="tag" size="small" type="info">{{ tag }}</el-tag><span v-if="!row.tags?.length" class="muted">未设置</span></div></template></el-table-column>
      <el-table-column label="创建密钥 / 任务" min-width="245"><template #default="{ row }"><span class="mono">{{ row.key_id }}</span><span v-if="row.job_id" class="id-line">{{ row.job_id }}</span></template></el-table-column>
      <el-table-column label="状态" min-width="100"><template #default="{ row }">{{ contentStatus(row.status) }}</template></el-table-column>
      <el-table-column label="到期时间" min-width="170"><template #default="{ row }">{{ time(row.expires_at) }}</template></el-table-column>
      <el-table-column label="操作" min-width="160"><template #default="{ row }"><el-button v-if="canDownload" link :disabled="row.status !== 'available'" @click="download(row)">下载</el-button><el-button v-if="session.allows('content:delete')" link type="danger" :disabled="['pending_reclaim', 'reclaimed'].includes(row.status ?? '')" @click="remove(row)">删除</el-button></template></el-table-column>
    </el-table>
    <div class="content-pagination"><el-button :disabled="page <= 1 || loading" @click="goPage(-1)">上一页</el-button><el-button :disabled="!hasMore || page >= 10000 || loading" @click="goPage(1)">下一页</el-button></div>
  </section>
  <el-drawer v-model="detailsOpen" title="内容详情" size="min(680px, 100vw)" destroy-on-close>
    <div v-loading="detailBusy">
      <el-alert v-if="detailError" :title="detailError" type="error" :closable="false"/>
      <template v-if="selected">
        <div class="content-detail-actions"><el-tag type="info">{{ contentStatus(selected.status) }}</el-tag><el-button :disabled="detailBusy || saving" @click="inspect(selected, true)">重新加载详情</el-button></div>
        <el-descriptions :column="1" border>
          <el-descriptions-item label="内容 ID"><span class="break-id">{{ selected.id }}</span></el-descriptions-item>
          <el-descriptions-item label="创建密钥"><span class="break-id">{{ selected.key_id }}</span></el-descriptions-item>
          <el-descriptions-item v-if="selected.job_id" label="关联任务"><span class="break-id">{{ selected.job_id }}</span></el-descriptions-item>
          <el-descriptions-item label="文件类型">{{ selected.media_type }} · {{ bytes(selected.bytes) }}</el-descriptions-item>
          <el-descriptions-item label="创建时间">{{ time(selected.created_at) }}</el-descriptions-item><el-descriptions-item label="到期时间">{{ time(selected.expires_at) }}</el-descriptions-item>
          <el-descriptions-item label="标签版本">{{ selected.metadata_revision ?? 1 }} · {{ time(selected.metadata_updated_at) }}</el-descriptions-item>
          <el-descriptions-item label="SHA-256"><span class="break-id">{{ selected.sha256 }}</span></el-descriptions-item>
          <el-descriptions-item label="规格"><pre class="code-view">{{ JSON.stringify(selected.metadata, null, 2) }}</pre></el-descriptions-item>
        </el-descriptions>
        <el-form label-position="top" class="content-tag-form" @submit.prevent="saveTags">
          <el-form-item label="整理标签"><el-select v-model="tagDraft" multiple filterable allow-create default-first-option :reserve-keyword="false" :multiple-limit="16" :disabled="!session.allows('content:manage') || saving || detailBusy" placeholder="输入标签后按回车"/></el-form-item>
          <div class="content-tag-actions"><small class="muted">最多 16 个，每个 64 字符；标签不改变内容访问权限。</small><el-button v-if="session.allows('content:manage')" native-type="submit" type="primary" :loading="saving" :disabled="!tagsDirty || detailBusy">保存标签</el-button></div>
        </el-form>
        <section class="content-preview" aria-label="文件预览">
          <div class="content-detail-actions"><strong>文件预览</strong><el-button v-if="canDownload && selected.preview_supported" :disabled="detailBusy || previewActive" @click="preview">预览文件</el-button><el-button v-if="previewActive" @click="clearPreview">关闭预览</el-button></div>
          <p class="muted">{{ selected.preview_supported && canDownload ? '点击后读取文件；离开页面将释放预览。' : '当前文件或权限不支持预览。' }}</p>
          <p v-if="selected.media_type === 'model/gltf-binary'" class="muted">已验证的 GLB 三维模型，可通过授权下载后在 Blender 或兼容工具中打开。网格、顶点和三角面计数见上方规格。</p>
          <el-alert v-if="previewError" :title="previewError" type="warning" :closable="false"/>
          <img v-if="previewActive && selected.media_type.startsWith('image/')" :src="previewUrl" alt="所选内容预览" @error="previewFailed"/>
          <audio v-if="previewActive && selected.media_type.startsWith('audio/')" ref="audio" :src="previewUrl" controls preload="none" @error="previewFailed"/>
          <video v-if="previewActive && selected.media_type.startsWith('video/')" ref="video" :src="previewUrl" controls preload="none" playsinline @error="previewFailed"/>
        </section>
      </template>
    </div>
  </el-drawer>
</template>

<style scoped>
.content-snapshot { font-size: 12px; }
.content-tags { display: flex; flex-wrap: wrap; gap: 4px; max-width: 260px; }
.content-tags :deep(.el-tag__content) { max-width: 240px; overflow: hidden; text-overflow: ellipsis; }
.content-pagination, .content-detail-actions, .content-tag-actions { display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-wrap: wrap; }
.content-pagination { justify-content: flex-end; padding-top: 18px; }
.content-detail-actions { margin: 12px 0; }
.content-tag-form { margin: 24px 0; }
.content-tag-form :deep(.el-select) { width: 100%; }
.content-tag-actions small { flex: 1; min-width: 180px; }
.content-preview { border-top: 1px solid var(--el-border-color); padding-top: 10px; }
.content-preview audio { display: block; width: 100%; min-height: 54px; }
.content-preview img, .content-preview video { display: block; max-width: 100%; width: 100%; max-height: 55vh; object-fit: contain; background: var(--rc-ink); border-radius: 8px; }
</style>
