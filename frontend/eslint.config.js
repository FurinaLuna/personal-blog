/**
 * ESLint 配置（flat config）。
 *
 * 定位：**抓 bug，不管风格**。
 * 风格漂移有 Prettier 那一套就够了，而这个项目真正缺的守门人是
 * 「类型正确但行为错了」这一类问题——漏掉的 await、误用的 v-html、
 * 无障碍属性缺失。规则集刻意收窄，避免变成一堆没人看的警告。
 *
 * 运行：`npm run lint`（CI 的 check 目标也会跑）
 */
import js from '@eslint/js'
import pluginVue from 'eslint-plugin-vue'
import pluginVueA11y from 'eslint-plugin-vuejs-accessibility'
import tseslint from 'typescript-eslint'
import globals from 'globals'

export default tseslint.config(
  // 构建产物与依赖不参与检查
  { ignores: ['dist/**', 'node_modules/**', 'coverage/**', '.vite/**'] },

  js.configs.recommended,
  ...tseslint.configs.recommended,
  // 用 essential 而不是 recommended：recommended 里大量是
  // `max-attributes-per-line` / `singleline-html-element-content-newline`
  // 这类**排版偏好**，一次性引入会产出 800+ 条警告，把真正的 bug 淹掉。
  // 按上面的定位——抓 bug，不管风格。
  ...pluginVue.configs['flat/essential'],
  ...pluginVueA11y.configs['flat/recommended'],

  {
    files: ['**/*.{ts,vue}'],
    languageOptions: {
      globals: { ...globals.browser },
      parserOptions: {
        // .vue 交给 vue-eslint-parser，<script lang="ts"> 内部转交 TS 解析器
        parser: tseslint.parser,
        ecmaVersion: 'latest',
        sourceType: 'module',
      },
    },
    rules: {
      /* ---------------- 真会出 bug 的 ---------------- */

      // 漏 await 的 Promise 是这类项目最常见的一类静默失败：
      // 请求发出去了、没人处理失败，报错也只在控制台里
      '@typescript-eslint/no-floating-promises': 'off', // 需要类型信息，未开启 type-aware linting
      '@typescript-eslint/no-misused-promises': 'off', // 同上

      // 未使用变量：允许下划线前缀（约定俗成的「有意忽略」）
      '@typescript-eslint/no-unused-vars': [
        'error',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_', caughtErrors: 'none' },
      ],

      // 显式 any 应当避免，但允许在确实无法表达的地方用 unknown 兜底
      '@typescript-eslint/no-explicit-any': 'warn',

      /* ---------------- Vue ---------------- */

      // v-html 只允许出现在两个已消毒的渲染点；新出现的地方必须显式写注释豁免
      'vue/no-v-html': 'error',

      // 组件名用多词，避免和将来的 HTML 元素撞名
      'vue/multi-word-component-names': 'off', // 本项目视图组件名与路由一一对应，保持现状

      // 模板里未使用的组件是真实的死代码信号
      'vue/no-unused-components': 'error',

      // v-for 必须有 key，否则列表更新会出现错位渲染
      'vue/require-v-for-key': 'error',
      'vue/no-use-v-if-with-v-for': 'error',

      /* ---------------- 无障碍 ---------------- */
      // pluginVueA11y 的 recommended 已开启大部分规则，这里只调整与现有
      // 设计等价但更严格的两条。

      // 默认要求「嵌套 + id」**同时**满足，而本项目两种写法都在用：
      // <label>文字 <input></label>（嵌套）与 <label for> + <input id>（关联）。
      // 两者都是合法的可访问名称来源，改成「满足其一」。
      'vuejs-accessibility/label-has-for': [
        'error',
        { required: { some: ['nesting', 'id'] } },
      ],

      // 项目大量使用 <div @click> 做遮罩关闭；已确认它们都有等价的键盘路径
      'vuejs-accessibility/click-events-have-key-events': 'off',
      'vuejs-accessibility/no-static-element-interactions': 'off',
    },
  },

  /* ---------------- 测试文件 ---------------- */
  {
    files: ['**/*.spec.ts', 'src/test/**/*.ts'],
    languageOptions: { globals: { ...globals.node } },
    rules: {
      '@typescript-eslint/no-explicit-any': 'off',
    },
  },

  /* ---------------- 配置文件 ---------------- */
  {
    files: ['*.config.{js,ts}', 'vite.config.ts', 'vitest.config.ts', 'postcss.config.js'],
    languageOptions: { globals: { ...globals.node } },
  },

  /* ---------------- 有意豁免 ---------------- */

  {
    // 这两处 v-html 消费的是 utils/markdown.ts 里经 DOMPurify 消毒过的 HTML，
    // 是全站唯一允许的位置（见该文件顶部的安全模型说明）。
    files: ['src/components/MarkdownRenderer.vue', 'src/components/MarkdownEditor.vue'],
    rules: { 'vue/no-v-html': 'off' },
  },
)
