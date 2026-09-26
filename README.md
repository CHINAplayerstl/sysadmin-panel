# 系统管理面板 · SysAdmin Panel

**中文** | [English](README.en.md)

> 一个跑在 Windows 本机、用浏览器访问的系统管理面板。
> 进程管理 · 电源控制 · 屏幕监控 · 文件浏览 · 剪贴板 · 启动项 · 命令回显 · 实时监控。
>
> 后端 Flask，前端原生 JS + ECharts，**依赖极少、离线可用**，不需要组策略，Windows 10 家庭版也能跑。

---

## ⚠️ 安全警告（请先读这段）

这个面板能**结束进程、执行命令、查看屏幕、下载任意文件、关机重启**。
它的访问令牌，本质上等于**这台机器的最高权限**。

- **默认只监听 `127.0.0.1`**。想开放到局域网，请务必先设置一个**长随机令牌**。
- 程序有一条硬性闸门：`host` 不是回环地址却**没配令牌**时，**直接拒绝启动**。
- **绝对不要做端口映射或内网穿透把它暴露到公网。**
- 若启用常驻服务（SYSTEM 身份运行），"命令执行"接口等于 **SYSTEM 级任意命令**，
  文件浏览器也能读全盘 —— 请自行评估是否接受。
- 本项目按 MIT 协议开源，**不提供任何担保**，用出问题请自己兜着。

---

## 功能

| 模块 | 说明 |
|---|---|
| **进程管理** | PID / 名称 / CPU / 内存 / 状态 / 用户；模糊搜索、四字段排序、批量勾选、批量结束。每 3 秒自动刷新 |
| **风险分级** | 进程分 `fatal`（结束会蓝屏，如 lsass/csrss）、`important`、`normal` 三级；`fatal` 级后端默认**拒绝**，需前端二次确认才带 `force` |
| **电源控制** | 延时关机 / 重启 / 取消 / 锁屏 / 休眠 / 睡眠，全部弹窗二次确认 |
| **屏幕监控** | 服务端抓屏推 JPEG，前端可调 1 / 2 / 0.5 / 0.33 帧每秒；手动截图下载；全屏查看 |
| **实时监控** | CPU / 内存 / 磁盘 / 网络上下行折线图（ECharts），后台采样线程 + 历史队列 |
| **系统信息** | CPU 型号、核心数、内存总量、系统版本、开机时长、各盘符容量 |
| **文件浏览器** | 浏览磁盘目录、下载文件（支持断点续传），路径越界有校验 |
| **剪贴板** | 查看当前剪贴板文本 |
| **启动项** | 注册表 Run/RunOnce（含 32/64 位视图）+ 两个启动文件夹 |
| **命令执行** | 网页执行 CMD 并回显。**默认白名单模式**（27 条查询命令），并禁止 `& \| > < ^` 等 shell 控制符 |
| **常驻服务** | 可选：装成开机自启的 SYSTEM 常驻服务，**不管谁登录、甚至没人登录都能访问** |

界面：深色科技风，卡片式仪表盘，toast 提示 + Promise 化确认弹窗 + 加载动画，纯 CSS Grid 响应式。

---

## 架构：为什么需要一个"会话内助手"

这个项目最有意思的地方，是一个 Windows 上的真实约束逼出来的设计。

**需求矛盾**：既要"没人登录时也能访问面板"，又要有"屏幕监控"。

- 要做到前者，服务必须以 **SYSTEM 身份常驻会话 0**（与任何用户登录会话无关）。
- 但**截屏和剪贴板是会话级资源** —— 会话 0 里抓不到用户桌面，只会得到黑屏和空剪贴板。

**解法**：把主服务和"抓屏"拆成两个进程。

```
        ┌──────────── 会话 0（服务会话，SYSTEM）────────────┐
        │  面板主服务                                      │
        │    进程 / 电源 / 文件 / 命令 / 系统监控   ✅ 全可用 │
        │    截屏 / 剪贴板：自己抓不到（会话级资源）         │
        └───────────────────┬──────────────────────────────┘
                            │  WTSQueryUserToken
                            │  + DuplicateTokenEx
                            │  + CreateProcessAsUser
                            ▼
        ┌──────────── 用户桌面会话 ────────────────────────┐
        │  助手 agent.py（以登录者本人身份运行）             │
        │    抓屏 / 读剪贴板 → POST 回 127.0.0.1 内部接口    │
        └──────────────────────────────────────────────────┘
```

助手只在**真的有人看屏幕时**才抓屏（前端停止轮询 8 秒后它就空转），平时几乎不占 CPU。
没人登录时，屏幕卡片会显示一张写清楚原因的**占位图**，而不是裂图。

### 助手为什么要部署到 `C:\ProgramData`

助手以**当前登录者本人**的身份运行，而项目通常位于某个用户的目录下
（`C:\Users\<用户名>\...`），那里的 ACL 只给该用户和 SYSTEM/Administrators。

**换一个用户登录时，那个用户连项目目录都进不去**，`CreateProcessAsUser` 连解释器都打不开，
症状就是网页上永远显示"正在拉起会话内助手"。

所以主服务会自动把助手需要的文件部署到 `C:\ProgramData\sysadmin-panel\` 并授权：

| 内容 | 权限 |
|---|---|
| `agent.py` / `screen_grab.py` | `BUILTIN\Users` 只读+执行 |
| `agent-config.json`（只有端口和 `agent_secret`） | `BUILTIN\Users` 只读 |
| 助手日志目录 | `BUILTIN\Users` 可写 |
| Python 解释器目录 | `BUILTIN\Users` 只读+执行 |

> 面板的 `config.json`（含 `access_token`）**不会**被复制过去 —— 那个令牌不能让普通用户读到。
> 部署是幂等且自动的，改了 `agent.py` 不需要重跑安装脚本。

---

## 快速开始

### 1. 依赖

```powershell
python -m pip install -r requirements.txt
```

要求 Python 3.10+（开发环境是 3.12）。依赖只有四个：Flask、psutil、Pillow、mss。

### 2. 配置

首次启动会自动生成 `config.json` 并随机分配令牌。也可以先手动复制模板：

```powershell
Copy-Item config.example.json config.json
```

关键项：

| 配置项 | 说明 |
|---|---|
| `host` | `127.0.0.1` 仅本机（默认）；`0.0.0.0` 局域网可访问（**必须配 `access_token`**） |
| `port` | 端口，默认 8787 |
| `access_token` | 访问令牌。留空 = 不校验（危险）。生成：`python -c "import secrets;print(secrets.token_urlsafe(24))"` |
| `allow_arbitrary_commands` | `false` = 命令白名单模式（默认）；`true` = 允许任意命令（危险） |
| `file_roots` | 文件浏览器可访问的根目录；留空 = 所有盘符 |
| `screenshot_quality` | 截屏 JPEG 质量，默认 70 |

### 3. 启动

```powershell
python run.py                  # 用 config.json 里的 host/port
python run.py --port 8788      # 临时换端口
python run.py --host 0.0.0.0   # 临时开放到局域网
```

也可以双击 `start.cmd`。

启动时如果端口被占，程序会**直接告诉你占用者是谁**（PID + 进程名），而不是甩一个 `WinError 10048`。

### 4. 打开

浏览器访问 `http://127.0.0.1:8787`，首屏输入访问令牌即可。令牌存在 localStorage 里，之后不用重复输入。

---

## 装成常驻服务（可选）

```powershell
# 需要管理员权限：双击 install-service.cmd
```

它会注册一个计划任务 `sysadmin-panel`：

| 机制 | 作用 |
|---|---|
| 开机触发（延迟 30 秒） | 开机自动启动，**不需要任何人登录** |
| 每 1 分钟看门狗 | 进程退出后自动补起；已在运行时忽略新实例 |
| 失败自动重启 | 非零退出后 1 分钟重试，最多 999 次 |
| 无执行时间上限 | 不会被任务计划程序按"超时"掐掉 |
| 身份 `SYSTEM` / 权限 `Highest` | 跑在会话 0，与任何人的登录会话都无关 |

**改完代码或配置想重启？** 不用开管理员窗口：

```powershell
curl -X POST http://127.0.0.1:8787/api/service/restart -H "X-Auth-Token: <你的令牌>"
```

它会干净退出，再由看门狗拉起（实测约 41 秒）。

> 注意：**SYSTEM 身份的计划任务在非管理员上下文里 `Get-ScheduledTask` 看不到**，
> 查询与管理都需要管理员权限。

---

## 接口一览

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/ping` | 探测令牌是否正确 |
| POST | `/api/service/restart` | 重启服务（退出后由看门狗拉起） |
| GET | `/api/system/info` | CPU 型号、内存总量、系统版本、盘符、开机时间 |
| GET | `/api/metrics` | 实时指标 + 历史序列（图表数据源） |
| GET | `/api/processes?search=&sort=&order=` | 进程列表 |
| POST | `/api/processes/kill` | `{"pids":[...], "force":false}` 批量结束 |
| POST | `/api/power/shutdown` · `/reboot` · `/cancel` · `/lock` · `/hibernate` · `/sleep` | 电源控制 |
| GET | `/api/power/status` | 待执行状态与剩余秒数 |
| GET | `/api/screen/frame` · `/snapshot` · `/info` | 屏幕帧 / 手动截图下载 / 状态 |
| GET | `/api/files/drives` · `/list?path=` · `/download?path=` | 盘符 / 列目录 / 下载 |
| GET | `/api/clipboard` | 剪贴板文本 |
| GET | `/api/startup` | 开机启动项 |
| GET | `/api/exec/presets` · POST `/api/exec` | 命令白名单 / 执行命令 |
| —— | `/internal/agent/*` | 仅供会话内助手，回环地址 + `agent_secret` 双保险 |

令牌三种传法：请求头 `X-Auth-Token`、查询串 `?token=`（`<img>` 用这种）、Cookie `panel_token`。

---

## 安全模型

已实现的防护：

1. **默认只监听 `127.0.0.1`**，局域网里其它设备根本连不上。
2. **所有 `/api/*` 都要令牌**，用 `hmac.compare_digest` 常数时间比较。
3. **监听非本机地址却无令牌时拒绝启动** —— 不给你犯错的机会。
4. **失败限速**：同一 IP 5 分钟内失败 10 次 → 封禁 5 分钟（令牌是唯一防线，必须防爆破）。
5. **命令默认白名单 + 禁止 shell 元字符**，防止 `ipconfig & 别的什么` 绕过。
6. **文件访问限定在 `file_roots` 内**，用 `os.path.commonpath` 校验（`C:\foo` 骗不过 `C:\foobar`）。
7. 响应头带 `nosniff` / `SAMEORIGIN`，`/api/*` 全部 `no-store`。
8. **内部接口只允许回环地址**并校验独立密钥，局域网无法访问。

你还需要自己注意的：

- **令牌不要外传**：别截图、别提交、别贴聊天记录。
- 想更安全就把 `file_roots` 收窄，例如 `["C:\\Users\\<你>"]`。
- Flask 自带的是**开发服务器**。本机自用没问题；要长期后台跑可换 `waitress`：
  `pip install waitress`，然后 `waitress-serve --listen=127.0.0.1:8787 run:app`（需自行导出 `app`）。

---

## 踩坑记录

这些是开发过程中真实踩到并修掉的坑，写出来给遇到同类问题的人省点时间。

**1. 会话 0 抓不到屏 —— 这不是 bug，是 Windows 的设计**
以 SYSTEM 常驻的服务跑在会话 0，`ImageGrab` / `mss` 在这里只能抓到黑屏。
正确解法是用 `WTSQueryUserToken` + `CreateProcessAsUser` 把抓屏助手送进用户的交互式会话。

**2. 换用户登录后助手起不来 —— 用户目录 ACL**
项目在 `C:\Users\<A>\...`，ACL 只给 A 和 SYSTEM/Administrators。
B 登录时连目录都进不去。解法是把助手部署到 `C:\ProgramData` 并单独授权。
（顺带一提：Windows 默认给所有用户 `SeChangeNotifyPrivilege`（绕过遍历检查），
所以**不需要**给上级目录授权，直接授权目标目录即可。）

**3. `os.execv` 在 Windows 上不能用来"原地重启"**
它实际是"另起一个进程 + 结束自己"，PID 会变；更糟的是 Flask 的监听套接字设了
`SO_REUSEADDR`，新进程能**重复绑定**同一端口，两个套接字抢端口 ——
表现是端口"在监听"但所有连接超时，旧套接字还会挂在已死进程名下变成孤儿。
正确做法是干净退出，交给计划任务的看门狗重新拉起。

**4. 登录后弹出两个 `desktop.ini` 记事本**
资源管理器会在登录时遍历 Startup 目录并"启动"其中的条目。
正常的 `desktop.ini` 带 `Hidden+System` 属性会被跳过；一旦属性丢失，
它就被当成普通启动项交给 `ShellExecute`，而 `.ini` 的默认处理程序是 `NOTEPAD.EXE` ——
两个 Startup 文件夹正好对应两个记事本。修复就是恢复它的 `Hidden+System`。

**5. SYSTEM 身份的计划任务在非管理员上下文里"不存在"**
`Get-ScheduledTask` 会**直接列不出来**它。排查时要提权，或用 `schtasks /query`。

**6. `Set-LocalUser -UserMayChangePassword` 的报错文案是错的**
在管理员账户上会抛"不能删除最后一个管理员"，而真实错误码是 **1322 ERROR_ACCOUNT_RESTRICTION**：
Windows **刻意禁止**把"用户不能更改密码"设在 Administrators 成员身上，因为它要保证管理员总能登录。
只有绕过 cmdlet 直接看 Win32 返回值才看得到真实错误码。

**7. `pythonw.exe` 下 `sys.stdout` 是 `None`**
无控制台启动时，任何 `print()` 都会抛 `AttributeError` 把服务搞挂。
必须在**导入 Flask 之前**把输出重定向到日志文件（werkzeug 的日志处理器也是导入时绑定 `sys.stderr` 的）。

---

## 目录结构

```
sysadmin-panel/
├── run.py                  # 启动入口：端口诊断、安全闸门、日志重定向
├── config.py               # 配置读写（首次运行生成 config.json + 随机令牌）
├── agent.py                # 会话内助手：在用户桌面抓屏 / 读剪贴板
├── screen_grab.py          # 抓屏共享实现（mss 优先约 45ms，PIL 兜底约 530ms）
├── smoke_test.py           # 接口自检（34 项冒烟测试，全程只读）
├── start.cmd               # 前台启动
├── install-service.ps1/.cmd# 装成 SYSTEM 常驻服务（需管理员）
├── takeover-8787.ps1/.cmd  # 接管被占用的端口：结束占用者 + 加防火墙规则（需管理员）
├── requirements.txt / config.example.json / LICENSE / .gitignore
│
├── app/                    # 后端
│   ├── __init__.py         # 应用工厂 + 重启接口 + 统一错误响应
│   ├── auth.py             # 令牌校验 + 失败限速
│   ├── sampler.py          # 后台指标采样线程
│   ├── wts.py              # 跨会话启动（WTS API 封装）
│   ├── live_state.py       # 主服务与助手之间的共享状态
│   ├── agent_control.py    # 助手的部署与生命周期
│   ├── utils.py            # 进程风险分级、控制台编码、命令执行
│   └── api_*.py            # 各接口蓝图（system / process / power / screen / files / misc）
│
├── templates/index.html    # 单页仪表盘
└── static/
    ├── css/style.css       # 深色科技风样式
    ├── js/                 # api.js / ui.js / charts.js / modules.js / main.js
    └── vendor/echarts.min.js
```

---

## 自检

改完代码后可以跑一遍：

```powershell
python smoke_test.py http://127.0.0.1:8787 <你的令牌>
```

覆盖认证、静态资源、指标增长、进程搜索排序、危险操作拦截、截屏与缓存、
文件越界拦截、剪贴板、启动项、命令白名单等。**全程只读**，唯一"危险"的用例是
故意用 PID 4 试探拦截逻辑，不会真杀东西。

---

## 已知限制

- 屏幕监控是**截图轮询**而非视频流，最高 2 帧每秒，不适合看视频。
- **锁屏 / 安全桌面上无法截图**（Windows 限制），此时会显示明确提示，解锁后自动恢复。
- 剪贴板、屏幕只在**有人登录**时才有内容。
- 命令执行基于 `cmd.exe`，不支持交互式命令（如 `diskpart`、`ftp`）。
- 只做了 Windows 适配（依赖 WTS API 与注册表）。

---

## License

[MIT](LICENSE)
