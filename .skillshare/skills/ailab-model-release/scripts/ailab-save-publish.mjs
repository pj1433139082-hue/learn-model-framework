/**
 * 收尾两步：点「保存并继续」→ 点「发布」。
 *
 * 用法（ego-browser nodejs -e 里先赋值再 import）：
 *   globalThis.__AILAB_SAVE_ARGS__ = { publish: true }
 *
 * 已实测（2026-10-29 00:45，MLModel-2619）：
 *   - 「保存并继续」：DOM click 生效，toast「操作成功」，抽屉关闭，版本管理里出现 V002（未发布/未部署）
 *   - 「发布」：更多菜单能展开、菜单项坐标正确，但**鼠标点击与键盘 Enter 都没有任何反应**
 *     （无弹窗、无 toast、无路由变化、无新标签页）→ 抛 E_PUBLISH_UNRESPONSIVE，交回人工
 */
const args = globalThis.__AILAB_SAVE_ARGS__ || {};

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export async function saveAndPublish(page, { publish = false } = {}) {
  const task = page.task ? page.task() : null;
  const saved = await page.evaluate(() => {
    const d = document.querySelector('.el-drawer');
    if (!d || !d.getBoundingClientRect().width) return 'no-drawer';
    const btn = [...d.querySelectorAll('button')].find((b) => (b.textContent || '').trim() === '保存并继续');
    if (!btn) return 'no-button';
    btn.click();
    return 'clicked';
  });
  console.log('[ailab] 保存并继续:', saved);
  await sleep(3000);
  const toasts = await page.evaluate(() =>
    [...document.querySelectorAll('.el-message, .el-notification')].map((n) => (n.textContent || '').trim()).filter(Boolean)
  );
  console.log('[ailab] toasts:', JSON.stringify(toasts));
  const drawerOpen = await page.evaluate(() => {
    const d = document.querySelector('.el-drawer');
    return !!d && d.getBoundingClientRect().width > 0;
  });
  if (drawerOpen) throw new Error('E_SAVE_NOT_CLOSED: 点了保存但抽屉没关，先看表单校验提示');

  if (!publish) return { saved: true, published: false };

  // 发布：展开行尾「更多」（hover 必须原生鼠标）
  const link = await page.evaluate(() => {
    const links = [...document.querySelectorAll('.el-dropdown-link')];
    for (const e of links) {
      const b = e.getBoundingClientRect();
      if (!b.width) continue;
      const t = document.elementFromPoint(b.x + b.width / 2, b.y + b.height / 2);
      if (t === e || (t && e.contains(t))) return { x: b.x + b.width / 2, y: b.y + b.height / 2 };
    }
    return null;
  });
  if (!link) throw new Error('E_MORE_NOT_HITTABLE: 行尾「更多」被抽屉/弹窗盖住，先 reload 清场');
  await page.mouse.move(4, 4);
  await page.mouse.move(link.x, link.y);
  await sleep(1600);
  const item = await page.evaluate(() => {
    const m = [...document.querySelectorAll('.el-dropdown-menu')].find((x) => x.getBoundingClientRect().width > 0);
    if (!m) return null;
    const li = [...m.querySelectorAll('li')].find((x) => (x.textContent || '').trim() === '发布');
    if (!li) return null;
    const b = li.getBoundingClientRect();
    return { x: b.x + b.width / 2, y: b.y + b.height / 2 };
  });
  if (!item) throw new Error('E_PUBLISH_ITEM_MISSING: 菜单里没有「发布」');
  await page.mouse.click(item.x, item.y);
  await sleep(2500);
  const changed = await page.evaluate(() => {
    const dialogs = [...document.querySelectorAll('.el-dialog')].filter((d) => d.getBoundingClientRect().width > 0).length;
    const toasts = [...document.querySelectorAll('.el-message, .el-notification')].length;
    return { dialogs, toasts, hash: location.hash };
  });
  if (!changed.dialogs && !changed.toasts) {
    throw new Error(
      'E_PUBLISH_UNRESPONSIVE: 点「发布」无弹窗/无提示/无路由变化 —— 平台侧可能需要人工点或权限，见 references/pitfalls.md 末节'
    );
  }
  return { saved: true, published: true, evidence: changed };
}

if (args.auto) {
  const { taskSpace } = globalThis;
  const task = await taskSpace(args.space || 'ailab-model-params-20261028');
  const page = task.page(args.page || 'p2');
  const out = await saveAndPublish(page, { publish: args.publish !== false });
  console.log(JSON.stringify(out));
}
