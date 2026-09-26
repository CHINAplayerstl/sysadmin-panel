/* =========================================================================
 * ui.js —— 通用交互组件
 * 职责：toast 提示、确认弹窗（Promise 化）、卡片加载态、格式化工具。
 * ========================================================================= */
(function (global) {
  'use strict';

  /* ---------------------------------------------------------- 格式化工具 */
  var Fmt = {
    bytes: function (n) {
      if (n === null || n === undefined || isNaN(n)) return '—';
      var units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
      var v = Number(n), i = 0;
      while (Math.abs(v) >= 1024 && i < units.length - 1) { v /= 1024; i++; }
      return (i === 0 ? v.toFixed(0) : v.toFixed(1)) + ' ' + units[i];
    },
    rate: function (n) { return Fmt.bytes(n) + '/s'; },
    pct: function (n) { return (n === null || n === undefined) ? '—' : Number(n).toFixed(1) + '%'; },

    /** 秒 -> "3 天 04:15:22" */
    duration: function (seconds) {
      var s = Math.max(0, Math.floor(Number(seconds) || 0));
      var d = Math.floor(s / 86400); s -= d * 86400;
      var h = Math.floor(s / 3600);  s -= h * 3600;
      var m = Math.floor(s / 60);    s -= m * 60;
      var pad = function (x) { return x < 10 ? '0' + x : '' + x; };
      return (d > 0 ? d + ' 天 ' : '') + pad(h) + ':' + pad(m) + ':' + pad(s);
    },

    /** 时间戳 -> HH:MM:SS */
    clock: function (epochSeconds) {
      var d = new Date(Number(epochSeconds) * 1000);
      var pad = function (x) { return x < 10 ? '0' + x : '' + x; };
      return pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
    },

    /** 时间戳 -> YYYY-MM-DD HH:MM */
    datetime: function (epochSeconds) {
      if (!epochSeconds) return '—';
      var d = new Date(Number(epochSeconds) * 1000);
      var pad = function (x) { return x < 10 ? '0' + x : '' + x; };
      return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()) +
        ' ' + pad(d.getHours()) + ':' + pad(d.getMinutes());
    },

    escape: function (text) {
      return String(text === null || text === undefined ? '' : text)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
    }
  };

  /* ---------------------------------------------------------- Toast */
  var UI = {
    Fmt: Fmt,

    toast: function (title, message, type, timeout) {
      var box = document.getElementById('toasts');
      if (!box) return;

      var el = document.createElement('div');
      el.className = 'toast ' + (type || 'info');
      el.innerHTML =
        '<div><div class="t-title">' + Fmt.escape(title) + '</div>' +
        (message ? '<div class="t-msg">' + Fmt.escape(message) + '</div>' : '') + '</div>';
      box.appendChild(el);

      var life = timeout || (type === 'err' ? 6000 : 3600);
      setTimeout(function () {
        el.classList.add('out');
        setTimeout(function () { el.remove(); }, 200);
      }, life);
    },

    ok:   function (t, m) { this.toast(t, m, 'ok'); },
    err:  function (t, m) { this.toast(t, m, 'err'); },
    warn: function (t, m) { this.toast(t, m, 'warn'); },
    info: function (t, m) { this.toast(t, m, 'info'); },

    /* ---------------------------------------------------------- 确认弹窗 */
    _confirmResolve: null,

    /**
     * 二次确认。
     * @param {Object} opts { title, html, okText, cancelText }
     * @returns {Promise<boolean>}
     */
    confirm: function (opts) {
      var o = opts || {};
      var mask = document.getElementById('modal-mask');
      var title = document.getElementById('modal-title');
      var body = document.getElementById('modal-body');
      var okBtn = document.getElementById('modal-ok');
      var cancelBtn = document.getElementById('modal-cancel');

      return new Promise(function (resolve) {
        title.textContent = o.title || '确认操作';
        body.innerHTML = o.html || '';
        okBtn.textContent = o.okText || '确认';
        cancelBtn.textContent = o.cancelText || '取消';
        mask.classList.add('show');

        function done(value) {
          mask.classList.remove('show');
          okBtn.removeEventListener('click', onOk);
          cancelBtn.removeEventListener('click', onCancel);
          mask.removeEventListener('click', onMask);
          document.removeEventListener('keydown', onKey);
          resolve(value);
        }
        function onOk() { done(true); }
        function onCancel() { done(false); }
        function onMask(e) { if (e.target === mask) done(false); }
        function onKey(e) {
          if (e.key === 'Escape') done(false);
          if (e.key === 'Enter') done(true);
        }

        okBtn.addEventListener('click', onOk);
        cancelBtn.addEventListener('click', onCancel);
        mask.addEventListener('click', onMask);
        document.addEventListener('keydown', onKey);
      });
    },

    /* ---------------------------------------------------------- 加载态 */
    loading: function (cardId, on) {
      var el = document.getElementById(cardId);
      if (!el) return;
      el.classList.toggle('loading', !!on);
    },

    /** 把错误对象转成一句人能读的话 */
    errorText: function (err) {
      if (!err) return '未知错误';
      if (err.needToken) return '令牌无效或已失效，请重新输入';
      return err.message || String(err);
    },

    /** 统一的失败提示（带需要令牌时的额外处理） */
    fail: function (err, title) {
      if (err && err.needToken) {
        this.err(title || '认证失败', '令牌无效，请重新登录');
        if (global.App && App.showLogin) App.showLogin();
        return;
      }
      this.err(title || '操作失败', this.errorText(err));
    }
  };

  global.UI = UI;
})(window);
