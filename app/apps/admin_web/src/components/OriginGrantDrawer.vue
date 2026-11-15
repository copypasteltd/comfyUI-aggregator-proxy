<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { api, readableError, revisionHeaders, unwrap, type KeyRecord } from '../api'
import type { components } from '../generated/api'

const props = defineProps<{ record: KeyRecord | null; active: boolean }>()
const emit = defineEmits<{ close: [] }>()
const view = ref<components['schemas']['OriginGrantView'] | null>(null), enabled = ref(false), channels = ref<string[]>([]), loading = ref(false), writing = ref(false), error = ref(''), notice = ref('')
let controller: AbortController | null = null, disposed = false
async function load() {
  controller?.abort(); view.value = null; error.value = ''; notice.value = ''
  if (!props.record || !props.active) return
  const own = new AbortController(); controller = own; loading.value = true
  const deadline = setTimeout(() => own.abort(), 10000)
  try { const value = unwrap(await api.GET('/admin/api/v1/api-keys/{identifier}/origin', { params: { path: { identifier: props.record.id } }, signal: own.signal })); if (!disposed && controller === own && !own.signal.aborted) { view.value = value; enabled.value = value.enabled; channels.value = [...value.channel_ids] } }
  catch (e) { if (!disposed && controller === own) error.value = readableError(e) }
  finally { clearTimeout(deadline); if (controller === own) { loading.value = false; controller = null } }
}
async function save() {
  if (!props.record || !view.value || writing.value || !props.active) return
  const own = new AbortController(); controller = own; writing.value = true; error.value = ''; notice.value = ''
  const deadline = setTimeout(() => own.abort(), 10000)
  const identifier = props.record.id, expected = view.value.revision
  try {
    unwrap(await api.PATCH('/admin/api/v1/api-keys/{identifier}/origin', { params: { path: { identifier } }, headers: { ...revisionHeaders(expected), 'Idempotency-Key': crypto.randomUUID() }, body: { expected_revision: expected, enabled: enabled.value, channel_ids: channels.value }, signal: own.signal }))
    if (!disposed && controller === own) { notice.value = '原生面板授权已保存。'; view.value.revision++ }
  } catch (e) { if (!disposed && controller === own) error.value = `${readableError(e)}。结果不明确时请先“核对当前配置”，不要重复覆盖。` }
  finally { clearTimeout(deadline); if (controller === own) { writing.value = false; controller = null } }
}
function close(value: boolean) { if (!value && !writing.value) emit('close') }
watch(() => [props.record?.id, props.active], () => { if (!writing.value) void load() }, { immediate: true })
onBeforeUnmount(() => { disposed = true; controller?.abort() })
</script>

<template>
  <el-drawer :model-value="!!record" title="原生 ComfyUI 访问授权" size="min(560px, 100vw)" :close-on-click-modal="!writing" :close-on-press-escape="!writing" :show-close="!writing" @update:model-value="close">
    <div v-loading="loading"><p v-if="record"><strong>{{ record.name }}</strong></p><el-alert v-if="error" :title="error" type="error" :closable="false"/><el-alert v-if="notice" :title="notice" type="success" :closable="false"/>
      <el-form v-if="view" label-position="top" :disabled="writing || !active" @submit.prevent="save"><section class="policy-block"><el-form-item label="允许使用原生面板"><el-switch v-model="enabled" aria-label="允许使用原生面板"/></el-form-item><p class="form-help">单独授权后，此 Key 才能登录 /origin/。现有生成 API 权限及日额度不会改变。</p></section><section class="policy-block"><el-form-item label="获准访问的设备"><el-checkbox-group v-model="channels"><el-checkbox v-for="device in view.options" :key="device.id" :value="device.id">{{ device.name }}{{ device.enabled ? '' : '（未启用）' }}</el-checkbox></el-checkbox-group></el-form-item><p v-if="!view.options.length" class="form-help">此项目尚未登记 ComfyUI 渠道。</p><p class="form-help">授权范围还受 Key 原有渠道选择约束。进入设备需申请独占，已有任务和维护占用会保留。</p></section><el-alert :title="view.trust_notice" type="warning" :closable="false"/><div class="drawer-footer"><el-button :disabled="writing || !active" @click="load">核对当前配置</el-button><el-button type="primary" native-type="submit" :loading="writing" :disabled="!active || (enabled && !channels.length)">保存原生授权</el-button></div></el-form>
      <el-button v-else :disabled="loading || !active" @click="load">重新读取</el-button>
    </div>
  </el-drawer>
</template>
