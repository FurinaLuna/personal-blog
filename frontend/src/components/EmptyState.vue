<script setup lang="ts">
/** 空状态。所有列表页共用，保证「没数据」时也有明确出口，而不是一片空白。 */
withDefaults(
  defineProps<{
    title?: string
    description?: string
    actionLabel?: string
  }>(),
  {
    title: '这里还没有内容',
    description: '',
    actionLabel: '',
  },
)

defineEmits<{ action: [] }>()
</script>

<template>
  <div class="flex flex-col items-center justify-center rounded-xl border border-dashed border-border px-6 py-16 text-center">
    <div class="mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-surface-muted text-ink-faint">
      <svg
        class="h-6 w-6"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        stroke-width="1.6"
        stroke-linecap="round"
      >
        <path d="M4 6h16v12H4z" />
        <path d="M4 9h16M8 13h6" />
      </svg>
    </div>
    <p class="text-base font-medium text-ink">{{ title }}</p>
    <p v-if="description" class="mt-1.5 max-w-sm text-sm text-ink-soft">{{ description }}</p>
    <button v-if="actionLabel" type="button" class="btn--primary mt-5" @click="$emit('action')">
      {{ actionLabel }}
    </button>
    <slot />
  </div>
</template>
