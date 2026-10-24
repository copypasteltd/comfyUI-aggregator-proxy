<script setup lang="ts">
defineProps<{ description: string | null; uncertain: boolean; busy: boolean; active: boolean; error: string; canInspect: boolean }>()
defineEmits<{ retry: []; clear: []; inspect: [] }>()
</script>
<template>
  <el-alert v-if="error" :title="error" type="error" :closable="false"/>
  <section v-if="description" class="key-request-status" aria-label="Key 请求核对">
    <strong>{{ busy ? '请求正在处理' : '请求结果待核对' }}</strong><p>{{ description }}</p>
    <p class="form-help">关闭本地记录不会撤销服务器操作。创建或轮换的首次密钥无法通过重放再次显示；请先核对当前状态，再决定后续操作。</p>
    <div class="toolbar"><el-button :disabled="busy || !uncertain || !active" size="small" type="primary" @click="$emit('retry')">使用原请求核对</el-button><el-button v-if="canInspect" :disabled="busy || !active" size="small" @click="$emit('inspect')">查看当前策略</el-button><el-button :disabled="busy || !active" size="small" @click="$emit('clear')">关闭本地记录</el-button></div>
  </section>
</template>
<style scoped>
.key-request-status { padding: 14px; margin: 16px 0; border: 1px solid #d7e1e8; border-radius: 4px; background: #f5f8fa; overflow-wrap: anywhere; }
</style>
