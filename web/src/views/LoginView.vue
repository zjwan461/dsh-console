<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ArrowRight, Box, Lock, Timer } from '@element-plus/icons-vue'
import { useAuthStore } from '@/stores/auth'
import { errorMessage } from '@/api/client'

const auth = useAuthStore()
const router = useRouter()
const route = useRoute()
const loading = ref(false)
const form = reactive({ username: '', password: '', remember: true })

async function submit() {
  if (!form.username || !form.password) {
    ElMessage.warning('请输入用户名和密码')
    return
  }
  loading.value = true
  try {
    await auth.login(form.username.trim(), form.password, form.remember)
    const next = typeof route.query.next === 'string' ? route.query.next : '/'
    router.replace(next)
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    loading.value = false
  }
}

const highlights = [
  {
    icon: Box,
    title: '一人一舱',
    desc: '独立子域名、数据目录与配额，互不打扰',
  },
  { icon: Timer, title: '按活跃计量', desc: '闲置自动休眠，离开不烧时长' },
  { icon: Lock, title: '数据留舱', desc: '快照与镜像随时回到上一次现场' },
]
</script>

<template>
  <div class="login">
    <section class="brand-panel">
      <div class="panel-inner">
        <div class="wordmark">
          <span class="mark">墨</span>
          <span class="wordmark-text">
            <strong>优墨云舱</strong>
            <small>YouMo Cloud Cabin</small>
          </span>
        </div>

        <div class="pitch">
          <h2>一人一舱<br />随处开工</h2>
          <p>
            把 DeepSeek Harness 装进每个人自己的云端工作舱：登录即用，离开自动休眠，回来接着做。
          </p>
        </div>

        <ul class="highlights">
          <li v-for="item in highlights" :key="item.title">
            <el-icon><component :is="item.icon" /></el-icon>
            <div>
              <strong>{{ item.title }}</strong>
              <span>{{ item.desc }}</span>
            </div>
          </li>
        </ul>

        <div class="panel-foot">DSH 云电脑 · 控制台</div>
      </div>
      <div class="grain" />
    </section>

    <section class="form-panel">
      <div class="form-wrap">
        <header class="form-head">
          <span class="eyebrow">欢迎回来</span>
          <h1>登录云舱</h1>
          <p>使用管理员分配的账号，或凭邀请码注册后登录。</p>
        </header>

        <el-form label-position="top" size="large" @submit.prevent="submit">
          <el-form-item label="用户名">
            <el-input
              v-model="form.username"
              placeholder="请输入用户名"
              autocomplete="username"
              autofocus
            />
          </el-form-item>
          <el-form-item label="密码">
            <el-input
              v-model="form.password"
              type="password"
              show-password
              placeholder="请输入密码"
              autocomplete="current-password"
              @keyup.enter="submit"
            />
          </el-form-item>

          <div class="form-row">
            <el-checkbox v-model="form.remember">保持登录 7 天</el-checkbox>
          </div>

          <el-button type="primary" class="submit" :loading="loading" @click="submit">
            进入控制台
            <el-icon class="submit-arrow"><ArrowRight /></el-icon>
          </el-button>
        </el-form>

        <div class="form-foot">
          还没有账号？<router-link to="/register">用邀请码注册</router-link>
        </div>
      </div>

      <p class="copyright">优墨系列 · 云端工作舱</p>
    </section>
  </div>
</template>

<style scoped>
.login {
  min-height: 100vh;
  display: grid;
  grid-template-columns: minmax(420px, 46%) 1fr;
  background: var(--paper);
}

/* ── left: ink panel ─────────────────────────────────────────────────── */

.brand-panel {
  position: relative;
  overflow: hidden;
  color: #e8ecef;
  background:
    radial-gradient(620px 460px at 18% 8%, rgba(47, 111, 99, 0.42), transparent 68%),
    radial-gradient(520px 420px at 88% 92%, rgba(169, 113, 63, 0.22), transparent 66%),
    linear-gradient(155deg, #121a22 0%, #16212b 52%, #101820 100%);
  display: flex;
  align-items: center;
}

.brand-panel::after {
  /* faint cabin contour lines */
  content: '';
  position: absolute;
  inset: -20% -30% auto auto;
  width: 620px;
  height: 620px;
  border-radius: 50%;
  border: 1px solid rgba(255, 255, 255, 0.05);
  box-shadow:
    0 0 0 70px rgba(255, 255, 255, 0.022),
    0 0 0 150px rgba(255, 255, 255, 0.014);
  pointer-events: none;
}

.panel-inner {
  position: relative;
  z-index: 1;
  padding: 56px 60px;
  max-width: 560px;
  display: flex;
  flex-direction: column;
  min-height: 100%;
}

.wordmark {
  display: flex;
  align-items: center;
  gap: 12px;
}

.mark {
  width: 38px;
  height: 38px;
  border-radius: 11px;
  display: grid;
  place-items: center;
  background: #f5f2ec;
  color: #121a22;
  font-family: 'Songti SC', 'STSong', Georgia, serif;
  font-size: 21px;
  font-weight: 700;
}

.wordmark-text {
  display: flex;
  flex-direction: column;
  line-height: 1.25;
}

.wordmark-text strong {
  font-size: 16px;
  letter-spacing: 0.04em;
}

.wordmark-text small {
  font-size: 10.5px;
  letter-spacing: 0.16em;
  color: #7f8e9b;
  text-transform: uppercase;
  margin-top: 2px;
}

.pitch {
  margin-top: auto;
  padding-top: 64px;
}

.pitch h2 {
  font-size: 42px;
  line-height: 1.24;
  margin: 0 0 20px;
  font-weight: 600;
  letter-spacing: 0.02em;
  color: #f6f4ef;
}

.pitch p {
  margin: 0;
  color: #98a6b2;
  font-size: 14px;
  line-height: 1.85;
  max-width: 400px;
}

.highlights {
  list-style: none;
  margin: 44px 0 0;
  padding: 0;
  display: grid;
  gap: 18px;
}

.highlights li {
  display: flex;
  gap: 13px;
  align-items: flex-start;
}

.highlights .el-icon {
  width: 30px;
  height: 30px;
  flex: 0 0 30px;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.055);
  border: 1px solid rgba(255, 255, 255, 0.07);
  color: #9ed3c4;
  font-size: 15px;
}

.highlights div {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.highlights strong {
  font-size: 13.5px;
  font-weight: 550;
  color: #e4e9ed;
}

.highlights span {
  font-size: 12.5px;
  color: #8b99a5;
  line-height: 1.6;
}

.panel-foot {
  margin-top: auto;
  padding-top: 48px;
  font-size: 11px;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  color: #5c6a76;
}

.grain {
  position: absolute;
  inset: 0;
  pointer-events: none;
  opacity: 0.5;
  background-image: radial-gradient(rgba(255, 255, 255, 0.045) 1px, transparent 1px);
  background-size: 3px 3px;
}

/* ── right: form ─────────────────────────────────────────────────────── */

.form-panel {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 48px 32px 28px;
  position: relative;
}

.form-wrap {
  width: 100%;
  max-width: 380px;
}

.form-head {
  margin-bottom: 30px;
}

.eyebrow {
  display: inline-block;
  font-size: 11px;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  color: var(--primary);
  margin-bottom: 12px;
}

.form-head h1 {
  font-size: 30px;
  margin: 0 0 10px;
  font-weight: 600;
  letter-spacing: -0.02em;
}

.form-head p {
  margin: 0;
  color: var(--muted);
  font-size: 13px;
  line-height: 1.7;
}

.form-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin: 4px 0 24px;
}

.submit {
  width: 100%;
  height: 46px;
  font-size: 14.5px;
  letter-spacing: 0.04em;
}

.submit-arrow {
  margin-left: 8px;
  font-size: 15px;
}

.form-foot {
  margin-top: 24px;
  text-align: center;
  color: var(--muted);
  font-size: 13px;
}

.form-foot a {
  font-weight: 500;
  margin-left: 2px;
}

.copyright {
  position: absolute;
  bottom: 22px;
  font-size: 11.5px;
  color: #a8a094;
  letter-spacing: 0.04em;
}

@media (max-width: 980px) {
  .login {
    grid-template-columns: 1fr;
  }
  .brand-panel {
    display: none;
  }
  .form-panel {
    min-height: 100vh;
    background:
      radial-gradient(620px 380px at 50% -10%, rgba(47, 111, 99, 0.12), transparent 70%),
      var(--paper);
  }
}
</style>
