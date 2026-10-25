<script setup lang="ts">
defineProps<{ description: string | null; uncertain: boolean; busy: boolean; active: boolean; error: string }>()
defineEmits<{ retry: []; clear: [] }>()
</script>
<template>
  <el-alert v-if="error" :title="error" type="error" :closable="false"/>
  <section v-if="description" class="model-request-status" aria-label="模型请求核对">
    <strong>{{ busy ? '请求正在处理' : '请求结果待核对' }}</strong><p>{{ description }}</p>
    <p class="form-help">关闭本地记录不会撤销服务器操作。结果未知时，先使用原请求核对或查看模型目录、验证任务。</p>
    <div class="toolbar"><el-button :disabled="busy || !uncertain || !active" size="small" type="primary" @click="$emit('retry')">使用原请求核对</el-button><el-button :disabled="busy || !active" size="small" @click="$emit('clear')">关闭本地记录</el-button></div>
  </section>
</template>
<style scoped>
.model-request-status { padding: 14px; margin: 16px 0; border: 1px solid var(--rc-line); border-radius: 4px; background: var(--rc-canvas); overflow-wrap: anywhere; }
</style>
