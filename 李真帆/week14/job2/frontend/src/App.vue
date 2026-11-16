<template>
  <a-layout class="app-layout">
    <a-layout-header class="app-header">
      <div class="brand">多模态 RAG 知识库</div>
      <a-menu mode="horizontal" theme="dark" :selected-keys="[route.path]" @select="onMenu">
        <a-menu-item key="/kb">知识库</a-menu-item>
        <a-menu-item key="/chat">对话</a-menu-item>
      </a-menu>
    </a-layout-header>
    <a-layout-content class="app-content">
      <router-view />
    </a-layout-content>
  </a-layout>
</template>

<script setup lang="ts">
import { useRoute, useRouter } from 'vue-router'

const route = useRoute()
const router = useRouter()

function onMenu({ key }: { key: string | number }) {
  router.push(String(key))
}
</script>

<style scoped>
.app-header {
  display: flex;
  align-items: center;
  gap: 24px;
}
.brand {
  color: #fff;
  font-size: 16px;
  font-weight: 600;
  white-space: nowrap;
}
.app-content {
  min-height: calc(100vh - 64px);
}
</style>
