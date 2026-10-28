/**
 * ailab-version.mjs — 从行尾「更多」进「版本管理」，再进「另存为新版本」或「版本更新」表单。
 *
 * 前置：ailab-open.mjs 已经跑过，且用同一个 AILAB_PAGE（菜单/弹窗状态留在页签里）。
 *
 * 参数（环境变量，或 --action save-as-new 追加在 -e 之后）：
 *   AILAB_MODEL   必填。用于定位行，仅日志与打印
 *   AILAB_ACTION  可选。menu（默认，只打开版本管理）/ save-as-new / update
 *   AILAB_PAGE    必填。复用 ailab-open.mjs 输出的 page
 *   AILAB_TASK    可选。默认 ailab-model-release
 *
 * 输出：JSON（ok / step / versionDialog / form / missing / manual）
 * 退出码：0 正常；2 步骤没走通
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

function done(payload, code) {
  console.log(JSON.stringify(payload, null, 2));
  if (code) process.exitCode = code;
}

const model = arg("model", "");
const action = arg("action", "menu");
const actionLabel = action === "save-as-new" ? "另存为新版本" : action === "update" ? "版本更新" : "";
if (!model || !arg("page", "")) {
  done({ ok: false, step: "version", error: "缺少 AILAB_MODEL 或 AILAB_PAGE" }, 2);
} else {
  const task = await taskSpace(arg("task", "ailab-model-release"));
  const page = await getPage(task, arg("page", ""));

  // 1) 重新 hover「更多」：换进程后菜单可能是关的
  const rect = await page.evaluate(
    pageFn({ model }, "const links = [...document.querySelectorAll('.el-dropdown-link')]; const link = links.find((el) => { const row = el.closest('tr'); return row && (row.textContent || '').includes(model); }) || links[links.length - 1]; if (!link) return null; link.scrollIntoView({ block: 'center' }); const b = link.getBoundingClientRect(); return { x: b.x + b.width / 2, y: b.y + b.height / 2 };")
  );
  if (rect) {
    await page.mouse.move(rect.x, rect.y);
    await sleep(1500);
  }

  // 2) 点「版本管理」
  const menuClick = await page.evaluate(
    pageFn({}, VISIBLE + " const menus = [...document.querySelectorAll('.el-dropdown-menu')].filter(visible); const menu = menus[menus.length - 1]; if (!menu) return { ok: false, reason: 'no visible menu（先跑 ailab-open.mjs 并复用同一个 AILAB_PAGE）' }; const item = [...menu.querySelectorAll('li')].find((li) => (li.textContent || '').trim() === '版本管理'); if (!item) return { ok: false, reason: 'menu has no 版本管理: ' + (menu.textContent || '').slice(0, 120) }; item.click(); return { ok: true };")
  );
  await sleep(3000);

  const versionDialog = await page.evaluate(
    pageFn({}, VISIBLE + " const dlg = [...document.querySelectorAll('.el-dialog')].filter(visible).pop(); if (!dlg) return null; return { title: ((dlg.querySelector('.el-dialog__title') || {}).textContent || '').trim(), rows: [...dlg.querySelectorAll('tr')].map((tr) => [...tr.querySelectorAll('td')].map((td) => (td.textContent || '').trim())), buttons: [...dlg.querySelectorAll('button')].map((b) => (b.textContent || '').trim()).filter(Boolean) };")
  );

  let actionClick = null;
  let form = null;
  if (actionLabel) {
    actionClick = await page.evaluate(
      pageFn({ actionLabel }, VISIBLE + " const dlg = [...document.querySelectorAll('.el-dialog')].filter(visible).pop(); if (!dlg) return { ok: false, reason: 'no dialog' }; const btn = [...dlg.querySelectorAll('button')].find((b) => (b.textContent || '').trim() === actionLabel); if (!btn) return { ok: false, reason: 'no button: ' + actionLabel }; btn.click(); return { ok: true };")
    );
    await sleep(2500);
    form = await page.evaluate(
      pageFn({}, VISIBLE + " const dlg = [...document.querySelectorAll('.el-dialog')].filter(visible).pop(); if (!dlg) return null; const items = [...dlg.querySelectorAll('.el-form-item')].map((it) => { const label = ((it.querySelector('.el-form-item__label') || {}).textContent || '').trim(); const input = it.querySelector('input, textarea'); return { label, required: (it.className || '').indexOf('is-required') >= 0, control: it.querySelector('.el-select') ? 'select' : (it.querySelector('textarea') ? 'textarea' : 'text'), value: input ? input.value : '' }; }); return { title: ((dlg.querySelector('.el-dialog__title') || {}).textContent || '').trim(), items, buttons: [...dlg.querySelectorAll('button')].map((b) => (b.textContent || '').trim()).filter(Boolean) };")
    );
  }

  const missing = form
    ? form.items.filter((it) => it.required && !it.value && it.control !== "select").map((it) => it.label)
    : [];
  const manual = form
    ? form.items.filter((it) => it.required && it.control === "select" && !it.value).map((it) => it.label)
    : [];

  const ok = Boolean(menuClick && menuClick.ok) && Boolean(versionDialog) && Boolean(versionDialog.title.includes("版本")) && (!actionLabel || Boolean(actionClick && actionClick.ok));
  done({
    ok,
    step: "version",
    page: arg("page", ""),
    action,
    menuClick,
    versionDialog,
    actionClick,
    form,
    missing,
    manual,
    next: !actionLabel
      ? "选好 AILAB_ACTION=save-as-new 或 update 再跑一次"
      : "补齐 missing（文本必填）与 manual（下拉，人工点）后跑 ailab-bind-run.mjs",
  }, ok ? 0 : 2);
}
