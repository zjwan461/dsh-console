<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts'

const props = withDefaults(
  defineProps<{
    x: string[]
    series: Array<{ name: string; data: number[]; color?: string; area?: boolean }>
    height?: string
  }>(),
  { height: '280px' },
)

const el = ref<HTMLDivElement | null>(null)
let chart: echarts.ECharts | null = null

function render() {
  if (!el.value) return
  if (!chart) chart = echarts.init(el.value)
  chart.setOption(
    {
      grid: { left: 44, right: 18, top: 28, bottom: 40 },
      tooltip: { trigger: 'axis' },
      legend: { top: 0, icon: 'roundRect', itemWidth: 10, itemHeight: 10 },
      xAxis: {
        type: 'category',
        data: props.x,
        boundaryGap: false,
        axisLine: { lineStyle: { color: '#cbd5e1' } },
        axisLabel: { color: '#64748b', fontSize: 11 },
      },
      yAxis: {
        type: 'value',
        splitLine: { lineStyle: { color: '#eef2f7' } },
        axisLabel: { color: '#64748b', fontSize: 11 },
      },
      series: props.series.map((item) => ({
        name: item.name,
        type: 'line',
        smooth: true,
        showSymbol: false,
        data: item.data,
        lineStyle: { width: 2, color: item.color },
        itemStyle: { color: item.color },
        areaStyle: item.area
          ? {
              opacity: 0.18,
              color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                { offset: 0, color: item.color ?? '#4d6bfe' },
                { offset: 1, color: 'rgba(77,107,254,0)' },
              ]),
            }
          : undefined,
      })),
    },
    true,
  )
}

function resize() {
  chart?.resize()
}

onMounted(() => {
  render()
  window.addEventListener('resize', resize)
})
onBeforeUnmount(() => {
  window.removeEventListener('resize', resize)
  chart?.dispose()
  chart = null
})
watch(() => [props.x, props.series], render, { deep: true })
</script>

<template>
  <div ref="el" :style="{ width: '100%', height }" />
</template>
