# quote0-monitor — Quote/0 墨水屏本地监控器

ChatGPT(Codex) + GLM 用量 → 渲染 → 推送到 Quote/0 电子墨水屏的两张循环卡片。
合并自 [quote0-burnout](https://github.com/ajaxjiang96/quote0-burnout)（ChatGPT 侧）
与 [quote0-glm](https://github.com/YuanTutu/quote0-glm)（GLM 侧），渲染视觉与两项目一致。
设计前提：**开机 = 在消耗额度 = 需要刷新**；关机无消耗，屏幕停留旧数据即为正确状态。

**下载使用**：到 [Releases](https://github.com/YuanTutu/quote0-monitor/releases)
下载 `quote0-monitor.exe`（单文件，免装 Python），双击运行后在配置窗口填入
自己的密钥即可——详见程序内「使用说明」。当前版本 v1.8.0。

## 运行

```powershell
pip install -r requirements.txt      # requests + Pillow + pystray
python monitor.py                    # 预览窗口
python monitor.py --silent           # 启动即隐藏到托盘
python monitor.py --once             # 自检：抓一轮推一轮即退出
python monitor.py --install-autostart    # 开机自启（托盘静默）
python monitor.py --uninstall-autostart
```

## 分发给别人用（exe）

打包（产物在 `dist\quote0-monitor.exe`，32MB 单文件，无需对方装 Python）：

```powershell
python -m PyInstaller --onefile --windowed --name quote0-monitor --clean ^
  --hidden-import zoneinfo ^
  --add-data "..\quote0-burnout\render.py;bundle/burnout" ^
  --add-data "..\quote0-burnout\providers;bundle/burnout/providers" ^
  --add-data "..\quote0-burnout\assets;bundle/burnout/assets" ^
  --add-data "..\quote0-glm\render.py;bundle/glm" ^
  --add-data "..\quote0-glm\glm.py;bundle/glm" ^
  --add-data "..\quote0-glm\assets;bundle/glm/assets" ^
  monitor.py
```

对方使用流程（**只发 exe 一个文件**，不含任何密钥）：

1. 双击运行一次 → 自动在 exe 旁边生成 `config.json` 模板后退出
2. 编辑 `config.json` 填**对方自己的**信息：
   - `glm_api_key`：对方自己的智谱开放平台 Key
   - `quote0_api_key` / `quote0_device_id`：对方自己的 Dot App 密钥和设备序列号
   - `card_gpt` / `card_glm`：对方设备上两张 Image API 卡片的 taskKey
3. 再运行 exe → 预览窗口 + 托盘；ChatGPT 卡要用的机器跑一次 `codex login`
   （不想装 codex 就临时填 `codex_access_token`，约 10 天有效）
4. 想开机自启：`quote0-monitor.exe --install-autostart`

注意：exe 模式下配置/日志/预览图都在 exe 所在目录；`--once`、`--silent`
等参数与源码模式一致。

- 关闭窗口（X / 隐藏到托盘）= 缩到右下角托盘**继续后台刷新**；真正退出在
  托盘图标右键菜单。托盘双击/“显示窗口”恢复窗口。
- 界面里可改：刷新间隔（1–60 分钟，即改即生效）、ChatGPT/GLM 开关。
- ChatGPT 侧**只读**本机 `~/.codex/auth.json` 的 token，不刷新不消耗
  refresh token，与本机 codex CLI 无冲突；GLM 侧用开放平台 API Key。
- 单实例锁：已有实例运行时再次启动会提示并退出。

## 配置（config.json，gitignored，含密钥）

| 字段 | 说明 |
|---|---|
| `interval_minutes` | 刷新间隔（分钟） |
| `enable_gpt` / `enable_glm` | 两个 provider 开关（笔记本可关 ChatGPT 只推 GLM） |
| `quote0_api_key` / `quote0_device_id` | Dot 开放平台密钥 / 设备序列号 |
| `card_gpt` / `card_glm` | 两张循环卡片的 taskKey 绑定 |
| `glm_api_key` / `glm_plan` | 智谱开放平台 Key / `cn`·`intl`·`team` |
| `glm_org` / `glm_project` | 仅团队版需要（控制台用量页请求头里可找到） |
| `codex_access_token` / `codex_account_id` | 可选：ChatGPT 账号覆盖（不填=本机 codex 登录） |

## 换账号 / 换设备怎么改

改 `config.json` 后**重启程序**（托盘右键退出再开）即生效：

| 要换什么 | 改哪里 |
|---|---|
| GLM 账号 | `glm_api_key`（团队版另填 `glm_plan: "team"` + `glm_org`/`glm_project`） |
| ChatGPT 账号（推荐） | 该账号下跑一次 `codex login`——程序自动跟随机器登录走 |
| ChatGPT 账号（不登录 CLI） | `codex_access_token` + `codex_account_id` 填入 config（token 约 10 天过期，适合临时用） |
| 墨水屏设备 / 卡片 | `quote0_device_id` / `card_gpt` / `card_glm` |
| Dot 推送密钥 | `quote0_api_key` |

注意：`codex_access_token` 填了就**优先于**本机 codex 登录；留空删除即恢复默认。

## 部署到笔记本

1. 拷贝整个 `quote0-monitor` 文件夹（自包含：渲染器/provider/字体都在
   `bundle\` 里，config.json 已含密钥）
2. `pip install -r requirements.txt`（或直接用打包好的 exe）
3. 只推 GLM：界面取消 ChatGPT 勾选（写入 config），无需 codex 登录；
   也要 ChatGPT 卡则先跑一次 `codex login`（各机 token 链独立）
4. `python monitor.py --install-autostart`

> 本项目由 quote0-burnout（ChatGPT 侧）与 quote0-glm（GLM 侧）合并而来，
> 两者被实际使用的渲染器/provider/字体已收编进 `bundle\`；两个 GitHub
> 仓库（YuanTutu/quote0-burnout、quote0-glm）保留作历史存档。

## 失败判定与恢复机制

程序不探测"网络"本身，判定粒度是**每个数据源**（chatgpt.com 与
open.bigmodel.cn 各自独立）能否拿到数据：

```
一次抓取失败（超时/连接错误）
 → 10 秒后重试 → 再失败 → 25 秒后重试 → 又失败
     ‖ 三连败（跨约 35 秒）＝ 本周期失败 ‖
 → 进入停更态：屏上保留最后一次成功的数字（数字/进度条原样），
   时间戳冻结在最后成功时刻并加 *，页脚徽标位换为错误标记
 → 此后指数退避重试：10 → 20 → 40 → 60 分钟（封顶，
   config 的 stale_max_backoff_minutes，默认 60）
 → 任何一次成功 → 计数清零、退出停更态、恢复正常推送
```

- **徽标区分失败原因**：`NET ERR`（网络不可达）／ `AUTH ERR`
  （密钥无效、未登录 codex、GLM key 错误）。认证类失败不参与快速重试
  （重试无意义），但同样进停更态并按退避节奏定期复查——修好配置后
  无需重启程序，下个退避到点自动恢复；
- **停更帧只推一次**，不重复推送相同画面；最后成功快照落盘
  `tmp\last_good.json`，断网期间重启程序依然能显示余额+错误标记；
- **「立即刷新」按钮随时无视退避插队**（排障用）；
- 从网络实际断开到屏上出现错误标记，最坏延迟 ≈ 一个刷新周期 + 35 秒。

## 排障

| 现象 | 处理 |
|---|---|
| GLM 拉取失败"身份验证失败" | config 里 glm_api_key 带了引号 → 去掉引号 |
| ChatGPT 拉取失败 no auth | 本机没登录 codex → 跑一次 `codex` |
| 推送 404 | App 循环列表里卡片被删/换 key → `--list-tasks` 查新 key 改 config |
| 时间戳旧 | 正常：机器刚开机/间隔未到；点“立即刷新” |
