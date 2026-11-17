<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'

const props = defineProps<{ instanceId: number; title: string }>()
const emit = defineEmits<{ close: [] }>()

const host = ref<HTMLDivElement | null>(null)
const state = ref<'connecting' | 'open' | 'closed' | 'error'>('connecting')
const message = ref('')

let term: Terminal | null = null
let fit: FitAddon | null = null
let socket: WebSocket | null = null
let observer: ResizeObserver | null = null
let disposed = false

const STATE_TEXT: Record<typeof state.value, string> = {
  connecting: '正在连接容器…',
  open: '已连接',
  closed: '连接已断开',
  error: '连接失败',
}

function sendResize() {
  if (!socket || socket.readyState !== WebSocket.OPEN || !term) return
  socket.send(JSON.stringify({ type: 'resize', cols: term.cols, rows: term.rows }))
}

function connect() {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const url = `${protocol}//${window.location.host}/api/v1/admin/instances/${props.instanceId}/terminal`
  socket = new WebSocket(url)
  socket.binaryType = 'arraybuffer'

  socket.onopen = () => {
    state.value = 'open'
    sendResize()
  }

  socket.onmessage = (event) => {
    if (typeof event.data === 'string') {
      try {
        const control = JSON.parse(event.data) as { type?: string; message?: string }
        if (control.type === 'error') {
          state.value = 'error'
          message.value = control.message ?? '无法打开终端'
          term?.write(`\r\n\x1b[31m${message.value}\x1b[0m\r\n`)
        }
        // `ready` and `pong` need no UI action.
      } catch {
        term?.write(event.data)
      }
      return
    }
    term?.write(new Uint8Array(event.data as ArrayBuffer))
  }

  socket.onerror = () => {
    if (state.value !== 'open') state.value = 'error'
    message.value = '无法建立 WebSocket 连接'
  }

  socket.onclose = () => {
    if (disposed) return
    state.value = 'closed'
    term?.write('\r\n\x1b[90m[连接已断开]\x1b[0m\r\n')
  }
}

onMounted(() => {
  term = new Terminal({
    fontFamily: '"Cascadia Mono", "JetBrains Mono", Consolas, "Courier New", monospace',
    fontSize: 13,
    lineHeight: 1.25,
    cursorBlink: true,
    convertEol: false,
    scrollback: 5000,
    theme: {
      background: '#0e1419',
      foreground: '#d6e0e5',
      cursor: '#7fbfae',
      cursorAccent: '#0e1419',
      selectionBackground: 'rgba(127, 191, 174, 0.28)',
      black: '#1b232c',
      red: '#c97b6f',
      green: '#8fbf9f',
      yellow: '#d6b37a',
      blue: '#7fa6c9',
      magenta: '#b79ac9',
      cyan: '#88c4bd',
      white: '#d6e0e5',
      brightBlack: '#5b6670',
      brightRed: '#e0968a',
      brightGreen: '#a8d4b5',
      brightYellow: '#e8c78e',
      brightBlue: '#9cbfdc',
      brightMagenta: '#cbb2da',
      brightCyan: '#a3d8d1',
      brightWhite: '#f1f5f7',
    },
  })
  fit = new FitAddon()
  term.loadAddon(fit)
  if (host.value) {
    term.open(host.value)
    fit.fit()
  }

  // Keystrokes go out as binary so they can never be mistaken for a JSON
  // control frame (a literal `{"type":...}` typed at the prompt, for example).
  const encoder = new TextEncoder()
  term.onData((data) => {
    if (socket?.readyState === WebSocket.OPEN) socket.send(encoder.encode(data))
  })
  term.onResize(sendResize)

  if (host.value && typeof ResizeObserver !== 'undefined') {
    observer = new ResizeObserver(() => {
      try {
        fit?.fit()
      } catch {
        // the element can be detached while the dialog closes
      }
    })
    observer.observe(host.value)
  }

  connect()
  term.focus()
})

onBeforeUnmount(() => {
  disposed = true
  observer?.disconnect()
  socket?.close()
  term?.dispose()
  term = null
})
</script>

<template>
  <div class="terminal-shell">
    <div class="terminal-bar">
      <span class="status" :class="state">
        <span class="dot" />
        {{ STATE_TEXT[state] }}
      </span>
      <span class="hint">Ctrl+C 中断 · Tab 补全 · 滚轮翻历史</span>
      <el-button link class="close" @click="emit('close')">关闭</el-button>
    </div>
    <div ref="host" class="terminal-host" />
    <p v-if="state === 'error' && message" class="error">{{ message }}</p>
  </div>
</template>

<style scoped>
.terminal-shell {
  display: flex;
  flex-direction: column;
  border-radius: 12px;
  overflow: hidden;
  border: 1px solid #1f2a33;
  background: #0e1419;
}

.terminal-bar {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 8px 12px;
  background: #131b22;
  border-bottom: 1px solid #1f2a33;
}

.status {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 11.5px;
  color: #8b9aa5;
}

.status .dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #6b7780;
}

.status.open {
  color: #8fbf9f;
}

.status.open .dot {
  background: #7fbfae;
  box-shadow: 0 0 0 3px rgba(127, 191, 174, 0.14);
}

.status.connecting .dot {
  background: #d6b37a;
}

.status.error,
.status.closed {
  color: #d99a8f;
}

.status.error .dot,
.status.closed .dot {
  background: #c97b6f;
}

.hint {
  flex: 1;
  text-align: right;
  font-size: 11px;
  color: #5b6670;
}

.close {
  color: #9fb0ba;
  font-size: 12px;
}

.terminal-host {
  height: 440px;
  padding: 10px 12px;
}

.terminal-host :deep(.xterm) {
  height: 100%;
}

.error {
  margin: 0;
  padding: 0 12px 10px;
  color: #d99a8f;
  font-size: 12px;
}

@media (max-width: 720px) {
  .terminal-host {
    height: 320px;
  }
  .hint {
    display: none;
  }
}
</style>
