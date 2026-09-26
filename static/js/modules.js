/* =========================================================================
 * modules.js —— 各功能卡片的业务逻辑
 * 每个模块自成闭包，只通过 Modules.xxx 暴露 init/refresh 等方法。
 * ========================================================================= */
(function (global) {
  'use strict';

  var Fmt = UI.Fmt;

  /* =====================================================================
   * 系统信息
   * ===================================================================== */
  var System = (function () {
    var info = null;

    function kv(key, value) {
      return '<div class="kv"><div class="kv-k">' + Fmt.escape(key) + '</div>' +
        '<div class="kv-v">' + Fmt.escape(value) + '</div></div>';
    }

    function render(data) {
      var grid = document.getElementById('sys-grid');
      if (!grid) return;

      var cpuText = data.cpu_model || '未知';
      if (data.cpu_physical || data.cpu_logical) {
        cpuText += '（' + (data.cpu_physical || '?') + ' 核 / ' + (data.cpu_logical || '?') + ' 线程';
        if (data.cpu_freq) cpuText += ' @ ' + data.cpu_freq + ' MHz';
        cpuText += '）';
      }

      var html = [
        kv('主机名', data.hostname),
        kv('当前用户', data.user),
        kv('操作系统', data.platform),
        kv('系统架构', data.architecture),
        kv('CPU', cpuText),
        kv('内存总量', data.mem_total_human),
        kv('交换分区', data.swap_total_human),
        kv('开机时间', data.boot_time_text),
        kv('Python', data.python),
        kv('服务进程 PID', data.pid)
      ].join('');

      // 每个盘符占一格
      (data.disks || []).forEach(function (d) {
        html += kv('磁盘 ' + d.device, d.used_human + ' / ' + d.total_human + '（' + Fmt.pct(d.percent) + '）');
      });

      grid.innerHTML = html;

      var host = document.getElementById('tb-host');
      if (host) host.textContent = data.hostname;

      Charts.renderDisks(data.disks);
    }

    return {
      refresh: function () {
        UI.loading('card-system', true);
        return Api.systemInfo()
          .then(function (res) {
            info = Api.unwrap(res).data;
            render(info);
          })
          .catch(function (err) { UI.fail(err, '读取系统信息失败'); })
          .finally(function () { UI.loading('card-system', false); });
      },
      get: function () { return info; }
    };
  })();

  /* =====================================================================
   * 进程管理
   * ===================================================================== */
  var Process = (function () {
    var items = [];
    var selected = {};      // pid -> true
    var lastFetch = 0;

    function riskBadge(risk) {
      if (risk === 'fatal') return '<span class="risk risk-fatal">系统关键</span>';
      if (risk === 'important') return '<span class="risk risk-important">重要</span>';
      return '';
    }

    function render() {
      var tbody = document.getElementById('proc-tbody');
      if (!tbody) return;

      if (!items.length) {
        tbody.innerHTML = '<tr><td colspan="7" class="empty">没有匹配的进程</td></tr>';
        updateKillButton();
        return;
      }

      var maxMem = items.reduce(function (m, r) { return Math.max(m, r.mem); }, 1) || 1;

      tbody.innerHTML = items.map(function (p) {
        var checked = selected[p.pid] ? ' checked' : '';
        var rowCls = selected[p.pid] ? ' class="selected"' : '';
        var cpuHot = p.cpu >= 40 ? ' hot' : '';
        var memPct = Math.min(100, (p.mem / maxMem) * 100);

        return '<tr' + rowCls + ' data-pid="' + p.pid + '">' +
          '<td class="col-check"><input type="checkbox" class="proc-check" data-pid="' + p.pid + '"' + checked + '></td>' +
          '<td class="mono dim">' + p.pid + '</td>' +
          '<td><div class="name-cell">' + riskBadge(p.risk) +
            '<span class="name-text" title="' + Fmt.escape(p.name) + '">' + Fmt.escape(p.name) + '</span>' +
          '</div></td>' +
          '<td class="col-num"><div class="num">' + p.cpu.toFixed(1) + '%</div>' +
            '<div class="bar' + cpuHot + '"><i style="width:' + Math.min(100, p.cpu) + '%"></i></div></td>' +
          '<td class="col-num"><div class="num">' + Fmt.escape(p.mem_human) + '</div>' +
            '<div class="bar"><i style="width:' + memPct + '%"></i></div></td>' +
          '<td class="col-status dim">' + Fmt.escape(p.status) + '</td>' +
          '<td class="col-user dim" title="' + Fmt.escape(p.username) + '">' +
            Fmt.escape((p.username || '').split('\\').pop() || '—') + '</td>' +
        '</tr>';
      }).join('');

      updateKillButton();
    }

    function updateKillButton() {
      var btn = document.getElementById('btn-kill-selected');
      var count = Object.keys(selected).length;
      if (btn) {
        btn.textContent = '结束选中 (' + count + ')';
        btn.disabled = count === 0;
      }

      var all = document.getElementById('proc-select-all');
      if (all) {
        var visible = items.map(function (p) { return p.pid; });
        all.checked = visible.length > 0 && visible.every(function (pid) { return selected[pid]; });
        all.indeterminate = !all.checked && visible.some(function (pid) { return selected[pid]; });
      }
    }

    function note() {
      var el = document.getElementById('proc-status');
      if (!el) return;
      var ago = lastFetch ? Math.round((Date.now() - lastFetch) / 1000) : null;
      el.textContent = '每 3 秒自动刷新' + (ago !== null ? ' · ' + ago + ' 秒前' : '');
    }

    function params() {
      return {
        search: (document.getElementById('proc-search') || {}).value || '',
        sort: (document.getElementById('proc-sort') || {}).value || 'cpu',
        order: (document.getElementById('proc-order') || {}).value || 'desc'
      };
    }

    function refresh() {
      return Api.processes(params())
        .then(function (res) {
          var data = Api.unwrap(res);
          items = data.items || [];
          lastFetch = Date.now();

          // 清理已经消失的进程的勾选状态
          var alive = {};
          items.forEach(function (p) { alive[p.pid] = true; });
          Object.keys(selected).forEach(function (pid) {
            if (!alive[pid]) delete selected[pid];
          });

          var count = document.getElementById('proc-count');
          if (count) count.textContent = data.total + ' 个进程 · 合计 ' + data.total_mem_human;

          render();
          note();
        })
        .catch(function (err) { UI.fail(err, '读取进程列表失败'); });
    }

    function killSelected() {
      var pids = Object.keys(selected).map(Number);
      if (!pids.length) return Promise.resolve();

      var targets = items.filter(function (p) { return selected[p.pid]; });
      var fatal = targets.filter(function (p) { return p.risk === 'fatal'; });
      var important = targets.filter(function (p) { return p.risk === 'important'; });

      var html = '<div>即将结束 <b>' + pids.length + '</b> 个进程：</div>' +
        '<ul class="mono-list">' +
        targets.slice(0, 12).map(function (p) {
          return '<li>' + Fmt.escape(p.name) + ' (PID ' + p.pid + ')</li>';
        }).join('') +
        (targets.length > 12 ? '<li>…还有 ' + (targets.length - 12) + ' 个</li>' : '') +
        '</ul>';

      if (fatal.length) {
        html += '<p class="danger-text" style="margin-top:10px">⚠ 其中包含 ' + fatal.length +
          ' 个系统关键进程（' + fatal.map(function (p) { return Fmt.escape(p.name); }).join('、') +
          '）。结束它们极可能导致蓝屏、强制重启或当前会话崩溃。</p>';
      } else if (important.length) {
        html += '<p style="margin-top:10px;color:#fde68a">⚠ 其中包含 ' + important.length +
          ' 个重要进程，可能导致桌面或服务异常。</p>';
      }

      return UI.confirm({
        title: '确认结束进程',
        html: html,
        okText: fatal.length ? '强制结束' : '结束进程'
      }).then(function (yes) {
        if (!yes) return;

        return Api.killProcesses(pids, fatal.length > 0).then(function (res) {
          var data = Api.unwrap(res);
          var failed = (data.results || []).filter(function (r) { return !r.ok; });

          if (data.succeeded && !failed.length) {
            UI.ok('已结束', '成功结束 ' + data.succeeded + ' 个进程');
          } else if (data.succeeded) {
            UI.warn('部分成功', '成功 ' + data.succeeded + ' 个，失败 ' + failed.length +
              ' 个。原因：' + failed.slice(0, 3).map(function (r) { return r.name + ' → ' + r.error; }).join('；'));
          } else {
            UI.err('全部失败', failed.slice(0, 3).map(function (r) { return r.name + ' → ' + r.error; }).join('；'));
          }

          Object.keys(selected).forEach(function (pid) {
            var hit = (data.results || []).find(function (r) { return String(r.pid) === String(pid) && r.ok; });
            if (hit) delete selected[pid];
          });

          return refresh();
        });
      }).catch(function (err) { UI.fail(err, '结束进程失败'); });
    }

    function init() {
      var search = document.getElementById('proc-search');
      if (search) {
        var timer = null;
        search.addEventListener('input', function () {
          clearTimeout(timer);
          timer = setTimeout(refresh, 250);   // 输入防抖，别每敲一个字就打一次接口
        });
      }

      ['proc-sort', 'proc-order'].forEach(function (id) {
        var el = document.getElementById(id);
        if (el) el.addEventListener('change', refresh);
      });

      var tbody = document.getElementById('proc-tbody');
      if (tbody) {
        tbody.addEventListener('change', function (e) {
          var box = e.target.closest('.proc-check');
          if (!box) return;
          var pid = Number(box.dataset.pid);
          if (box.checked) selected[pid] = true; else delete selected[pid];
          var row = box.closest('tr');
          if (row) row.classList.toggle('selected', box.checked);
          updateKillButton();
        });
      }

      var all = document.getElementById('proc-select-all');
      if (all) {
        all.addEventListener('change', function () {
          items.forEach(function (p) {
            if (all.checked) selected[p.pid] = true; else delete selected[p.pid];
          });
          render();
        });
      }

      var killBtn = document.getElementById('btn-kill-selected');
      if (killBtn) killBtn.addEventListener('click', killSelected);
    }

    return { init: init, refresh: refresh, clearSelection: function () { selected = {}; render(); } };
  })();

  /* =====================================================================
   * 电源控制
   * ===================================================================== */
  var Power = (function () {
    var TEXTS = {
      shutdown: {
        title: '确认关机',
        html: function (d) {
          return d > 0
            ? '<div>系统将在 <b>' + d + ' 秒</b>后关机。</div><p>期间可以点「取消关机」撤销。</p>'
            : '<p class="danger-text">将立即关机，未保存的工作会丢失。</p>';
        },
        ok: '关机'
      },
      reboot: {
        title: '确认重启',
        html: function (d) {
          return d > 0
            ? '<div>系统将在 <b>' + d + ' 秒</b>后重启。</div><p>期间可以点「取消关机」撤销。</p>'
            : '<p class="danger-text">将立即重启，未保存的工作会丢失。</p>';
        },
        ok: '重启'
      },
      cancel: {
        title: '取消关机 / 重启?',
        html: function () { return '<div>将撤销已下达但尚未执行的关机或重启命令。</div>'; },
        ok: '取消关机'
      },
      lock: {
        title: '确认锁屏?',
        html: function () { return '<div>锁屏后需要重新输入密码才能回到桌面。</div>'; },
        ok: '锁屏'
      },
      hibernate: {
        title: '确认休眠?',
        html: function () { return '<div>系统将把内存写入磁盘后断电。若未启用休眠功能，系统会返回错误提示。</div>'; },
        ok: '休眠'
      },
      sleep: {
        title: '确认睡眠?',
        html: function () { return '<div>系统将进入低功耗状态。注意：部分机器会因电源设置直接转为休眠。</div>'; },
        ok: '睡眠'
      }
    };

    function delay() {
      var el = document.getElementById('power-delay');
      var v = parseInt((el && el.value) || '0', 10);
      return isNaN(v) || v < 0 ? 0 : v;
    }

    function doAction(action) {
      var text = TEXTS[action];
      if (!text) return;

      var d = delay();
      return UI.confirm({
        title: text.title,
        html: text.html(d),
        okText: text.ok
      }).then(function (yes) {
        if (!yes) return;

        return Api.power(action, d).then(function (res) {
          var data = Api.unwrap(res);
          UI.ok('指令已下达', data.message || '已执行');
          refresh();
        }).catch(function (err) {
          // 后端把系统原始错误放在 error 里，这里直接透出，便于排查（如未启用休眠）
          UI.fail(err, '电源操作失败');
        });
      });
    }

    function refresh() {
      return Api.powerStatus().then(function (res) {
        var data = Api.unwrap(res);
        var el = document.getElementById('power-pending');
        if (!el) return;
        if (data.pending && data.pending.action && data.remaining !== null && data.remaining > 0) {
          el.textContent = '待执行：' + data.pending.action + '（剩 ' + data.remaining + ' 秒）';
          el.style.color = '#fbbf24';
        } else {
          el.textContent = '';
        }
      }).catch(function () { /* 状态查询失败不打扰用户 */ });
    }

    function init() {
      document.querySelectorAll('[data-power]').forEach(function (btn) {
        btn.addEventListener('click', function () { doAction(btn.dataset.power); });
      });
    }

    return { init: init, refresh: refresh };
  })();

  /* =====================================================================
   * 屏幕监控
   * ===================================================================== */
  var Screen = (function () {
    var running = false;
    var intervalMs = 1000;
    var timer = null;
    var lastAt = 0;

    function setCover(show, text) {
      var cover = document.getElementById('screen-cover');
      if (!cover) return;
      cover.classList.toggle('hidden', !show);
      if (text) cover.textContent = text;
    }

    function meta() {
      var el = document.getElementById('screen-meta');
      if (!el) return;
      if (!running) { el.textContent = '已暂停'; return; }
      var fps = (1000 / intervalMs);
      el.textContent = '实时 · ' + (fps >= 1 ? fps.toFixed(fps % 1 ? 1 : 0) + ' 帧/秒' : '每 ' + (intervalMs / 1000) + ' 秒 1 帧') +
        (lastAt ? ' · ' + Fmt.clock(lastAt / 1000) : '');
    }

    function schedule() {
      if (!running) return;
      timer = setTimeout(loadFrame, intervalMs);
    }

    /** 串行加载：等上一帧加载完再请求下一帧，避免请求堆积 */
    function loadFrame() {
      if (!running) return;
      var img = document.getElementById('screen-img');
      var pre = new Image();

      pre.onload = function () {
        if (img) img.src = pre.src;
        lastAt = Date.now();
        meta();
        schedule();
      };
      pre.onerror = function () { schedule(); };
      pre.src = Api.screenFrameUrl();
    }

    function start() {
      if (running) return;
      running = true;
      setCover(false);
      var btn = document.getElementById('btn-screen-toggle');
      if (btn) btn.textContent = '暂停';
      loadFrame();
      meta();
    }

    function stop() {
      running = false;
      if (timer) { clearTimeout(timer); timer = null; }
      setCover(true, '已暂停');
      var btn = document.getElementById('btn-screen-toggle');
      if (btn) btn.textContent = '开始';
      meta();
    }

    function snapshot() {
      // 用隐藏的 <a download> 触发下载；图片地址带令牌，浏览器直接发请求即可
      var a = document.createElement('a');
      a.href = Api.screenSnapshotUrl();
      a.download = '';
      document.body.appendChild(a);
      a.click();
      a.remove();
      UI.info('已请求截图', '浏览器将按下载设置保存这张画面');
    }

    function fullscreen() {
      var frame = document.getElementById('screen-frame');
      if (!frame) return;
      if (document.fullscreenElement) {
        document.exitFullscreen();
      } else if (frame.requestFullscreen) {
        frame.requestFullscreen();
      } else if (frame.webkitRequestFullscreen) {
        frame.webkitRequestFullscreen();
      }
    }

    function init() {
      var btn = document.getElementById('btn-screen-toggle');
      if (btn) btn.addEventListener('click', function () { running ? stop() : start(); });

      var fps = document.getElementById('screen-fps');
      if (fps) {
        fps.addEventListener('change', function () {
          intervalMs = parseInt(fps.value, 10) || 1000;
          meta();
        });
      }

      var shot = document.getElementById('btn-screen-shot');
      if (shot) shot.addEventListener('click', snapshot);

      var full = document.getElementById('btn-screen-full');
      if (full) full.addEventListener('click', fullscreen);

      Api.screenInfo().then(function (res) {
        var data = Api.unwrap(res);
        var el = document.getElementById('screen-meta');
        if (el) el.textContent = data.width + '×' + data.height + ' · 已暂停';
      }).catch(function () { /* 屏幕信息拿不到就先不管 */ });
    }

    return { init: init, start: start, stop: stop, isRunning: function () { return running; } };
  })();

  /* =====================================================================
   * 文件浏览器
   * ===================================================================== */
  var Files = (function () {
    var current = '';
    var parent = null;
    var drivesLoaded = false;

    function loadDrives() {
      if (drivesLoaded) return Promise.resolve();
      return Api.drives().then(function (res) {
        var data = Api.unwrap(res);
        var sel = document.getElementById('file-drives');
        if (sel) {
          sel.innerHTML = '<option value="">选择盘符…</option>' + (data.items || []).map(function (d) {
            return '<option value="' + Fmt.escape(d.path) + '">' + Fmt.escape(d.path) +
              (d.total_human ? '  ' + d.free_human + ' 可用' : '') + '</option>';
          }).join('');
        }
        drivesLoaded = true;

        if (!current && data.items && data.items.length) load(data.items[0].path);
      }).catch(function (err) { UI.fail(err, '读取盘符失败'); });
    }

    function load(path) {
      UI.loading('card-files', true);
      return Api.listDir(path).then(function (res) {
        var data = Api.unwrap(res);
        current = data.path;
        parent = data.parent;

        var input = document.getElementById('file-path');
        if (input) input.value = data.path;

        var count = document.getElementById('file-count');
        if (count) {
          count.textContent = data.count + ' 项' + (data.truncated ? '（已截断）' : '');
        }

        var tbody = document.getElementById('file-tbody');
        if (!tbody) return;

        if (!data.items.length) {
          tbody.innerHTML = '<tr><td colspan="4" class="empty">这个目录是空的</td></tr>';
          return;
        }

        tbody.innerHTML = data.items.map(function (f) {
          var icon = f.is_dir ? '📁' : '📄';
          var nameCell = f.is_dir
            ? '<a href="#" class="file-enter" data-path="' + Fmt.escape(f.path) + '">' + icon + ' ' + Fmt.escape(f.name) + '</a>'
            : '<span>' + icon + ' ' + Fmt.escape(f.name) + '</span>';
          var action = f.is_dir
            ? '<button class="btn btn-mini file-enter" data-path="' + Fmt.escape(f.path) + '">打开</button>'
            : '<a class="btn btn-mini" href="' + Api.downloadUrl(f.path) + '">下载</a>';

          return '<tr>' +
            '<td>' + nameCell + '</td>' +
            '<td class="col-num num">' + Fmt.escape(f.size_human) + '</td>' +
            '<td class="col-time dim">' + Fmt.escape(f.mtime_text) + '</td>' +
            '<td class="col-act">' + action + '</td>' +
          '</tr>';
        }).join('');
      }).catch(function (err) {
        UI.fail(err, '读取目录失败');
        var tbody = document.getElementById('file-tbody');
        if (tbody) tbody.innerHTML = '<tr><td colspan="4" class="empty">' + Fmt.escape(UI.errorText(err)) + '</td></tr>';
      }).finally(function () { UI.loading('card-files', false); });
    }

    function up() { if (parent) load(parent); else UI.info('已经是顶层', '没有更上一级目录了'); }

    function init() {
      var tbody = document.getElementById('file-tbody');
      if (tbody) {
        tbody.addEventListener('click', function (e) {
          var el = e.target.closest('.file-enter');
          if (!el) return;
          e.preventDefault();
          load(el.dataset.path);
        });
      }

      var pathInput = document.getElementById('file-path');
      if (pathInput) {
        pathInput.addEventListener('keydown', function (e) {
          if (e.key === 'Enter') load(pathInput.value.trim());
        });
      }

      var go = document.getElementById('btn-file-go');
      if (go) go.addEventListener('click', function () { load(pathInput.value.trim()); });

      var upBtn = document.getElementById('btn-file-up');
      if (upBtn) upBtn.addEventListener('click', up);

      var refreshBtn = document.getElementById('btn-file-refresh');
      if (refreshBtn) refreshBtn.addEventListener('click', function () { load(current); });

      var sel = document.getElementById('file-drives');
      if (sel) sel.addEventListener('change', function () { if (sel.value) load(sel.value); });

      loadDrives();
    }

    return { init: init, load: load };
  })();

  /* =====================================================================
   * 剪贴板
   * ===================================================================== */
  var Clipboard = (function () {
    function refresh() {
      return Api.clipboard().then(function (res) {
        var data = Api.unwrap(res);
        var area = document.getElementById('clip-text');
        if (area) area.value = data.text || '';
        var meta = document.getElementById('clip-meta');
        if (meta) {
          meta.textContent = data.text
            ? data.length + ' 字符 · ' + (data.lines || 0) + ' 行' + (data.truncated ? ' · 已截断' : '')
            : (data.note || '剪贴板为空');
        }
      }).catch(function (err) { UI.fail(err, '读取剪贴板失败'); });
    }
    return { refresh: refresh };
  })();

  /* =====================================================================
   * 启动项
   * ===================================================================== */
  var Startup = (function () {
    function refresh() {
      return Api.startup().then(function (res) {
        var data = Api.unwrap(res);
        var count = document.getElementById('startup-count');
        if (count) count.textContent = data.count + ' 项';

        var tbody = document.getElementById('startup-tbody');
        if (!tbody) return;

        if (!data.items.length) {
          tbody.innerHTML = '<tr><td colspan="4" class="empty">没有找到启动项</td></tr>';
          return;
        }

        tbody.innerHTML = data.items.map(function (s) {
          return '<tr>' +
            '<td>' + Fmt.escape(s.name) + '</td>' +
            '<td class="dim">' + Fmt.escape(s.source) + '</td>' +
            '<td class="dim" title="' + Fmt.escape(s.location) + '">' + Fmt.escape(s.location) + '</td>' +
            '<td class="mono dim" title="' + Fmt.escape(s.command) + '">' + Fmt.escape(s.command) + '</td>' +
          '</tr>';
        }).join('');
      }).catch(function (err) { UI.fail(err, '读取启动项失败'); });
    }
    return { refresh: refresh };
  })();

  /* =====================================================================
   * 命令执行
   * ===================================================================== */
  var Exec = (function () {
    var history = [];

    function print(text, cls) {
      var out = document.getElementById('exec-output');
      if (!out) return;
      out.innerHTML = cls ? '<span class="' + cls + '">' + Fmt.escape(text) + '</span>' : Fmt.escape(text);
      out.scrollTop = 0;
    }

    function run(command) {
      var input = document.getElementById('exec-input');
      var cmd = (command !== undefined ? command : (input && input.value) || '').trim();
      if (!cmd) { UI.warn('命令为空', '请先输入要执行的命令'); return Promise.resolve(); }

      if (input) input.value = cmd;
      print('> ' + cmd + '\n\n执行中…');

      var started = Date.now();
      return Api.exec(cmd).then(function (res) {
        var data = Api.unwrap(res);
        var text = '';
        if (data.stdout) text += data.stdout;
        if (data.stderr) text += (text ? '\n' : '') + data.stderr;

        print(text || '（没有输出）', data.code === 0 ? null : 'err');

        var meta = document.getElementById('exec-meta');
        if (meta) {
          meta.textContent = '返回码 ' + data.code + ' · 耗时 ' + data.duration + ' 秒 · 模式 ' +
            (data.mode === 'arbitrary' ? '任意命令' : '白名单') + (data.truncated ? ' · 输出已截断' : '');
        }
        history.unshift(cmd);
        history = history.slice(0, 30);
      }).catch(function (err) {
        var detail = UI.errorText(err);
        if (err && err.payload && err.payload.hint) detail += '\n提示：' + err.payload.hint;
        print('✗ ' + detail, 'err');
        UI.fail(err, '命令执行失败');
      });
    }

    function init() {
      var btn = document.getElementById('btn-exec-run');
      if (btn) btn.addEventListener('click', function () { run(); });

      var input = document.getElementById('exec-input');
      if (input) {
        input.addEventListener('keydown', function (e) {
          if (e.key === 'Enter') run();
          // 上箭头翻历史命令
          if (e.key === 'ArrowUp' && history.length) {
            e.preventDefault();
            input.value = history[0];
          }
        });
      }

      return Api.execPresets().then(function (res) {
        var data = Api.unwrap(res);
        var mode = document.getElementById('exec-mode');
        if (mode) {
          mode.textContent = data.allow_arbitrary
            ? '⚠ 任意命令模式（config.json 已放开）'
            : '白名单模式 · 超时 ' + data.timeout + ' 秒';
          mode.style.color = data.allow_arbitrary ? '#f87171' : '';
        }

        var box = document.getElementById('exec-presets');
        if (box) {
          var quick = ['ipconfig', 'ipconfig /all', 'ping 127.0.0.1', 'netstat -ano', 'tasklist', 'systeminfo', 'whoami /all'];
          box.innerHTML = quick.map(function (c) {
            return '<span class="preset" data-cmd="' + Fmt.escape(c) + '">' + Fmt.escape(c) + '</span>';
          }).join('');
          box.addEventListener('click', function (e) {
            var el = e.target.closest('.preset');
            if (el) run(el.dataset.cmd);
          });
        }
      }).catch(function () { /* 白名单拿不到不影响执行 */ });
    }

    return { init: init, run: run };
  })();

  global.Modules = {
    system: System,
    process: Process,
    power: Power,
    screen: Screen,
    files: Files,
    clipboard: Clipboard,
    startup: Startup,
    exec: Exec
  };
})(window);
