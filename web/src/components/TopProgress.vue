<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'

// Thin indeterminate bar under the header while any API call is in flight. It
// appears only after a short delay so fast requests do not flash the UI, and it
// keeps moving during long ones so the page never looks frozen.
const visible = ref(false)
let timer: number | null = null

function onBusy(event: Event) {
  const busy = (event as CustomEvent<boolean>).detail
  if (busy) {
    if (timer === null && !visible.value) {
      timer = window.setTimeout(() => {
        visible.value = true
        timer = null
      }, 180)
    }
    return
  }
  if (timer !== null) {
    clearTimeout(timer)
    timer = null
  }
  visible.value = false
}

onMounted(() => window.addEventListener('api:busy', onBusy))
onUnmounted(() => {
  window.removeEventListener('api:busy', onBusy)
  if (timer !== null) clearTimeout(timer)
})
</script>

<template>
  <div v-show="visible" class="top-progress" role="progressbar" aria-label="正在加载">
    <div class="bar" />
  </div>
</template>

<style scoped>
.top-progress {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  height: 2px;
  z-index: 3000;
  overflow: hidden;
  background: color-mix(in srgb, var(--brand, #2f6f63) 18%, transparent);
}

.bar {
  height: 100%;
  width: 40%;
  border-radius: 2px;
  background: var(--brand, #2f6f63);
  animation: slide 1.1s ease-in-out infinite;
}

@keyframes slide {
  0% {
    transform: translateX(-100%);
  }
  50% {
    transform: translateX(120%);
  }
  100% {
    transform: translateX(260%);
  }
}

@media (prefers-reduced-motion: reduce) {
  .bar {
    animation-duration: 3s;
  }
}
</style>
