<script setup lang="ts">
import { onUnmounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api, readableError, revisionHeaders, unwrap } from '../api'

const props = defineProps<{ jobId: string; revision: number }>()
const open = ref(false), busy = ref(false), evidence = ref(''), error = ref('')
let frozen = { jobId: '', revision: 0 }, intent = '', token = '', disposed = false
const controller = new AbortController()
function begin() { frozen = { jobId: props.jobId, revision: props.revision }; evidence.value = ''; error.value = ''; intent = ''; token = ''; open.value = true }
async function submit() {
  if (busy.value) return
  const body = { job_id: frozen.jobId, expected_revision: frozen.revision, evidence_id: evidence.value.trim() }
  if (intent !== JSON.stringify(body)) { intent = JSON.stringify(body); token = crypto.randomUUID() }
  busy.value = true; error.value = ''
  try {
    unwrap(await api.POST('/admin/api/v1/spec-isolations', { signal: controller.signal, headers: { ...revisionHeaders(frozen.revision), 'Idempotency-Key': token }, body }))
    if (!disposed) { open.value = false; intent = ''; token = ''; ElMessage.success('该 GPU 上的生成规格已隔离') }
  } catch (e) { if (!disposed) error.value = readableError(e) }
  finally { busy.value = false }
}
onUnmounted(() => { disposed = true; controller.abort() })
</script>

<template>
  <el-button type="danger" plain @click="begin">隔离此生成规格</el-button>
  <el-dialog v-model="open" title="隔离此生成规格" width="min(560px, 94vw)" append-to-body :close-on-click-modal="false" :close-on-press-escape="!busy" :show-close="!busy">
    <p class="muted">暂停当前执行 GPU 上同版本、同规格的新客户任务。已有执行继续处理，可在规格隔离页面查看和恢复。</p>
    <el-alert v-if="error" :title="error" type="error" :closable="false"/>
    <el-form label-position="top" @submit.prevent="submit"><el-form-item label="调查记录编号" for="job-spec-evidence"><el-input id="job-spec-evidence" v-model="evidence" placeholder="例如 incident-20261023" maxlength="128" :disabled="busy"/><p class="muted">8～128 位字母、数字及 . _ : -。</p></el-form-item><div class="actions"><el-button :disabled="busy" @click="open = false">返回</el-button><el-button native-type="submit" type="danger" :loading="busy">确认隔离</el-button></div></el-form>
  </el-dialog>
</template>
