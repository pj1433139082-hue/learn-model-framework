/**
 * ailab-read-params.mjs — 只读核对：当前关联部署 + 参数表签名 + 类型统计。
 *
 * 不改任何东西，可以随时跑（保存前、发布前都用它）。
 *
 * 参数（环境变量，或 --expect-arrays 16 追加在 -e 之后）：
 *   AILAB_PAGE           必填。复用 ailab-open.mjs 输出的 page
 *   AILAB_TASK           可选。默认 ailab-model-release
 *   AILAB_EXPECT_ARRAYS  可选。array 至少出现几次（风机效率 2619 是 16）
 *   AILAB_EXPECT_STRINGS 可选。string 至少出现几次（factory_code + start_time = 2）
 *
 * 输出：JSON（ok / deployed / params / rows / counts / suspicious）
 * 退出码：0 核对通过；2 不通过
 */

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

function count(haystack, needle) {
  return String(haystack || "").toLowerCase().split(String(needle).toLowerCase()).length - 1;
}

function done(payload, code) {
  console.log(JSON.stringify(payload, null, 2));
  if (code) process.exitCode = code;
}

const pageLabel = arg("page", "");
const expectArrays = Number(arg("expect-arrays", "0")) || 0;
const expectStrings = Number(arg("expect-strings", "0")) || 0;

if (!pageLabel) {
  done({ ok: false, step: "read-params", error: "缺少 AILAB_PAGE" }, 2);
} else {
  const task = await taskSpace(arg("task", "ailab-model-release"));
  const page = await getPage(task, pageLabel);

  const state = await page.evaluate(
    pageFn({}, VISIBLE + " const dlg = [...document.querySelectorAll('.el-dialog')].filter(visible).pop(); if (!dlg) return null; const text = dlg.textContent || ''; const at = text.indexOf('输入参数'); const dep = text.indexOf('当前关联部署'); const rows = [...dlg.querySelectorAll('tr')].map((tr) => [...tr.querySelectorAll('td')].map((td) => (td.textContent || '').trim())).filter((cells) => cells.length).slice(0, 60); return { title: ((dlg.querySelector('.el-dialog__title') || {}).textContent || '').trim(), deployed: dep >= 0 ? text.slice(dep, dep + 160) : '', params: at >= 0 ? text.slice(at, at + 3000) : text.slice(0, 3000), rows };")
  );

  if (!state) {
    done({ ok: false, step: "read-params", error: "页签上没有打开的弹窗" }, 2);
  } else {
    const params = state.params || "";
    const counts = {
      array: count(params, "array"),
      string: count(params, "string"),
      double: count(params, "double"),
      any: count(params, "any"),
      expectArrays,
      expectStrings,
    };
    const suspicious = [];
    if (counts.double > 0) suspicious.push("参数表出现 double（输入签名不该有浮点）");
    if (counts.any > 0) suspicious.push("参数表出现 any（签名没推断出来）");
    const arraysOk = expectArrays > 0 ? counts.array >= expectArrays : true;
    const stringsOk = expectStrings > 0 ? counts.string >= expectStrings : true;
    const ok = arraysOk && stringsOk && suspicious.length === 0;

    done({
      ok,
      step: "read-params",
      page: pageLabel,
      title: state.title,
      deployed: state.deployed,
      counts,
      suspicious,
      arraysOk,
      stringsOk,
      rows: state.rows,
      params: params.slice(0, 2000),
      hint: ok
        ? "签名形态正常，可以交人工保存/发布"
        : "按 suspicious 与 counts 回查签名；同步顺序问题见 references/pitfalls.md 第 3、4 条",
    }, ok ? 0 : 2);
  }
}
