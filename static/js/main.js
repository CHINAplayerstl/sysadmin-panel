/* =========================================================================
 * main.js —— 启动编排
 * 职责：登录流程、各模块初始化、轮询调度（页面隐藏时自动停）、顶部状态栏。
 * ========================================================================= */
(function (global) {
  'use strict';

  var Fmt = UI.Fmt;

  // 轮询周期（毫秒）
  var TICK = {
    metrics: 1000,     // 监控图表 / 顶部状态栏
    process: 3000,     // 进程列表（需求要求 3 秒）
    power: 5000        // 电源待执行状态
  };

  var timers = {};
  var live = true;          // 是否允许自动刷新（用户可暂停）
  var booted = false;

  /* ---------------------------------------------------------- 登录 */
  function showLogin(message) {
    var mask = document.getElementById('login-mask');
    var err = document.getElementById('login-error');
    if (mask) mask.classList.remove('hidden');
    if (err) err.textContent = message || '';
    var input = document.getElementById('login-token');
    if (input) { input.value = ''; input.focus(); }
    stopPolling();
  }

  function hideLogin() {
    var mask = document.getElementById('login-mask');
    if (mask) mask.classList.add('hidden');
  }

  function tryLogin(token) {
    Api.saveToken(token);
    return Api.ping().then(function () {
      hideLogin();
      startApp();
    }).catch(function (err) {
      Api.clearToken();
      showLogin(err && err.needToken ? '令牌不正确，请重新输入' : ('连接失败：' + UI.errorText(err)));
      throw err;
    });
  }

  /* ---------------------------------------------------------- 顶部状态栏 */
  function setConn(ok, text) {
    var el = document.getElementById('tb-conn');
    var t = document.getElementById('tb-conn-text');
    if (el) el.className = 'conn ' + (ok ? 'ok' : 'bad');
    if (t) t.textContent = text;
  }

  /* ---------------------------------------------------------- 轮询调度 */
  function every(name, ms, fn, immediate) {
    if (timers[name]) clearInterval(timers[name]);
    timers[name] = setInterval(function () {
      if (!live || document.hidden) return;
      fn();
    }, ms);
    if (immediate !== false) fn();
  }

  function stopPolling() {
    Object.keys(timers).forEach(function (k) {
      clearInterval(timers[k]);
      delete timers[k];
    });
  }

  function startPolling() {
    // 监控：每秒一次，顺便更新顶部状态栏
    every('metrics', TICK.metrics, function () {
      Api.metrics().then(function (res) {
        var data = Api.unwrap(res);
        var m = data.latest || {};

        Charts.update(data);

        var cpu = document.getElementById('tb-cpu');
        var mem = document.getElementById('tb-mem');
        var uptime = document.getElementById('tb-uptime');
        if (cpu) cpu.textContent = Fmt.pct(m.cpu);
        if (mem) mem.textContent = Fmt.pct(m.mem) + '（' + Fmt.bytes(m.mem_used) + '）';
        if (uptime) uptime.textContent = Fmt.duration(m.uptime);

        setConn(true, '已连接');
      }).catch(function (err) {
        setConn(false, err && err.needToken ? '令牌失效' : '连接中断');
        if (err && err.needToken) showLogin('令牌已失效，请重新输入');
      });
    });

    // 进程列表
    every('process', TICK.process, function () {
      if (!Modules.screen.isRunning()) { /* 屏幕关闭时省一点开销，无实际动作 */ }
      Modules.process.refresh();
    });

    // 电源状态
    every('power', TICK.power, function () { Modules.power.refresh(); });
  }

  /* ---------------------------------------------------------- 应用启动 */
  function startApp() {
    if (booted) { startPolling(); return; }
    booted = true;

    Charts.init();

    Modules.system.refresh();
    Modules.process.init();
    Modules.power.init();
    Modules.screen.init();
    Modules.files.init();
    Modules.exec.init();
    Modules.startup.refresh();
    Modules.clipboard.refresh();

    startPolling();
    setConn(true, '已连接');
  }

  /* ---------------------------------------------------------- 事件绑定 */
  function bindUI() {
    // 登录
    var loginBtn = document.getElementById('login-btn');
    var loginInput = document.getElementById('login-token');
    if (loginBtn) {
      loginBtn.addEventListener('click', function () {
        var token = (loginInput && loginInput.value || '').trim();
        if (!token) { document.getElementById('login-error').textContent = '请输入访问令牌'; return; }
        tryLogin(token).catch(function () { /* 错误已在 tryLogin 里提示 */ });
      });
    }
    if (loginInput) {
      loginInput.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') loginBtn.click();
      });
    }

    // 暂停 / 恢复自动刷新
    var liveBtn = document.getElementById('btn-live');
    if (liveBtn) {
      liveBtn.addEventListener('click', function () {
        live = !live;
        liveBtn.textContent = live ? '暂停刷新' : '继续刷新';
        UI.info(live ? '已恢复自动刷新' : '已暂停自动刷新', live ? '' : '表格数据将保持不动，可点各卡片的刷新按钮手动更新');
        if (live) { Modules.process.refresh(); Modules.power.refresh(); }
      });
    }

    // 退出（清掉本地令牌）
    var logout = document.getElementById('btn-logout');
    if (logout) {
      logout.addEventListener('click', function () {
        UI.confirm({
          title: '退出登录？',
          html: '<div>将清除本机浏览器里保存的访问令牌。服务端不受影响，下次需要重新输入令牌。</div>',
          okText: '退出'
        }).then(function (yes) {
          if (!yes) return;
          Api.clearToken();
          location.reload();
        });
      });
    }

    // 各卡片的手动刷新按钮
    document.querySelectorAll('[data-refresh]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var which = btn.dataset.refresh;
        if (Modules[which] && Modules[which].refresh) Modules[which].refresh();
      });
    });

    // 页面切到后台时暂停轮询，回到前台立刻补一次
    document.addEventListener('visibilitychange', function () {
      if (!document.hidden && live) {
        Modules.process.refresh();
        Modules.power.refresh();
      }
    });
  }

  /* ---------------------------------------------------------- 引导 */
  function boot() {
    bindUI();

    var token = Api.loadToken();
    if (!token) { showLogin(''); return; }

    Api.ping().then(function () {
      hideLogin();
      startApp();
    }).catch(function (err) {
      if (err && err.needToken) {
        Api.clearToken();
        showLogin('令牌已失效，请重新输入');
      } else {
        showLogin('无法连接服务：' + UI.errorText(err));
      }
    });
  }

  global.App = { boot: boot, showLogin: showLogin, startApp: startApp };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})(window);
