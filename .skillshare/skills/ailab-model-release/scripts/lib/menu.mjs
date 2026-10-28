import { SEL, TIMEOUTS } from './constants.mjs';
import { fail } from './errors.mjs';

/**
 * 读当前所有可见下拉菜单（rect 宽高 > 0）及其菜单项。
 * 入参: page
 * 返回: { visibleCount, menus: [{ items, rect }] }
 * 失败: 不抛错。
 */
export async function readMenus(page) {
  return page.evaluate(() => {
    const menus = [...document.querySelectorAll('.el-dropdown-menu')].map((m) => {
      const b = m.getBoundingClientRect();
      const popper = m.closest('.el-popper');
      const pb = popper ? popper.getBoundingClientRect() : b;
      return {
        items: [...m.querySelectorAll('.el-dropdown-menu__item, li')]
          .map((i) => (i.textContent || '').trim())
          .filter(Boolean),
        rect: { x: Math.round(pb.x), y: Math.round(pb.y), w: Math.round(pb.width), h: Math.round(pb.height) },
      };
    });
    const visible = menus.filter((m) => m.rect.w > 0 && m.rect.h > 0);
    return { visibleCount: visible.length, menus: visible };
  });
}

/**
 * 展开表格行尾的「更多」下拉（选择器 .el-dropdown-link）。
 * 必须用原生鼠标 hover：DOM click 只会弹筛选框，不展开菜单。
 * 入参:
 *   page
 *   options.modelId        行内包含的文本（如 'MLModel-2619'），优先按它定位行
 *   options.rowIndex       没有 modelId 时按第几个含「更多」的行（默认 0）
 *   options.expectItems    期望菜单里出现的项，如 ['版本管理']，用来确认展开的是这一行的菜单
 *   options.timeout        等菜单出现的上限（默认 8000）
 *   options.linkSelector   默认 .el-dropdown-link
 * 返回: { rect: {x,y}, linkText, items, menuCount }
 * 失败:
 *   [AILAB_ROW_MORE_NOT_FOUND]  表里找不到行尾「更多」
 *   [AILAB_MENU_HOVER_TIMEOUT]  hover 后 <timeout>ms 没有可见菜单（坐标在视口外 / hover 方式不对）
 */
export async function openRowMoreMenu(page, options = {}) {
  const {
    modelId = null,
    rowIndex = 0,
    expectItems = null,
    timeout = TIMEOUTS.menu,
    linkSelector = SEL.dropdownLink,
    label = 'hover 行尾更多',
    attempts = 3,
  } = options;

  const locate = () =>
    page.evaluate(
      ({ linkSelector, modelId, rowIndex }) => {
        const vis = (el) => {
          const b = el.getBoundingClientRect();
          return b.width > 0 && b.height > 0;
        };
        const tables = [...document.querySelectorAll('.el-table')].filter(vis);
        const table = tables.find((t) => [...t.querySelectorAll('.el-table__header th')].some((th) => (th.textContent || '').trim() === '模型ID')) || tables[0];
        if (!table) return { ok: false, rows: 0 };
        const rows = [...table.querySelectorAll('.el-table__body tr')].filter(vis);
        let link = null;
        if (modelId) {
          const row = rows.find((r) => (r.textContent || '').includes(modelId));
          if (row) link = row.querySelector(linkSelector);
        } else {
          const links = rows.map((r) => r.querySelector(linkSelector)).filter(Boolean);
          link = links[rowIndex] || null;
        }
        if (!link) return { ok: false, rows: rows.length };
        link.scrollIntoView({ block: 'center', inline: 'center' });
        const b = link.getBoundingClientRect();
        const cx = Math.round(b.x + b.width / 2);
        const cy = Math.round(b.y + b.height / 2);
        const hit = document.elementFromPoint(cx, cy);
        return {
          ok: true,
          x: cx,
          y: cy,
          w: Math.round(b.width),
          h: Math.round(b.height),
          text: (link.textContent || '').trim(),
          rows: rows.length,
          topmost: !!(hit && (hit === link || link.contains(hit))),
          underCursor: hit ? hit.tagName + '.' + String(hit.className || '').split(' ').slice(0, 2).join('.') : null,
        };
      },
      { linkSelector, modelId, rowIndex },
    );

  let lastTarget = null;
  let lastDiag = null;
  for (let attempt = 1; attempt <= Math.max(1, attempts); attempt += 1) {
    const target = await locate();
    if (!target.ok) {
      fail(
        'AILAB_ROW_MORE_NOT_FOUND',
        '列表里找不到行尾「更多」(' + linkSelector + ')：当前 ' + target.rows + ' 行' +
          (modelId ? '，没有一行包含「' + modelId + '」' : '，rowIndex=' + rowIndex + ' 越界'),
        { modelId, rowIndex, rows: target.rows },
      );
    }
    lastTarget = target;
    if (!target.topmost) {
      // 坐标上盖着别的东西：几乎都是上一轮留下的 drawer / 弹窗没关（SPA 同 URL 不会重置）
      lastDiag = { reason: 'covered', underCursor: target.underCursor };
      await page.waitForTimeout(300);
      continue;
    }
    // 先把鼠标挪开再移过去：DOM 被重渲染后，鼠标停在原地不会触发 mouseenter
    await page.mouse.move(4, 4);
    await page.mouse.move(target.x, target.y, { label });
    try {
      await page.waitForFunction(
        ({ expectItems }) => {
          const menus = [...document.querySelectorAll('.el-dropdown-menu')].filter((m) => {
            const b = m.getBoundingClientRect();
            return b.width > 0 && b.height > 0;
          });
          return menus.some((m) => {
            const items = [...m.querySelectorAll('.el-dropdown-menu__item, li')]
              .map((i) => (i.textContent || '').trim())
              .filter(Boolean);
            if (!items.length) return false;
            if (!expectItems || !expectItems.length) return true;
            return expectItems.every((t) => items.includes(t));
          });
        },
        { expectItems },
        { timeout: Math.max(1500, Math.round(timeout / Math.max(1, attempts))), polling: 120 },
      );
      const state = await readMenus(page);
      const items = state.menus.length ? state.menus[state.menus.length - 1].items : [];
      return {
        rect: { x: target.x, y: target.y },
        linkText: target.text,
        items,
        menuCount: state.visibleCount,
        attempt,
      };
    } catch (err) {
      lastDiag = await page.evaluate(
        ({ x, y }) => {
          const hit = document.elementFromPoint(x, y);
          const menus = [...document.querySelectorAll('.el-dropdown-menu')].map((m) => {
            const b = m.getBoundingClientRect();
            return { w: Math.round(b.width), h: Math.round(b.height), text: (m.textContent || '').trim().slice(0, 40) };
          });
          return {
            underCursor: hit ? hit.tagName + '.' + String(hit.className || '').split(' ').slice(0, 2).join('.') : null,
            menus,
          };
        },
        { x: target.x, y: target.y },
      );
    }
  }

  if (lastDiag && lastDiag.reason === 'covered') {
    fail(
      'AILAB_MENU_COVERED',
      '「更多」的中心 (' + lastTarget.x + ',' + lastTarget.y + ') 上盖着 ' + (lastDiag.underCursor || '?') +
        '：页面没清场，多半是上一轮的 drawer / 弹窗还开着（SPA 切同一个 hash URL 不会重载）。先 page.reload() 或用 ensurePage 重新进列表页',
      lastDiag,
    );
  }
  fail(
    'AILAB_MENU_HOVER_TIMEOUT',
    'hover 到「更多」中心 (' + lastTarget.x + ',' + lastTarget.y + ') 试了 ' + attempts + ' 次，' + timeout +
      'ms 内没有出现可见菜单（光标下是 ' + ((lastDiag && lastDiag.underCursor) || '?') + '）。DOM click 不会展开这个 el-dropdown，' +
      '必须用 page.mouse.move 到 .el-dropdown-link 的 getBoundingClientRect 中心；坐标要重新取（表格重渲染后旧坐标会失效）',
    { x: lastTarget.x, y: lastTarget.y, diag: lastDiag },
  );
}

/**
 * 点某个已展开的「更多」菜单项（菜单必须先由 openRowMoreMenu 展开）。
 * 入参: page, itemText, options { exact=true, timeout=8000 }
 * 返回: { clicked, itemText, items }
 * 失败:
 *   [AILAB_MENU_NOT_OPEN]      当前没有可见菜单（先调 openRowMoreMenu）
 *   [AILAB_MENU_ITEM_NOT_FOUND] 可见菜单里没有「<itemText>」，现有项：…
 */
export async function clickMenuItem(page, itemText, options = {}) {
  const { exact = true, timeout = TIMEOUTS.menu } = options;
  const want = String(itemText == null ? '' : itemText).trim();
  if (!want) fail('AILAB_MENU_ITEM_NOT_FOUND', 'clickMenuItem 的 itemText 为空');

  let result = null;
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    result = await page.evaluate(
      ({ want, exact }) => {
        const menus = [...document.querySelectorAll('.el-dropdown-menu')].filter((m) => {
          const b = m.getBoundingClientRect();
          return b.width > 0 && b.height > 0;
        });
        if (!menus.length) return { ok: false, reason: 'no-menu' };
        const allItems = [];
        for (const menu of menus) {
          for (const item of menu.querySelectorAll('.el-dropdown-menu__item, li')) {
            const text = (item.textContent || '').trim();
            if (!text) continue;
            allItems.push(text);
            const hit = exact ? text === want : text.includes(want);
            if (hit) {
              item.click();
              return { ok: true, items: allItems, menuCount: menus.length };
            }
          }
        }
        return { ok: false, reason: 'no-item', items: allItems, menuCount: menus.length };
      },
      { want, exact },
    );
    if (result.ok) break;
    if (result.reason === 'no-menu') {
      fail('AILAB_MENU_NOT_OPEN', '当前没有可见的「更多」下拉菜单：先调 openRowMoreMenu（DOM click 不会展开菜单）');
    }
    await page.waitForTimeout(200);
  }
  if (!result || !result.ok) {
    fail(
      'AILAB_MENU_ITEM_NOT_FOUND',
      '可见菜单里没有「' + want + '」，现有项：' + ((result && result.items) || []).join(' / '),
      { want, items: (result && result.items) || [] },
    );
  }
  return { clicked: true, itemText: want, items: result.items };
}
