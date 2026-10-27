// 验收报告图表：混淆矩阵热力图 + 分类别统计柱状图
(function() {
  var style = getComputedStyle(document.documentElement);
  var accent = style.getPropertyValue('--accent').trim();
  var accent2 = style.getPropertyValue('--accent2').trim();
  var ink = style.getPropertyValue('--ink').trim();
  var muted = style.getPropertyValue('--muted').trim();
  var rule = style.getPropertyValue('--rule').trim();
  var bg2 = style.getPropertyValue('--bg2').trim();

  var cats = ['高风险', '中风险', '低风险', '无风险', '非楼道'];

  // --- 图 1：混淆矩阵热力图（行=期望，列=预测）---
  // 行序: 高风险/中风险/低风险/无风险/非楼道
  var matrix = [
    [5, 0, 0, 0, 0],
    [0, 2, 0, 0, 0],
    [1, 0, 0, 0, 0],
    [0, 0, 0, 2, 0],
    [0, 0, 0, 0, 5]
  ];
  var heatData = [];
  for (var y = 0; y < matrix.length; y++) {
    for (var x = 0; x < matrix[y].length; x++) {
      heatData.push([x, y, matrix[y][x]]);
    }
  }

  var chart1 = echarts.init(document.getElementById('chart-confusion'), null, { renderer: 'svg' });
  chart1.setOption({
    animation: false,
    tooltip: {
      appendToBody: true,
      formatter: function(p) {
        return '期望：' + cats[p.value[1]] + '<br/>预测：' + cats[p.value[0]] + '<br/>数量：' + p.value[2];
      }
    },
    grid: { top: 40, left: 80, right: 30, bottom: 90 },
    xAxis: {
      type: 'category', data: cats, name: '预测类别', nameLocation: 'middle', nameGap: 32,
      axisLabel: { color: ink }, axisLine: { lineStyle: { color: rule } }, splitArea: { show: false }
    },
    yAxis: {
      type: 'category', data: cats, name: '期望类别', nameLocation: 'middle', nameGap: 55,
      axisLabel: { color: ink }, axisLine: { lineStyle: { color: rule } }, splitArea: { show: false }
    },
    visualMap: {
      min: 0, max: 5, calculable: true, orient: 'horizontal', left: 'center', bottom: 6,
      inRange: { color: [bg2, accent2] },
      textStyle: { color: muted }
    },
    series: [{
      type: 'heatmap',
      data: heatData,
      label: {
        show: true,
        color: ink,
        formatter: function(p) { return p.value[2]; }
      },
      itemStyle: { borderColor: rule, borderWidth: 1 },
      emphasis: { itemStyle: { shadowBlur: 6, shadowColor: 'rgba(0,0,0,0.2)' } }
    }]
  });
  window.addEventListener('resize', function() { chart1.resize(); });

  // --- 图 2：分类别样本数 vs 正确数 ---
  var chart2 = echarts.init(document.getElementById('chart-category'), null, { renderer: 'svg' });
  chart2.setOption({
    animation: false,
    tooltip: { appendToBody: true, trigger: 'axis' },
    legend: { top: 10, textStyle: { color: ink } },
    grid: { top: 60, left: 60, right: 30, bottom: 40 },
    xAxis: {
      type: 'category', data: cats,
      axisLabel: { color: ink }, axisLine: { lineStyle: { color: rule } }
    },
    yAxis: {
      type: 'value', minInterval: 1,
      axisLabel: { color: muted }, splitLine: { lineStyle: { color: rule } }
    },
    series: [
      {
        name: '样本数', type: 'bar', barGap: '10%',
        data: [5, 2, 1, 2, 5],
        itemStyle: { color: muted, opacity: 0.45, borderRadius: [3, 3, 0, 0] },
        label: { show: true, position: 'top', color: muted }
      },
      {
        name: '正确数', type: 'bar',
        data: [5, 2, 0, 2, 5],
        itemStyle: { color: accent2, borderRadius: [3, 3, 0, 0] },
        label: { show: true, position: 'top', color: accent2, fontWeight: 'bold' }
      }
    ]
  });
  window.addEventListener('resize', function() { chart2.resize(); });
})();
