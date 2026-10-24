<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { api, readableError, revisionHeaders, unwrap, type Channel } from '../api'
import { useSession } from '../session'
const props = defineProps<{ modelValue: boolean; target: Channel | null; active: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: boolean]; applied: [] }>()
const session = useSession(), busy = ref(false), error = ref(''), operation = ref(''), uncertain = ref(false)
let alive = true, request: AbortController | null = null
function visible(id: string) { return alive && props.modelValue && props.active && !document.hidden && props.target?.id === id && !!session.info && !session.ending }
async function confirm() {
  const target = props.target
  if (!target || busy.value || operation.value || uncertain.value || !visible(target.id)) return
  const own = new AbortController(); request = own; busy.value = true; error.value = ''
  const timer = setTimeout(() => own.abort(), 10000)
  try {
    const value = unwrap(await api.POST('/admin/api/v1/channels/{identifier}/reapply', { params: { path: { identifier: target.id } }, headers: revisionHeaders(target.revision), body: { expected_revision: target.revision }, signal: own.signal }))
    if (visible(target.id) && request === own && !own.signal.aborted) { operation.value = value.operation_id ?? ''; emit('applied') }
  } catch (e) { if (visible(target.id) && request === own) { uncertain.value = true; error.value = own.signal.aborted ? '请求结果未确认，请到操作中心核对并刷新渠道；超时不代表已回滚。' : readableError(e) } }
  finally { clearTimeout(timer); if (request === own) { request = null; busy.value = false } }
}
watch(() => [props.modelValue, props.target?.id, props.active, session.info?.csrf_token, session.ending] as const, ([open, , active, token, ending]) => {
  if (!open || !active || !token || ending) { request?.abort(); request = null; busy.value = false; if (open) emit('update:modelValue', false) }
  if (!open) { error.value = ''; operation.value = ''; uncertain.value = false }
})
onBeforeUnmount(() => { alive = false; request?.abort(); request = null })
</script>
<template>
  <el-dialog :model-value="modelValue" title="重新应用当前配置" width="min(540px, 94vw)" top="8vh" :show-close="!busy" :close-on-click-modal="!busy" :close-on-press-escape="!busy" @update:model-value="emit('update:modelValue', $event)">
    <p>{{ target?.name }}</p><p class="mono reapply-id">{{ target?.id }}</p>
    <p>提交期望配置版本 {{ target?.desired_revision }}。渠道将停用并排空，撤销验证与环境批准。完成后仍需重新探测、同步和验证。</p>
    <p class="form-help">未结束的指令必须先核对；GPU 忙碌时等待排空。此操作不安装模型或自定义节点。</p>
    <el-alert v-if="error" :title="error" type="error" :closable="false"/>
    <el-alert v-if="operation" title="重新应用已提交，请在操作中心查看结果。" type="success" :closable="false"/>
    <p v-if="operation"><router-link :to="{ path: '/operations', query: { operation_id: operation } }">查看本次管理操作</router-link></p><p v-else-if="uncertain"><router-link to="/operations">进入操作中心核对</router-link></p>
    <template #footer><el-button :disabled="busy" @click="emit('update:modelValue', false)">关闭</el-button><el-button type="primary" :loading="busy" :disabled="!!operation || uncertain || !active" @click="confirm">确认重新应用</el-button></template>
  </el-dialog>
</template>
<style scoped>.reapply-id { overflow-wrap: anywhere; font-size: 12px; }</style>
