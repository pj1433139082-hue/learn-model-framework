# 踩过的坑

按「症状 → 根因 → 做法」记。每条都来自 2026-10-28 在 MLModel-2619 上的实测。

## 目录

1. [「更多」是 hover 菜单，必须原生鼠标坐标](#1-更多是-hover-菜单必须原生鼠标坐标)
2. [可见性不要用 offsetParent 判](#2-可见性不要用-offsetparent-判)
3. [先选 Run → 切换部署文件 → 确定选择 → 再同步](#3-先选-run--切换部署文件--确定选择--再同步)
4. [同步成功的判定是参数表变成新签名](#4-同步成功的判定是参数表变成新签名)
5. [模型类型 / 分类两个下拉脚本点不开](#5-模型类型--分类两个下拉脚本点不开)
6. [TaskSpace 与页签生命周期](#6-taskspace-与页签生命周期)
7. [等待要按「文本出现」判，不要只睡固定时间](#7-等待要按文本出现判不要只睡固定时间)
8. [登录态是浏览器的现成会话](#8-登录态是浏览器的现成会话)
9. [上传 Run 阶段才会遇到网络问题](#9-上传-run-阶段才会遇到网络问题)
10. [脚本不代点保存与发布](#10-脚本不代点保存与发布)

## 1. 「更多」是 hover 菜单，必须原生鼠标坐标

**症状**：`document.querySelector(".el-dropdown-link").click()` 之后 `.el-dropdown-menu` 为空，页面上反而多出一个筛选 popover。
**根因**：Element-UI 的 `el-dropdown` 监听 mouseenter，DOM 的 `click()` 不产生 hover 状态。
**做法**：取 `.el-dropdown-link` 的 `getBoundingClientRect()` 中心点，`page.mouse.move(x, y)`，等 1500 ms 再读菜单。

```js
const b = link.getBoundingClientRect();
await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2);
await sleep(1500);
```

**补充**：目标行可能在视口外，先 `scrollIntoView({ block: "center" })` 再取 rect；菜单每次只挂一棵，取「最后一个可见的」。

## 2. 可见性不要用 offsetParent 判

**症状**：菜单/弹窗肉眼可见，脚本筛「可见元素」却筛不到。
**根因**：Element-UI 的 popper 挂在 `body` 上、`position: fixed`，这类元素 `offsetParent === null`，和「隐藏」不是一回事。
**做法**：用几何 + 计算样式判，且弹窗/菜单取最后一个匹配项：

```js
const visible = (el) => {
  const r = el.getBoundingClientRect();
  const s = getComputedStyle(el);
  return r.width > 0 && r.height > 0 && s.display !== "none" && s.visibility !== "hidden" && s.opacity !== "0";
};
const dlg = [...document.querySelectorAll(".el-dialog")].filter(visible).pop();
```

**反例**：r16/r17 当年用 `offsetParent !== null` 过滤 `.el-dialog`；弹窗被 teleport 或改成 fixed 定位后会漏，本技能的脚本统一用几何判定。

## 3. 先选 Run → 切换部署文件 → 确定选择 → 再同步

**症状**：点「同步输入输出参数」toast 报成功，但参数表还是旧签名（旧 Run 的参数）。
**根因**：同步接口读的是表单里「当前关联部署」，不是列表里勾选的那一行。直接在版本弹窗的主表格里点 radio 再同步，同步的是旧 Run。
**正确顺序**：

1. 点「切换部署文件」（表单「绑定部署文件」段）；
2. 二层弹窗「选择部署文件」里，按 Run ID 选行内 radio；
3. 点「确定选择」，等表单回显「当前关联部署」= 新 Run；
4. 最后才点「同步输入输出参数」。

**判定**：参数表行数/参数名/类型列与同步前快照不同；`ailab-bind-run.mjs` 会打印 `changed`。

## 4. 同步成功的判定是参数表变成新签名

**症状**：把 toast 当成功，保存后发现平台参数页还是旧参数。
**根因**：toast 只能证明接口返回，不能证明同步的是目标 Run。
**判定基线**（风机效率 2619，2026-10-28 实测，输入签名为 18 个参数）：

| 位置 | 形态 |
|---|---|
| 输入 | `factory_code: string` + 16 个测点参数，每个都是 `array<string>` + `start_time: string` |
| 输出 | `unit: string`、`data_time: string`、`eta_excel: double`、`eta_corrected: double`、`wp_excel: double`、`wp_corrected: double`、`quality: string` |
| 禁止 | 输入里出现 `any` 或 `double` |

**为什么不能有 double**：部署配置常量（如 elevation = 28.0 m）一旦进了请求签名，平台参数页填整数 `28` 会被推断成 int64，predict 前报 `Can not safely convert int64 to float64`。常量应当只 `log_param`，不进签名。
**做法**：`ailab-read-params.mjs` 读参数表并统计类型；风机效率这种 16 测点模型加 `AILAB_EXPECT_ARRAYS=16`，数组数不对就判失败。

## 5. 模型类型 / 分类两个下拉脚本点不开

**症状**：表单里「模型类型」「分类」用原生坐标点击后，选项 popper 抓不到；同一页的「模型适用场景」却能选中。
**根因**：未定位清楚（同页三个 `el-select` 行为不一致），2026-10-28 没时间继续查。
**做法**：这两个字段人工点；其余文本字段与「模型适用场景」可脚本填。`ailab-new-model.mjs` 会把仍待补的必填项列出来。

## 6. TaskSpace 与页签生命周期

**症状**：上一步打开的菜单/弹窗，下一步找不到；或 `task.page("p2")` 报错。
**根因**：标签页被关掉，或换了一个 TaskSpace；表单状态只活在原页签里。
**做法**：一条链路固定 `AILAB_TASK` + `AILAB_PAGE`；标签关了 `await task.newPage()` 重开，并从 2.1 重来，不要试图续接半截表单。

## 7. 等待要按「文本出现」判，不要只睡固定时间

**症状**：偶发点空（按钮还没渲染、弹窗还没挂载）。
**做法**：动作后先等（列表 4500 ms、查询 3000 ms、菜单 1500 ms、弹窗 2000–2500 ms、同步 4000 ms 是实测下限），再用文本/DOM 判定；判定失败就重试这一小步，不要继续往下点。

## 8. 登录态是浏览器的现成会话

**症状**：脚本打开是登录页。
**根因**：平台用浏览器里的现有会话（2026-10-28 是同事的登录身份）；会话过期就跳登录。
**做法**：脚本不存账号密码；跳登录就人工登录，再从 2.1 重跑。

## 9. 上传 Run 阶段才会遇到网络问题

**症状**：`deploy.py` 上传卡住、502、TLS 握手超时；但浏览器点平台页面正常。
**根因**：平台页面走公网域名，上传 Run 走内网 MLflow（`ailab-mlflow.bewg.net.cn`），链路不同。
**2026-10-28 的实测链**：TCP 443 能连、明文 HTTP 收到 ELB 的 `400 The plain HTTP request was sent to HTTPS port`，但 TLS ClientHello 无回包 → 回程大包被吞 → 把 VirtualNet MTU 降到 1200 后恢复；上传成功拿到 Run ID 后再回平台同步参数。
**做法**：上传失败先按这条链排查网络与 MTU，别在平台页面上找原因；平台点选不受影响。

## 10. 脚本不代点保存与发布

**症状**：脚本一路点到底，把版本发布出去了。
**根因**：保存 / 提交 / 发布 / 取消发布都是写状态动作，且平台没有二次确认兜底。
**做法**：脚本停在「参数表已核对」；这四个按钮要用户对这一次的明确授权，逐个点、逐个回报状态。

## 实机补充（2026-10-28 深夜，browser-scout 全链路只读跑通）

以下 10 条都是踩出来的，按优先级排：

1. **同 URL 不重置 SPA**：`page.goto` 同一个 `#/...` 地址不会重载，上一轮的 drawer / 弹窗还在，
   会盖住列表导致 hover 打空。**URL 相同就先 `page.reload()`。**
2. **hover 三件套**：先 `move(4, 4)` 再移入目标（表格重渲染后鼠标原地不动不触发 mouseenter）；
   每次重取 rect；用 `document.elementFromPoint(x, y)` 校验最顶层是不是那个 link，
   不是就报「被 drawer/弹窗盖住」，不要盲点。
3. **搜完要校验**：可见行里必须含搜索词，否则数到的是旧行（实测数出 12 行旧数据 → hover 打空）；
   行数只数「表头含 模型ID」的那张表，别用全局 `tr`（会把抽屉里的表算进去）。
4. **select 封装**：三处判 `is-disabled`（wrapper class / `.el-select.is-disabled` / `input.disabled`），
   禁用就抛明确错误并可 `waitEnabled`；可见性用 `getBoundingClientRect()` 宽高 > 0；
   点开后取**最后一个可见** dropdown；坐标点击前判 topmost，不 topmost 就退回 DOM `li.click()`。
5. **表单就绪等待**：drawer 打开瞬间所有字段 disabled 且值是「请选择」（异步回填），
   必须等「模型名称 / 版本」非空再读，否则会把可取字段误判成锁死。
6. **按钮藏在别的 tab**：「切换部署文件 / 同步输入输出参数」在**「参数配置」tab 面板**里
   （`.input-params-container > .param-top-toolbar`）；停在别的 tab 时 rect 是 0×0（display:none），
   DOM `click()` 仍能生效——这正是"看起来成功"的根源。正解：绑定部署文件 tab 选 Run →
   参数配置 tab → 切换部署文件 → 确定选择 → 同步。
7. **二层弹窗表格要轮询**：容器可见 ≠ 表格已加载（实测约 300ms 才出行），打开就查会拿到 0 行。
8. **「确定选择」不落库**：弹窗原文「此处仅为当前编辑临时切换部署文件，点击模型编辑底部的"保存"后持久化更新」。
9. **toast 用观察器按「成功」匹配**：同步会连出两条（`已切换部署文件，正在同步参数...` → `从 MLflow 同步输入输出参数成功`），
   只等「同步」会拿到过渡态、误判成功。
10. **同名 label**：整页 `版本别名 / 模型名称 / 计算资源` 各 2 份（弹窗筛选区 + 表单），按 label 找字段必须 scope 到 `.el-drawer`。

### 一条被纠正的旧结论

早先记录说「模型类型 / 分类 的选项抓不到」是**误判**：这两个 `.el-select__wrapper` 本来就带 `is-disabled`，
值照常显示（「算法」「回归」），点它没有任何反应。**它们是已填且禁用，不是没填。**
判据：`wrapper.classList.contains('is-disabled')` + 读 `.el-select__selected-item` 的文本。

### 全链路只读验证（同一晚，MLModel-2619 / Run `bb408764ce7445389709c2bd6bda77c1`）

选 Run → 切换部署文件 → 确定选择 → 同步后读到：输入 **18** 项（`factory_code`/`start_time` = string，
16 个测点 = array，`hasAny=false`），输出 **7** 项（`unit`/`data_time` = string，四列 double，`quality` string）。
全程未点保存 / 发布 / 部署。
