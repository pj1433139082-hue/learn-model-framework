/**
 * ailab-open.mjs — 打开 ailab 模型列表，按搜索词定位模型，展开行尾「更多」。
 *
 * 参数（环境变量，或 --model 2619 追加在 -e 之后）：
 *   AILAB_MODEL  必填。模型编号或名称片段，如 2619 / MLModel-2619
 *   AILAB_TASK   可选。TaskSpace 名，默认 ailab-model-release
 *   AILAB_PAGE   可选。页签 label；不传则新开。输出里的 page 供后续脚本复用
 *   AILAB_URL    可选。覆盖列表页地址
 *
 * 输出：一段 JSON（ok / step / page / headers / rows / targetRow / menu / next）
 * 退出码：0 正常；2 参数缺失
 */

const LIST_URL = "https://ailab.bewg.net.cn/#/model-management/model-develop/machine/learning";

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

/**
 * 把参数内联进浏览器侧脚本，返回一个可在页面里求值的函数。
 * 不依赖 page.evaluate 的第二个参数：ego-browser 各版本对它的支持不一致。
 */
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
if (!model) {
  done({ ok: false, step: "open", error: "缺少 AILAB_MODEL（模型编号或名称片段）" }, 2);
} else {
  const task = await taskSpace(arg("task", "ailab-model-release"));
  const page = await getPage(task, arg("page", ""));
  const pageLabel = page.label || arg("page", "");

  await page.goto(arg("url", LIST_URL), { timeout: 45000 });
  await sleep(4500);
  await page.fill('input[placeholder="搜索模型名称"]', model);

  const queried = await page.evaluate(
    pageFn({}, "const btn = [...document.querySelectorAll('button')].find((b) => (b.textContent || '').trim() === '查询'); if (!btn) return false; btn.click(); return true;")
  );
  await sleep(3000);

  const snapshot = await page.evaluate(
    pageFn({}, VISIBLE + " const headers = [...document.querySelectorAll('thead th')].filter(visible).map((th) => (th.textContent || '').trim()); const rows = [...document.querySelectorAll('tbody tr')].filter(visible).map((tr) => [...tr.querySelectorAll('td')].map((td) => (td.textContent || '').trim())); const links = [...document.querySelectorAll('.el-dropdown-link')].map((el) => { const row = el.closest('tr'); return { text: (el.textContent || '').trim(), row: row ? (row.textContent || '').slice(0, 400) : '' }; }); return { headers, rows, links };")
  );

  const targetRow = snapshot.rows.find((cells) => cells.join(" ").includes(model)) || null;

  const rect = await page.evaluate(
    pageFn({ model }, "const links = [...document.querySelectorAll('.el-dropdown-link')]; const link = links.find((el) => { const row = el.closest('tr'); return row && (row.textContent || '').includes(model); }) || links[links.length - 1]; if (!link) return null; link.scrollIntoView({ block: 'center' }); const b = link.getBoundingClientRect(); return { x: b.x + b.width / 2, y: b.y + b.height / 2 };")
  );

  let menu = { hovered: false, items: [], error: "no .el-dropdown-link" };
  if (rect) {
    await page.mouse.move(rect.x, rect.y);
    await sleep(1500);
    const items = await page.evaluate(
      pageFn({}, VISIBLE + " const menus = [...document.querySelectorAll('.el-dropdown-menu')].filter(visible); const menu = menus[menus.length - 1]; return menu ? [...menu.querySelectorAll('li')].map((li) => (li.textContent || '').trim()).filter(Boolean) : [];")
    );
    menu = { hovered: true, rect, items };
  }

  const ok = Boolean(queried) && Boolean(targetRow) && menu.items.length > 0;
  done({
    ok,
    step: "open",
    task: arg("task", "ailab-model-release"),
    page: pageLabel,
    model,
    queryClicked: Boolean(queried),
    headers: snapshot.headers,
    rowCount: snapshot.rows.length,
    targetRow,
    menu,
    next: ok
      ? "AILAB_PAGE=" + pageLabel + " 跑 ailab-version.mjs（AILAB_ACTION=save-as-new 或 update）"
      : "菜单没展开就重跑本脚本；没有目标行就换搜索词",
  }, ok ? 0 : 2);
}
