/* =========================================================================
 * api.js —— 通信层
 * 职责：令牌管理、统一的 fetch 封装、所有后端接口的调用函数。
 * 约定：所有方法返回后端 JSON；HTTP 非 2xx 会抛 ApiError。
 * ========================================================================= */
(function (global) {
  'use strict';

  var TOKEN_KEY = 'panel_token';

  /** 带状态码与后端载荷的异常 */
  function ApiError(message, status, payload) {
    var err = new Error(message);
    err.name = 'ApiError';
    err.status = status;
    err.payload = payload || {};
    err.needToken = status === 401;
    return err;
  }

  var Api = {
    token: '',

    /* ------------------------------------------------ 令牌 */
    loadToken: function () {
      try {
        this.token = localStorage.getItem(TOKEN_KEY) || '';
      } catch (e) {
        this.token = '';
      }
      return this.token;
    },
    saveToken: function (token) {
      this.token = token || '';
      try { localStorage.setItem(TOKEN_KEY, this.token); } catch (e) { /* 隐私模式下忽略 */ }
    },
    clearToken: function () {
      this.token = '';
      try { localStorage.removeItem(TOKEN_KEY); } catch (e) { /* 忽略 */ }
    },

    /* ------------------------------------------------ 核心请求 */
    request: function (path, options) {
      var self = this;
      var opts = options || {};
      var headers = Object.assign({}, opts.headers || {});

      if (self.token) headers['X-Auth-Token'] = self.token;

      var body = opts.body;
      if (body !== undefined && body !== null && typeof body !== 'string') {
        headers['Content-Type'] = 'application/json';
        body = JSON.stringify(body);
      }

      return fetch(path, {
        method: opts.method || 'GET',
        headers: headers,
        body: body,
        cache: 'no-store',
        credentials: 'same-origin'
      }).then(function (res) {
        var isJson = (res.headers.get('content-type') || '').indexOf('application/json') >= 0;
        return (isJson ? res.json().catch(function () { return {}; }) : res.text().then(function (t) {
          return { ok: res.ok, error: res.ok ? undefined : t };
        })).then(function (payload) {
          if (!res.ok) {
            var msg = (payload && (payload.error || payload.message)) || ('HTTP ' + res.status);
            throw ApiError(msg, res.status, payload);
          }
          return payload;
        });
      });
    },

    get: function (path) { return this.request(path); },
    post: function (path, body) { return this.request(path, { method: 'POST', body: body || {} }); },

    /* ------------------------------------------------ 具体接口 */
    ping: function () { return this.get('/api/ping'); },

    systemInfo: function () { return this.get('/api/system/info'); },
    metrics: function () { return this.get('/api/metrics'); },

    processes: function (params) {
      var q = new URLSearchParams();
      if (params) {
        if (params.search) q.set('search', params.search);
        if (params.sort) q.set('sort', params.sort);
        if (params.order) q.set('order', params.order);
      }
      var qs = q.toString();
      return this.get('/api/processes' + (qs ? '?' + qs : ''));
    },
    killProcesses: function (pids, force) {
      return this.post('/api/processes/kill', { pids: pids, force: !!force });
    },

    power: function (action, delay) {
      return this.post('/api/power/' + action, { delay: delay || 0 });
    },
    powerStatus: function () { return this.get('/api/power/status'); },

    /* 图片不能带自定义请求头，所以把令牌放到查询串里（后端三种来源都认） */
    screenFrameUrl: function (stamp) {
      return '/api/screen/frame?token=' + encodeURIComponent(this.token) + '&_=' + (stamp || Date.now());
    },
    screenSnapshotUrl: function () {
      return '/api/screen/snapshot?token=' + encodeURIComponent(this.token);
    },
    screenInfo: function () { return this.get('/api/screen/info'); },

    drives: function () { return this.get('/api/files/drives'); },
    listDir: function (path) { return this.get('/api/files/list?path=' + encodeURIComponent(path || '')); },
    downloadUrl: function (path) {
      return '/api/files/download?token=' + encodeURIComponent(this.token) + '&path=' + encodeURIComponent(path);
    },

    clipboard: function () { return this.get('/api/clipboard'); },
    startup: function () { return this.get('/api/startup'); },
    execPresets: function () { return this.get('/api/exec/presets'); },
    exec: function (command) { return this.post('/api/exec', { command: command }); }
  };

  /** 若后端返回 ok:false，抛一个普通 Error，方便统一 catch 提示 */
  Api.unwrap = function (payload) {
    if (payload && payload.ok === false) {
      throw new Error(payload.error || '操作失败');
    }
    return payload;
  };

  global.Api = Api;
  global.ApiError = ApiError;
})(window);
