<script setup lang="ts">
defineProps<{ description: string | null; uncertain: boolean; busy: boolean; active: boolean; error: string; canInspect: boolean }>()
defineEmits<{ retry: []; clear: []; inspect: [] }>()
</script>
<template>
  <el-alert v-if="error" :title="error" type="error" :closable="false"/>
  <section v-if="description" class="request-status" aria-label="Workflow 请求核对">
    <strong>{{ busy ? '请求正在处理' : '请求结果待核对' }}</strong><p>{{ description }}</p>
    <p class="form-help">关闭本地记录不会撤销服务器操作。核对沿用原正文、幂等键及草稿版本，不会自动覆盖当前草稿。</p>
    <div class="toolbar"><el-button :disabled="busy || !uncertain || !active" size="small" type="primary" @click="$emit('retry')">使用原请求核对</el-button><el-button v-if="canInspect" :disabled="busy || !active" size="small" @click="$emit('inspect')">查看当前草稿</el-button><el-button :disabled="busy || !active" size="small" @click="$emit('clear')">关闭本地记录</el-button></div>
  </section>
</template>
<style scoped>
.request-status { padding: 14px; margin: 16px 0; border: 1px solid var(--rc-line); border-radius: 4px; background: var(--rc-canvas); overflow-wrap: anywhere; }
</style>
