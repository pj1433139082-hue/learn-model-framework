/**
 * ailab-new-model.mjs — 打开「新建模型」表单，填文本字段，列出仍待补的必填项。
 *
 * 前置：ailab-open.mjs 已经把页签停在模型列表页。
 *
 * 参数（环境变量，或 --name xxx 追加在 -e 之后）：
 *   AILAB_PAGE   必填。复用 ailab-open.mjs 输出的 page
 *   AILAB_TASK   可选。默认 ailab-model-release
 *   AILAB_NAME   可选。模型名称
 *   AILAB_ALIAS  可选。版本别名
 *   AILAB_DESC   可选。模型描述
 *   AILAB_LANG   可选。编程语言
 *
 * 输出：JSON（ok / fields / filled / missing / manual）
 * 退出码：0 表单打开成功；2 失败
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

const FIELDS = VISIBLE + " const dlg = [...document.querySelectorAll('.el-dialog')].filter(visible).pop(); if (!dlg) return null; return { title: ((dlg.querySelector('.el-dialog__title') || {}).textContent || '').trim(), items: [...dlg.querySelectorAll('.el-form-item')].map((it) => { const label = ((it.querySelector('.el-form-item__label') || {}).textContent || '').trim(); const input = it.querySelector('input, textarea'); return { label, required: (it.className || '').indexOf('is-required') >= 0, control: it.querySelector('.el-select') ? 'select' : (it.querySelector('textarea') ? 'textarea' : 'text'), value: input ? input.value : '' }; }), buttons: [...dlg.querySelectorAll('button')].map((b) => (b.textContent || '').trim()).filter(Boolean) };";

function done(payload, code) {
  console.log(JSON.stringify(payload, null, 2));
  if (code) process.exitCode = code;
}

const pageLabel = arg("page", "");
const values = [
  { label: "模型名称", value: arg("name", "") },
  { label: "版本别名", value: arg("alias", "") },
  { label: "编程语言", value: arg("lang", "") },
  { label: "模型描述", value: arg("desc", "") },
].filter((pair) => pair.value);

if (!pageLabel) {
  done({ ok: false, step: "new-model", error: "缺少 AILAB_PAGE" }, 2);
} else {
  const task = await taskSpace(arg("task", "ailab-model-release"));
  const page = await getPage(task, pageLabel);

  const opened = await page.evaluate(
    pageFn({}, VISIBLE + " const btn = [...document.querySelectorAll('button')].find((b) => (b.textContent || '').trim() === '新建模型'); if (!btn) return { ok: false, reason: 'no 新建模型 button（页签要停在模型列表页）' }; btn.click(); return { ok: true };")
  );
  await sleep(2500);

  const filled = [];
  for (const pair of values) {
    const result = await page.evaluate(
      pageFn(pair, "const dlg = [...document.querySelectorAll('.el-dialog')].pop(); if (!dlg) return { ok: false, label: label, reason: 'no dialog' }; const items = [...dlg.querySelectorAll('.el-form-item')]; const item = items.find((it) => ((it.querySelector('.el-form-item__label') || {}).textContent || '').trim().indexOf(label) >= 0); if (!item) return { ok: false, label: label, reason: 'label not found' }; const input = item.querySelector('input, textarea'); if (!input) return { ok: false, label: label, reason: 'no input' }; const proto = input.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype; const setter = Object.getOwnPropertyDescriptor(proto, 'value').set; setter.call(input, value); input.dispatchEvent(new Event('input', { bubbles: true })); input.dispatchEvent(new Event('change', { bubbles: true })); return { ok: true, label: label, value: input.value };")
    );
    filled.push(result);
  }

  const fields = await page.evaluate(pageFn({}, FIELDS));
  const missing = fields
    ? fields.items.filter((it) => it.required && !it.value && it.control !== "select").map((it) => it.label)
    : [];
  const manual = fields
    ? fields.items.filter((it) => it.required && it.control === "select" && !it.value).map((it) => it.label)
    : [];

  const ok = Boolean(opened && opened.ok) && Boolean(fields) && Boolean(fields.title.includes("新建"));
  done({
    ok,
    step: "new-model",
    page: pageLabel,
    opened,
    filled,
    fields,
    missing,
    manual,
    next: ok
      ? "补齐 missing（文本）与 manual（模型类型/分类/适用场景要在页面上点）后保存；保存后接着跑 ailab-bind-run.mjs"
      : "没打开新建模型弹窗：确认页签停在模型列表页",
  }, ok ? 0 : 2);
}
