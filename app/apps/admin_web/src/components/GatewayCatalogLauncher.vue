<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { api, readableError, unwrap } from '../api'
import type { components } from '../generated/api'
import { useSession } from '../session'
import GatewayCatalogDrawer from './GatewayCatalogDrawer.vue'

const session = useSession(), open = ref(false), catalogOpen = ref(false), visible = ref(!document.hidden)
const selected = ref<components['schemas']['CatalogGatewayOption'] | null>(null)
const data = ref<components['schemas']['Page_CatalogGatewayOption_'] | null>(null), loading = ref(false), error = ref(''), query = ref(''), applied = ref(''), page = ref(1)
const active = computed(() => open.value && visible.value && !!session.info && !session.ending)
let controller: AbortController | null = null, timer: ReturnType<typeof setTimeout> | null = null, disposed = false
function stop() { if (timer) clearTimeout(timer); timer = null; controller?.abort(); controller = null; loading.value = false }
async function load(delta = 0, reset = false) {
  if (!active.value || page.value + delta < 1) return
  stop(); if (reset) { page.value = 1; applied.value = query.value.trim() } else page.value += delta
  const own = new AbortController(); controller = own; timer = setTimeout(() => own.abort(new DOMException('读取超时', 'TimeoutError')), 10000); loading.value = true; data.value = null
  try { const result = unwrap(await api.GET('/admin/api/v1/gateway-catalog-options', { signal: own.signal, params: { query: { page: page.value, q: applied.value || undefined } } })); if (!disposed && active.value && controller === own && !own.signal.aborted) { data.value = result; error.value = '' } }
  catch (e) { if (!disposed && active.value && controller === own) error.value = own.signal.aborted ? '读取超时，请重试' : readableError(e) }
  finally { if (controller === own) stop() }
}
function choose(row: components['schemas']['CatalogGatewayOption']) { if (!active.value) return; selected.value = row; open.value = false; catalogOpen.value = true }
function start() { visible.value = !document.hidden; query.value = ''; applied.value = ''; page.value = 1; data.value = null; error.value = ''; open.value = true; void load() }
async function visibility() {
  visible.value = false; stop()
  if (document.hidden || !open.value || !session.info || session.ending) return
  const own = new AbortController(); controller = own; timer = setTimeout(() => own.abort(), 10000)
  try { await session.refresh(own.signal); if (!disposed && controller === own && !own.signal.aborted && session.info && !session.ending) { visible.value = true; void load() } }
  catch (e) { if (controller === own && !disposed) error.value = readableError(e) }
  finally { if (controller === own) stop() }
}
watch(open, value => { if (!value) { stop(); data.value = null; query.value = ''; applied.value = '' } })
watch(catalogOpen, value => { if (!value) selected.value = null })
watch(() => [session.info?.csrf_token, session.ending] as const, ([token, ending], [old]) => { if (!token || token !== old || ending) { stop(); open.value = false; catalogOpen.value = false; selected.value = null; data.value = null } })
onMounted(() => document.addEventListener('visibilitychange', visibility))
onUnmounted(() => { disposed = true; stop(); document.removeEventListener('visibilitychange', visibility) })
</script>
<template>
  <el-button v-if="session.allows('models:read')" @click="start">网关模型目录</el-button>
  <el-dialog v-model="open" title="选择网关目录" width="min(760px, 96vw)" :close-on-click-modal="false">
    <p class="form-help">选择要查看或发布模型目录的网关。此处只展示目录所需的名称和标识。</p>
    <el-alert v-if="error" :title="error" type="error" :closable="false"/>
    <el-form label-position="top" @submit.prevent="load(0, true)"><el-form-item label="网关名称搜索"><el-input v-model="query" aria-label="网关名称搜索" maxlength="128" :disabled="!active"/></el-form-item><el-button native-type="submit" :disabled="!active" :loading="loading">搜索网关</el-button></el-form>
    <el-table :data="data?.data ?? []" v-loading="loading" table-layout="auto" empty-text="暂无匹配网关；可调整搜索或由管理员登记网关服务"><el-table-column label="名称 / ID" min-width="230"><template #default="{ row }"><strong>{{ row.name }}</strong><span class="id-line">{{ row.id }}</span></template></el-table-column><el-table-column label="状态" width="90"><template #default="{ row }">{{ row.enabled ? '已启用' : '已停用' }}</template></el-table-column><el-table-column label="操作" width="100"><template #default="{ row }"><el-button :disabled="!active" @click="choose(row)">打开目录</el-button></template></el-table-column></el-table>
    <div class="pagination"><el-button :disabled="page === 1 || loading || !active" @click="load(-1)">上一页</el-button><span>第 {{ page }} 页</span><el-button :disabled="!data?.has_more || loading || !active" @click="load(1)">下一页</el-button></div>
  </el-dialog>
  <GatewayCatalogDrawer v-model="catalogOpen" :gateway="selected"/>
</template>
