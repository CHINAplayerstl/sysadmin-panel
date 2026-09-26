/* =========================================================================
 * charts.js —— ECharts 封装
 * 职责：CPU/内存双线图、网络上下行双线图、磁盘占用条。
 * 说明：数据来自后端后台采样线程，前端只负责把历史序列画出来，不做计算。
 * ========================================================================= */
(function (global) {
  'use strict';

  var hasEcharts = (typeof global.echarts !== 'undefined');
  var cpuChart = null;
  var netChart = null;

  /* ECharts 通用的深色样式 */
  function baseOption(yName, yFormatter) {
    return {
      backgroundColor: 'transparent',
      animation: false,
      grid: { left: 52, right: 18, top: 34, bottom: 26 },
      tooltip: {
        trigger: 'axis',
        backgroundColor: 'rgba(15,23,41,.95)',
        borderColor: '#1e2a44',
        textStyle: { color: '#e6edf7', fontSize: 12 },
        valueFormatter: yFormatter
      },
      legend: {
        top: 2,
        right: 6,
        textStyle: { color: '#8ea0bf', fontSize: 11 },
        itemWidth: 14,
        itemHeight: 8
      },
      xAxis: {
        type: 'category',
        boundaryGap: false,
        data: [],
        axisLine: { lineStyle: { color: '#1e2a44' } },
        axisLabel: { color: '#64748b', fontSize: 10, hideOverlap: true },
        axisTick: { show: false }
      },
      yAxis: {
        type: 'value',
        name: yName,
        nameTextStyle: { color: '#64748b', fontSize: 10 },
        min: 0,
        axisLine: { show: false },
        axisLabel: { color: '#64748b', fontSize: 10, formatter: yFormatter },
        splitLine: { lineStyle: { color: 'rgba(30,42,68,.6)' } }
      }
    };
  }

  function series(name, color, data, area) {
    return {
      name: name,
      type: 'line',
      smooth: true,
      symbol: 'none',
      sampling: 'lttb',
      lineStyle: { width: 2, color: color },
      itemStyle: { color: color },
      areaStyle: area ? {
        color: new global.echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: color.replace('rgb', 'rgba').replace(')', ',.28)') },
          { offset: 1, color: color.replace('rgb', 'rgba').replace(')', ',.02)') }
        ])
      } : undefined,
      data: data
    };
  }

  var COLOR_CPU = 'rgb(56,189,248)';
  var COLOR_MEM = 'rgb(167,139,250)';
  var COLOR_UP  = 'rgb(52,211,153)';
  var COLOR_DOWN= 'rgb(251,191,36)';

  var Charts = {
    available: hasEcharts,

    init: function () {
      if (!hasEcharts) {
        ['chart-cpu', 'chart-net'].forEach(function (id) {
          var el = document.getElementById(id);
          if (el) {
            el.innerHTML = '<div class="empty">图表库未加载（static/vendor/echarts.min.js 缺失且无外网），监控数据仍以数字形式显示。</div>';
          }
        });
        return;
      }

      var cpuEl = document.getElementById('chart-cpu');
      var netEl = document.getElementById('chart-net');
      if (cpuEl) cpuChart = global.echarts.init(cpuEl);
      if (netEl) netChart = global.echarts.init(netEl);

      var onResize = function () {
        if (cpuChart) cpuChart.resize();
        if (netChart) netChart.resize();
      };
      global.addEventListener('resize', onResize);
    },

    /** 用后端返回的 metrics 刷新两张图 */
    update: function (metrics) {
      if (!hasEcharts || !metrics || !metrics.history) return;

      var h = metrics.history;
      var labels = (h.t || []).map(function (ts) { return UI.Fmt.clock(ts); });

      if (cpuChart) {
        var opt = baseOption('%', function (v) { return v + '%'; });
        opt.xAxis.data = labels;
        opt.yAxis.max = 100;
        opt.series = [
          series('CPU', COLOR_CPU, h.cpu || [], true),
          series('内存', COLOR_MEM, h.mem || [], true)
        ];
        cpuChart.setOption(opt, { notMerge: true });
      }

      if (netChart) {
        var optNet = baseOption('速率', function (v) { return UI.Fmt.bytes(v) + '/s'; });
        optNet.xAxis.data = labels;
        optNet.series = [
          series('上行', COLOR_UP, h.net_up || [], true),
          series('下行', COLOR_DOWN, h.net_down || [], true)
        ];
        netChart.setOption(optNet, { notMerge: true });
      }
    },

    /** 顶部状态栏里的磁盘条 */
    renderDisks: function (disks) {
      var row = document.getElementById('disk-row');
      if (!row || !disks) return;

      row.innerHTML = disks.map(function (d) {
        var hot = d.percent >= 88 ? ' hot' : '';
        return '' +
          '<div class="disk-item">' +
            '<div class="disk-top">' +
              '<span class="disk-name">' + UI.Fmt.escape(d.device) + '</span>' +
              '<span class="disk-pct">' + UI.Fmt.pct(d.percent) + '</span>' +
            '</div>' +
            '<div class="disk-bar' + hot + '"><i style="width:' + Math.min(100, d.percent) + '%"></i></div>' +
            '<div class="disk-top" style="margin-top:5px">' +
              '<span class="dim">已用 ' + UI.Fmt.escape(d.used_human) + '</span>' +
              '<span class="dim">可用 ' + UI.Fmt.escape(d.free_human) + '</span>' +
            '</div>' +
          '</div>';
      }).join('');
    }
  };

  global.Charts = Charts;
})(window);
