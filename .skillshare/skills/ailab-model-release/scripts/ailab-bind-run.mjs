/**
 * ailab-bind-run.mjs — 绑定部署文件并同步输入输出参数（顺序不可换）。
 *
 *   切换部署文件 -> 二层弹窗「选择部署文件」按 Run ID 选行 -> 确定选择 -> 再同步输入输出参数
 *
 * 前置：ailab-version.mjs 已经停在表单弹窗上，且复用同一个 AILAB_PAGE。
 *
 * 参数（环境变量，或 --run <id> 追加在 -e 之后）：
 *   AILAB_RUN            必填。MLflow Run ID（32 位十六进制）
 *   AILAB_PAGE           必填。复用 ailab-open.mjs 输出的 page
 *   AILAB_TASK           可选。默认 ailab-model-release
 *   AILAB_EXPECT_ARRAYS  可选。要求同步后参数表里 array 至少出现几次（风机效率 2619 是 16）
 *
 * 输出：JSON（ok / changed / toast / before / after / counts / hint）
 * 退出码：0 判定成功；2 失败
 */

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function arg(key, fallback) {
  const envKey = "AILAB_" + key.toUpperCase().replace(/-/g, "_");
  const fromEnv = process.env[envKey];
  if (fromEnv !== undefined && fromEnv !== "") return fromEnv;
  const flag = "--" + key;
  const argv = process.argv.slice(2);
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === flag && argv[i + 1]) return argv[i + 1];
    if (argv[i].startsWith(flag + "=")) return argv[i].slice(flag.length + 1);
  }
  return fallback;
}

function pageFn(params, body) {
  const decls = Object.keys(params)
    .map((k) => "const " + k + " = " + JSON.stringify(params[k]) + ";")
    .join(" ");
  return new Function(decls + " " + body);
}

async function getPage(task, label) {
  if (label) {
    try {
      const page = await task.page(label);
      if (page) return page;
    } catch (err) {
      // 页签不存在就新开
    }
  }
  return await task.newPage();
}

const VISIBLE = "const visible = (el) => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el); return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0'; };";

const READ = VISIBLE + " const dlg = [...document.querySelectorAll('.el-dialog')].filter(visible).pop(); if (!dlg) return null; const text = dlg.textContent || ''; const at = text.indexOf('输入参数'); const dep = text.indexOf('当前关联部署'); return { title: ((dlg.querySelector('.el-dialog__title') || {}).textContent || '').trim(), deployed: dep >= 0 ? text.slice(dep, dep + 160) : '', params: at >= 0 ? text.slice(at, at + 3000) : text.slice(0, 3000) };";

function count(haystack, needle) {
  return String(haystack || "").toLowerCase().split(String(needle).toLowerCase()).length - 1;
}

function done(payload, code) {
  console.log(JSON.stringify(payload, null, 2));
  if (code) process.exitCode = code;
}

async function main() {
  const run = arg("run", "");
  const pageLabel = arg("page", "");
  const expectArrays = Number(arg("expect-arrays", "0")) || 0;

  if (!run || !pageLabel) {
    done({ ok: false, step: "bind-run", error: "缺少 AILAB_RUN 或 AILAB_PAGE" }, 2);
    return;
  }

  const task = await taskSpace(arg("task", "ailab-model-release"));
  const page = await getPage(task, pageLabel);

  const before = await page.evaluate(pageFn({}, READ));
  if (!before) {
    done({ ok: false, step: "bind-run", error: "页签上没有打开的弹窗：先跑 ailab-version.mjs 并复用同一个 AILAB_PAGE" }, 2);
    return;
  }

  // 1) 切换部署文件
  const openPicker = await page.evaluate(
    pageFn({}, VISIBLE + " const dlg = [...document.querySelectorAll('.el-dialog')].filter(visible).pop(); if (!dlg) return { ok: false, reason: 'no dialog' }; const btn = [...dlg.querySelectorAll('button')].find((b) => (b.textContent || '').trim() === '切换部署文件'); if (!btn) return { ok: false, reason: 'no 切换部署文件' }; btn.click(); return { ok: true };")
  );
  await sleep(2000);

  // 2) 二层弹窗里按 Run ID 选 radio + 确定选择
  const pick = await page.evaluate(
    pageFn({ run }, VISIBLE + " const dlg = [...document.querySelectorAll('.el-dialog')].filter(visible).find((d) => (((d.querySelector('.el-dialog__title') || {}).textContent) || '').includes('选择部署文件')); if (!dlg) return { ok: false, reason: 'no 选择部署文件 dialog' }; const rows = [...dlg.querySelectorAll('tr')]; const row = rows.find((tr) => (tr.textContent || '').includes(run)); if (!row) return { ok: false, reason: 'run not listed', runs: rows.map((tr) => (tr.textContent || '').trim().slice(0, 160)).filter(Boolean) }; const radio = row.querySelector('input[type=radio]'); if (!radio) return { ok: false, reason: 'row has no radio' }; radio.click(); const btn = [...dlg.querySelectorAll('button')].find((b) => (b.textContent || '').trim() === '确定选择'); if (!btn) return { ok: false, reason: 'no 确定选择' }; btn.click(); return { ok: true };")
  );
  await sleep(2500);

  const bound = await page.evaluate(pageFn({}, READ));

  // 3) 同步输入输出参数（必须在确定选择之后）
  const syncClick = await page.evaluate(
    pageFn({}, VISIBLE + " const dlg = [...document.querySelectorAll('.el-dialog')].filter(visible).pop(); if (!dlg) return { ok: false, reason: 'no dialog' }; const btn = [...dlg.querySelectorAll('button')].find((b) => (b.textContent || '').trim() === '同步输入输出参数'); if (!btn) return { ok: false, reason: 'no 同步输入输出参数' }; btn.click(); return { ok: true };")
  );
  await sleep(4000);

  const after = await page.evaluate(pageFn({}, READ));
  const toasts = await page.evaluate(
    pageFn({}, "return [...document.querySelectorAll('.el-message, .el-notification')].map((n) => (n.textContent || '').trim()).filter(Boolean).slice(-5);")
  );

  const toastOk = Array.isArray(toasts) && toasts.some((t) => t.includes("同步输入输出参数成功"));
  const changed = Boolean(before && after) && before.params !== after.params;
  const arrayCount = count(after && after.params, "array");
  const doubleCount = count(after && after.params, "double");
  const anyCount = count(after && after.params, "any");
  const deployedMentionsRun = Boolean(after && after.deployed.indexOf(run) >= 0);
  const arraysOk = expectArrays > 0 ? arrayCount >= expectArrays : true;
  const ok = Boolean(openPicker && openPicker.ok) && Boolean(pick && pick.ok) && Boolean(syncClick && syncClick.ok) && toastOk && changed && arraysOk;

  let hint = "同步成功：参数表已换成新 Run 的签名，交人工保存";
  if (!pick || !pick.ok) hint = "二层弹窗里没选中 Run：核对 Run ID 是否已上传到该实验";
  else if (!toastOk) hint = "没出现同步成功 toast：看 toasts 里的报错";
  else if (!changed) hint = "toast 成功但参数表没变：同步的是旧 Run，重跑本脚本（先切换部署文件再同步）";
  else if (!arraysOk) hint = "array 数量不足 " + expectArrays + "：签名不完整，回模型侧核对";

  done({
    ok,
    step: "bind-run",
    page: pageLabel,
    run,
    openPicker,
    pick,
    syncClick,
    toasts,
    changed,
    toastOk,
    deployedBefore: before.deployed,
    deployedAfter: bound ? bound.deployed : null,
    deployedMentionsRun,
    counts: { array: arrayCount, double: doubleCount, any: anyCount, expectArrays },
    beforeParams: (before.params || "").slice(0, 1200),
    afterParams: ((after && after.params) || "").slice(0, 1200),
    hint,
  }, ok ? 0 : 2);
}

await main();
