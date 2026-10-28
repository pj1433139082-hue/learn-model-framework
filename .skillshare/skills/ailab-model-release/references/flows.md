# 三条链路的逐步操作

平台：https://ailab.bewg.net.cn/#/model-management/model-develop/machine/learning
实测基线：2026-10-28，MLModel-2619，Run `bb408764ce7445389709c2bd6bda77c1`（`respected-shark-953`）。
选择器都按那次操作验证过；平台改版后先按每步的「判定」确认文本还在，再往下点。

## 目录

1. [公共前置](#1-公共前置)
2. [链路① 已有模型版本更新](#2-链路-已有模型版本更新)
3. [链路② 新建模型](#3-链路-新建模型)
4. [链路③ 部署发布](#4-链路-部署发布)
5. [判定条件速查](#5-判定条件速查)

## 1. 公共前置

- TaskSpace：`await taskSpace("ailab-model-release")`；页签：`task.page(label)`，取不到就 `task.newPage()`。
- 每个脚本是独立进程，但浏览器里的页签是同一个：菜单、弹窗、表单状态都留在页签里，所以后续脚本必须复用 `AILAB_PAGE`。
- 打开列表页后固定等 4500 ms（SPA 首屏），查询后再等 3000 ms。
- 点击分两类，不能混：

| 动作 | 方式 | 依据 |
|---|---|---|
| 查询、版本管理、另存为新版本、切换部署文件、确定选择、同步输入输出参数 | DOM `el.click()` | r15–r23 实测可用 |
| 展开行尾「更多」 | 原生鼠标坐标 `page.mouse.move(x, y)` | DOM click 只弹筛选框，不展开菜单 |

## 2. 链路① 已有模型版本更新

### 2.1 定位模型（`ailab-open.mjs`）

动作：`goto` 列表页 → 等 4500 ms → 填搜索框 → 点「查询」→ 等 3000 ms。

```js
await page.fill('input[placeholder="搜索模型名称"]', model);
const ok = await page.evaluate(() => {
  const btn = [...document.querySelectorAll("button")].find((b) => (b.textContent || "").trim() === "查询");
  if (!btn) return false;
  btn.click();
  return true;
});
```

判定：结果表出现目标行，行文本包含模型编号或名称；同时记下该行的「发布状态」「部署状态」两列。
失败信号：`查询` 按钮找不到（页面没加载完）→ 再等 2 s 重试一次；0 行 → 换名称片段或清空其它筛选。

### 2.2 展开行尾「更多」

`el-dropdown` 绑的是 mouseenter，DOM click 不触发 hover 状态，只会弹出筛选框。必须取坐标用原生鼠标。

```js
const rect = await page.evaluate(() => {
  const links = [...document.querySelectorAll(".el-dropdown-link")];
  const link = links[links.length - 1];          // 搜索结果只有一行时够用
  link.scrollIntoView({ block: "center" });
  const b = link.getBoundingClientRect();
  return { x: b.x + b.width / 2, y: b.y + b.height / 2 };
});
await page.mouse.move(rect.x, rect.y);
await sleep(1500);
```

判定：`.el-dropdown-menu` 可见，且恰好三项：`试用` / `训练记录` / `版本管理`。
失败信号：菜单为空但页面上多了一个筛选 popover → 说明用了 DOM click；先 `page.mouse.move` 到空白处，再按上面重来。

### 2.3 版本管理

动作：在已展开的菜单里 DOM click 文本为「版本管理」的 `li`，等 3000 ms。

判定：弹出标题为「版本管理」的 `el-dialog`，表格每行是 `V001 / V002 …`，行内按钮：`查看` / `另存为新版本` / `取消发布`；弹窗顶部按钮：`版本更新`。
失败信号：弹窗标题不是「版本管理」→ 菜单被上一步的点击关掉了，重做 2.2。

### 2.4A 另存为新版本

动作：在版本管理弹窗里 DOM click `另存为新版本`，等 2500 ms。

判定：打开表单弹窗，分三段：`模型信息` / `绑定部署文件` / `参数配置`；版本号自动填上一版 +1（如 V001 → V002）。
失败信号：版本号与已有版本重复 / 为空 → 先回列表确认最新版本号，再决定另存还是更新。

### 2.4B 版本更新

动作：在版本管理弹窗顶部 DOM click `版本更新`，等 2500 ms。

判定：同一套三段表单，版本号固定为当前版本（不新增记录）。
取舍：要保留旧版本、要可回滚 → 用 2.4A；只是替换部署文件、版本号不许变 → 用 2.4B。

### 2.5 绑定部署文件（顺序关键）

1. 在表单「绑定部署文件」段 DOM click `切换部署文件`，等 2000 ms。
2. 二层弹窗标题变为 `选择部署文件`，列出实验下的 Run（Run 名 + Run ID + 创建时间）。
3. 在**这个二层弹窗内**按 Run ID 找行，点行内 `input[type="radio"]`。
4. DOM click `确定选择`，等 2500 ms。

```js
const dlg = [...document.querySelectorAll(".el-dialog")].find((d) =>
  (((d.querySelector(".el-dialog__title") || {}).textContent) || "").includes("选择部署文件"));
const row = [...dlg.querySelectorAll("tr")].find((tr) => (tr.textContent || "").includes(runId));
row.querySelector('input[type="radio"]').click();
[...dlg.querySelectorAll("button")].find((b) => (b.textContent || "").trim() === "确定选择").click();
```

判定：回到表单后「当前关联部署」显示新 Run 的 ID / 名称。
失败信号：`当前关联部署` 仍是旧 Run → 上一步的 radio 点在了外层列表而不是二层弹窗。

### 2.6 同步输入输出参数

动作：等 2500 ms 后，在表单弹窗里 DOM click `同步输入输出参数`，等 4000 ms。

判定（两条都要满足）：

1. toast 出现「从 MLflow 同步输入输出参数成功」；
2. **参数表变成新 Run 的签名**：行数、参数名、类型列都与同步前不同。

失败信号：toast 成功但参数表没变 → 同步的是「当前关联部署」那个旧 Run，回 2.5 重选。详见 [pitfalls](pitfalls.md) 第 3 条。

### 2.7 保存

人工点 `保存` / `确定`（需用户授权）。判定：列表回显新版本号；新版本默认「未发布」，「部署状态」取决于是否已绑部署文件。

## 3. 链路② 新建模型

1. 列表页 DOM click `新建模型`，等 2500 ms（`ailab-new-model.mjs`）。
2. 表单与 2.4A 同一套，7 个必填：模型名称 / 模型类型 / 分类 / 版本别名 / 编程语言 / 模型适用场景 / 模型描述。
3. 文本字段用原生 setter + `input` 事件写入，不能只改 `value`（Vue 不认）：

```js
const input = formItem.querySelector("input, textarea");
const proto = input.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
Object.getOwnPropertyDescriptor(proto, "value").set.call(input, value);
input.dispatchEvent(new Event("input", { bubbles: true }));
input.dispatchEvent(new Event("change", { bubbles: true }));
```

4. `模型适用场景` 可脚本选；`模型类型` 和 `分类` 的 popper 脚本抓不到，必须人工点（见 [pitfalls](pitfalls.md) 第 5 条）。
5. 保存后回到列表，接 2.5 / 2.6 绑 Run、同步参数。

## 4. 链路③ 部署发布

### 4.1 读状态

列表两列 + 同名筛选：

- 发布状态：`已发布` / `发布审批中` / `未发布`
- 部署状态：`已部署` / `部署审批中` / `未部署`

`ailab-open.mjs` 输出的 `headers` 与 `rows` 按列名对齐即可读，不要按列序号猜。

### 4.2 换部署文件（部署动作本身）

就是 2.5：`切换部署文件` → 二层弹窗选 Run → `确定选择` → 2.6 同步参数。版本更新与新版本两条链路共用这一段。

### 4.3 发布 / 取消发布

- 入口：版本管理弹窗行内 `取消发布`；列表/参数页的 `发布`（不同版本页面位置略有差异，按按钮文本找）。
- 发布后状态走 `发布审批中` → `已发布`；取消发布回到 `未发布`。
- 这两个按钮是写状态动作：**必须逐次拿到用户授权再点**，脚本不代点。

### 4.4 发布前核对

1. `ailab-read-params.mjs`：参数表类型全为 `array` / `string`，没有 `double`、没有 `any`。
2. `AILAB_EXPECT_ARRAYS=<N>`：按模型的测点数要求数组参数个数（风机效率 2619 是 16）。
3. 部署状态为 `已部署` 或 `部署审批中`，且「当前关联部署」是本次上传的 Run。

## 5. 判定条件速查

| 看到什么 | 说明 | 下一步 |
|---|---|---|
| 菜单三项齐全 | hover 成功 | 点「版本管理」 |
| 只弹筛选框 | 用了 DOM click | 重新 hover（2.2） |
| 版本号自动 +1 | 另存为新版本生效 | 继续绑 Run |
| 「当前关联部署」= 目标 Run | 绑定成功 | 点同步 |
| 参数表行数/类型变化 + toast 成功 | 同步成功 | 交人工保存 |
| toast 成功但参数表没变 | 同步了旧 Run | 重做 2.5 → 2.6 |
| 参数表出现 `double` / `any` | 签名不干净 | 停手：回模型侧改签名单（见 pitfalls 4） |
| 保存按钮灰 | 必填未齐 | 按 field-options 补齐 |
| 发布状态 = 发布审批中 | 已提交审批 | 等审批，不要重复点 |
