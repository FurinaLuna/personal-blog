import typography from '@tailwindcss/typography'

/** @type {import('tailwindcss').Config} */
export default {
  // 排除测试文件：spec 里出现的类名不该进构建产物。
  // 实测（2026-09-22）：当前排除前后产物**一个字节都不差**（60499 = 60499），
  // 说明现在没有"只出现在测试里"的类；留着是为了防止将来出现时悄悄带上。
  content: ['./index.html', './src/**/*.{vue,js,ts,jsx,tsx}', '!./src/**/*.spec.ts'],
  // 主题切换靠给 <html> 加 .dark 类，而不是只跟随系统——
  // 用户手动选过之后应当被记住（存在 localStorage）。
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        // 颜色统一走 CSS 变量，暗色模式只需要换一组变量值，
        // 不用在几十个组件里到处写 dark:bg-xxx dark:text-yyy
        bg: 'rgb(var(--c-bg) / <alpha-value>)',
        surface: 'rgb(var(--c-surface) / <alpha-value>)',
        'surface-muted': 'rgb(var(--c-surface-muted) / <alpha-value>)',
        border: 'rgb(var(--c-border) / <alpha-value>)',
        ink: 'rgb(var(--c-ink) / <alpha-value>)',
        'ink-soft': 'rgb(var(--c-ink-soft) / <alpha-value>)',
        'ink-faint': 'rgb(var(--c-ink-faint) / <alpha-value>)',
        // 色值定义在 tokens.css（单一来源），这里只做引用
        brand: {
          50: 'rgb(var(--c-brand-50) / <alpha-value>)',
          100: 'rgb(var(--c-brand-100) / <alpha-value>)',
          200: 'rgb(var(--c-brand-200) / <alpha-value>)',
          300: 'rgb(var(--c-brand-300) / <alpha-value>)',
          400: 'rgb(var(--c-brand-400) / <alpha-value>)',
          500: 'rgb(var(--c-brand-500) / <alpha-value>)',
          600: 'rgb(var(--c-brand-600) / <alpha-value>)',
          700: 'rgb(var(--c-brand-700) / <alpha-value>)',
          800: 'rgb(var(--c-brand-800) / <alpha-value>)',
          900: 'rgb(var(--c-brand-900) / <alpha-value>)',
        },
        accent: {
          DEFAULT: 'rgb(var(--c-accent) / <alpha-value>)',
          soft: 'rgb(var(--c-accent-soft) / <alpha-value>)',
        },
      },
      // 字体栈在 src/styles/tokens.css 里定义（单一来源），这里只做引用，
      // 避免「同一个字体栈写在两个文件里」——那种重复迟早只改一处
      fontFamily: {
        sans: ['var(--font-sans)'],
        display: ['var(--font-display)'],
        mono: ['var(--font-mono)'],
      },
      maxWidth: {
        // 中文正文每行 38~42 字是舒适区，760px 偏宽。
        // 这个刻度是给**通栏**正文的（关于页 / 留言板 / 友链 / 404 等单列页面）。
        content: '700px',
        shell: '1120px',
        // 文章详情页的外壳：比 shell 宽，因为它是**两栏**（正文 + 目录），
        // 通栏宽度不再等于正文宽度 —— 详情页需要自己的刻度。
        // 正文列自己在组件里用 `article-column` 限宽（见 components.css）。
        article: '1280px',
      },
      typography: (theme) => ({
        DEFAULT: {
          css: {
            maxWidth: 'none',
            color: 'rgb(var(--c-ink))',
            // 正文字号 18px：实测参考站就是 18px，16px 在中文长文里偏小。
            // 配合下面的行高，760px 列宽下一行约 42 个中文字，
            // 落在仓库 maxWidth.content 注释认可的 38~42 舒适区。
            fontSize: '18px',
            // 中文正文 1.85 偏松，1.75 在「不局促」和「读得下去」之间更合适。
            // 18 × 1.85 = 33.3px，比 1.75（31.5px）更接近参考站的 1.9。
            lineHeight: '1.85',
            a: {
              color: theme('colors.brand.600'),
              textDecoration: 'none',
              borderBottom: `1px solid ${theme('colors.brand.200')}`,
              '&:hover': { color: theme('colors.brand.700') },
            },
            'h2, h3, h4': { color: 'rgb(var(--c-ink))', fontWeight: '600' },
            // 正文里的 h1/h2 跟随页头标题用衬线，h3 以下保持无衬线——
            // 衬线大标题 + 无衬线小标题是常见的编辑排版层次
            'h1, h2': { fontFamily: 'var(--font-display)' },
            code: {
              color: theme('colors.brand.700'),
              backgroundColor: theme('colors.brand.50'),
              padding: '0.15em 0.4em',
              borderRadius: '4px',
              fontWeight: '400',
            },
            'code::before': { content: 'none' },
            'code::after': { content: 'none' },
            pre: {
              backgroundColor: 'rgb(var(--c-code-bg))',
              color: 'rgb(var(--c-code-ink))',
              borderRadius: '10px',
              padding: '1rem 1.15rem',
              fontSize: '0.875rem',
              lineHeight: '1.7',
            },
            'pre code': {
              backgroundColor: 'transparent',
              color: 'inherit',
              padding: '0',
              fontSize: 'inherit',
            },
            blockquote: {
              borderLeftColor: theme('colors.brand.300'),
              color: 'rgb(var(--c-ink-soft))',
              fontStyle: 'normal',
            },
            'ul > li::marker': { color: theme('colors.brand.400') },
            'ol > li::marker': { color: 'rgb(var(--c-ink-faint))' },
            hr: { borderColor: 'rgb(var(--c-border))' },
            img: { borderRadius: '10px' },
            table: { fontSize: '0.9rem' },
            th: { backgroundColor: 'rgb(var(--c-surface-muted))' },
            'th, td': { borderColor: 'rgb(var(--c-border))' },
          },
        },
      }),
    },
  },
  plugins: [typography],
}
