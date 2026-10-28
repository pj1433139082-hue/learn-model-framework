---
name: ailab-model-release
description: 在北控 ailab 模型实验室平台（ailab.bewg.net.cn）完成模型版本更新、新建模型与部署发布：用 ego-browser 脚本走通「模型列表 → 更多 → 版本管理 → 另存为新版本/版本更新 → 切换部署文件 → 同步输入输出参数」，并按新 Run 的签名核对参数表。触发词：ailab、模型实验室平台、模型版本更新、同步输入参数、绑定部署文件、新建模型、发布、部署、发布状态、部署状态。
---

# ailab 模型版本更新与发布

> contract: workflow=none steps=none

平台地址：https://ailab.bewg.net.cn/#/model-management/model-develop/machine/learning
平台是 Element-UI 单页应用；登录态沿用浏览器里的现有会话，脚本不处理账号密码。
浏览器只用 ego-browser（先读它的技能说明确认 CLI 用法），脚本用 `nodejs` 模式跑。

## 先定边界

- 上传 Run、点「保存 / 提交 / 发布 / 取消发布」都是对外动作：每一次都要用户明确授权。
- 本技能默认做到「参数表已核对」为止，保存与发布交回人工。
- 一条链路只用一个 TaskSpace 和一个页签（默认 `ailab-model-release`）：换页签等于丢菜单、弹窗和表单状态。

## 三条链路怎么选

| 链路 | 判据 | 入口 | 结束状态 |
|---|---|---|---|
| ① 已有模型版本更新 | 列表里搜得到该模型 | 行尾「更多」→ 版本管理 | 新版本（另存）或原版本（更新）绑好新 Run |
| ② 新建模型 | 平台还没有这条模型 | 模型列表 → 新建模型 | 新模型记录（未发布），再绑 Run |
| ③ 部署发布 | 版本与 Run 已就位 | 版本管理 / 列表状态列 | 部署状态、发布状态推进 |

选择顺序：搜不到模型 → ②；有新 Run 要出新版本号 → ① 里的「另存为新版本」；版本号不变只换部署文件 → ① 里的「版本更新」；只差状态推进 → ③。

## 主流程

1. 备参数：`AILAB_MODEL`（模型编号或名称片段，如 `2619`）、`AILAB_RUN`（MLflow Run ID，32 位十六进制）。
2. `<skill>/scripts/ailab-open.mjs`：打开列表（**同一 URL 复用页签时先 `reload`**，否则上一轮的 drawer 会盖住列表——见 pitfalls 第 1 条） → 搜索 → 打印目标行与发布/部署状态 → hover「更多」并打印菜单。记下输出里的 `page`，后续脚本用 `AILAB_PAGE` 复用它。
3. `<skill>/scripts/ailab-version.mjs`：`AILAB_ACTION=save-as-new`（或 `update`）进入表单，核对版本号（自动 +1）与 7 个必填项。
4. `<skill>/scripts/ailab-bind-run.mjs`：切换部署文件 → 在「选择部署文件」弹窗里按 Run ID 选行 → 确定选择 → 再点同步输入输出参数。顺序不可颠倒。
5. `<skill>/scripts/ailab-read-params.mjs`：只读核对「当前关联部署」与参数表签名；`AILAB_EXPECT_ARRAYS=16` 时要求 16 个 array 参数。
6. 人工保存/提交（需授权），再用 `ailab-open.mjs` 复读发布状态与部署状态。

每步的选择器、判定条件与失败信号：读 [references/flows.md](references/flows.md)。
表单字段、下拉实录与状态取值：读 [references/field-options.md](references/field-options.md)。
踩过的坑与实测签名形态：动手前先读 [references/pitfalls.md](references/pitfalls.md)。

## 脚本

| 脚本 | 参数（环境变量） | 用途 |
|---|---|---|
| `<skill>/scripts/ailab-open.mjs` | `AILAB_MODEL`；可选 `AILAB_TASK` `AILAB_PAGE` `AILAB_URL` | 打开列表、搜索、打印行与发布/部署状态、hover「更多」并打印菜单项 |
| `<skill>/scripts/ailab-version.mjs` | `AILAB_MODEL`；`AILAB_ACTION`（menu / save-as-new / update）；`AILAB_PAGE` | 点「版本管理」，再点「另存为新版本」或「版本更新」，打印表单与必填缺口 |
| `<skill>/scripts/ailab-bind-run.mjs` | `AILAB_RUN`（必填）；`AILAB_PAGE`；可选 `AILAB_EXPECT_ARRAYS` | 切换部署文件 → 选 Run → 确定选择 → 同步参数 → 判定签名是否变化 |
| `<skill>/scripts/ailab-read-params.mjs` | `AILAB_PAGE`；可选 `AILAB_EXPECT_ARRAYS` `AILAB_EXPECT_STRINGS` | 只读：当前关联部署 + 参数表 + 类型统计（array/string/double/any） |
| `<skill>/scripts/ailab-save-publish.mjs` | `__AILAB_SAVE_ARGS__={publish:true}` | 点「保存并继续」（**已实测可用**，判据 toast「操作成功」+抽屉关闭）→ 再点「发布」（当前平台点了无反应，抛 `E_PUBLISH_UNRESPONSIVE` 交回人工） |
| `<skill>/scripts/ailab-run-update-version.mjs` | `AILAB_MODEL` `AILAB_RUN`；可选 `AILAB_ALIAS` | 一条命令串起 open → save-as-new → bind-run → read-params，默认 dry-run，不点保存/发布 |
| `<skill>/scripts/lib/` | — | 六个可复用模块：`page` `menu` `select` `dialog` `table` `toast`（另含 `form` `dom` `params` `errors` `constants` `index`）；10 条坑的可跑实现都在这里 |
| `<skill>/scripts/ailab-new-model.mjs` | `AILAB_NAME` `AILAB_ALIAS` `AILAB_DESC`；`AILAB_PAGE` | 打开「新建模型」表单、填文本字段、列出仍待补的必填项 |

运行方式（cmd；`<技能目录>` 换成本技能的实际路径）：

```bat
set AILAB_MODEL=2619
set AILAB_PAGE=ailab
"C:\Users\14331\.local\share\ego\active_version_dir\ego-browser.exe" nodejs -e "await import('file:///C:/<技能目录>/<skill>/scripts/ailab-open.mjs')"
```

PowerShell 写 `$env:AILAB_MODEL="2619"`。参数也可以 `--model 2619` 或 `--model=2619` 追加在 `-e` 之后，脚本两种都认（环境变量优先）。每个脚本结束打印一段 JSON，含 `ok` 与判定依据。

## 失败时的兜底

| 现象 | 先查 | 兜底 |
|---|---|---|
| `.el-dropdown-menu` 空、只弹出筛选框 | 是不是用了 DOM click | 改用原生鼠标 hover（pitfalls 1） |
| 菜单/弹窗明明可见却抓不到 | 是不是用 `offsetParent` 判可见 | 换 `getBoundingClientRect`（pitfalls 2） |
| 同步后参数表没变 | 动作顺序 | 回「切换部署文件」重选 Run 再同步（pitfalls 3） |
| toast 成功但类型不对 | 参数表类型列 | 按 pitfalls 4 的签名逐项核对 |
| 保存按钮灰 | 7 个必填项 | 按 field-options 补齐 |
| 模型类型/分类选不中 | Element-UI popper | 这两个字段人工点（pitfalls 5） |
| 脚本报找不到页签 | `AILAB_PAGE` 与上一步不一致 | 用 `ailab-open.mjs` 重开并复用新输出的 `page` |
| 页面跳登录 / 404 | 浏览器会话 | 人工登录后从第 2 步重跑 |
| 上传 Run 失败（502、TLS 超时） | 网络与 MTU | 见 pitfalls 9；平台点选本身不依赖内网上传通道 |

脚本只做只读与「切换部署文件 / 同步参数」这类不保存即不生效的动作；任何写状态的动作都停手问用户。
