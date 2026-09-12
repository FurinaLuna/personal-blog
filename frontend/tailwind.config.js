import typography from '@tailwindcss/typography'

/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{vue,js,ts,jsx,tsx}'],
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
        brand: {
          50: '#eef4ff',
          100: '#d9e6ff',
          200: '#bcd3ff',
          300: '#8eb6ff',
          400: '#598eff',
          500: '#3366f0',
          600: '#2449d8',
          700: '#1d3aae',
          800: '#1c338a',
          900: '#1c2f6e',
        },
      },
      // 字体栈在 src/styles/tokens.css 里定义（单一来源），这里只做引用，
      // 避免「同一个字体栈写在两个文件里」——那种重复迟早只改一处
      fontFamily: {
        sans: ['var(--font-sans)'],
        mono: ['var(--font-mono)'],
      },
      maxWidth: {
        content: '760px',
        shell: '1120px',
      },
      typography: (theme) => ({
        DEFAULT: {
          css: {
            maxWidth: 'none',
            color: 'rgb(var(--c-ink))',
            // 中文正文 1.85 偏松，1.75 在「不局促」和「读得下去」之间更合适
            lineHeight: '1.75',
            a: {
              color: theme('colors.brand.600'),
              textDecoration: 'none',
              borderBottom: `1px solid ${theme('colors.brand.200')}`,
              '&:hover': { color: theme('colors.brand.700') },
            },
            'h2, h3, h4': { color: 'rgb(var(--c-ink))', fontWeight: '600' },
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
