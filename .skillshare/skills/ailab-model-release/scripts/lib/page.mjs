import { AILAB_URL, SEL, TEXT, TIMEOUTS } from './constants.mjs';
import { fail } from './errors.mjs';
import { waitForVisible } from './dom.mjs';

/**
 * 复用或新建 Page（TaskSpace 里标签被关掉后 task.page(label) 会报错，必须 newPage）。
 * 入参:
 *   task    TaskSpace 句柄（await taskSpace(name)）
 *   options { label, url, waitFor, timeout }
 *     label    想复用的页面标签，如 'p2'；null 表示「有页面就复用第一个」
 *     url      给了就 goto（默认 ailab 模型列表页）
 *     waitFor  goto 后等这个选择器可见
 * 返回: { page, label, reused, created, reloaded }（reloaded=true 表示同 URL 时走了 reload 清场）
 * 失败:
 *   [AILAB_NO_TASK]            没有传 TaskSpace
 *   [AILAB_PAGE_CREATE_FAILED] 新建页面失败（多半是页面预算满了，先关掉一个 pN 标签）
 *   [AILAB_NAV_FAILED]         goto 失败（url / 网络 / 登录态）
 *   [AILAB_PAGE_READY_TIMEOUT] goto 后等不到目标元素
 */
export async function ensurePage(task, options = {}) {
  const { label = null, url = AILAB_URL, waitFor = SEL.searchInput, timeout = TIMEOUTS.nav } = options;
  if (!task) fail('AILAB_NO_TASK', 'ensurePage 需要 TaskSpace 句柄（先 await taskSpace(name)）');

  let pages = [];
  try {
    pages = await task.pages();
  } catch (err) {
    pages = [];
  }
  const candidates = label ? pages.filter((p) => p && p.label === label) : pages.filter(Boolean);
  let page = null;
  let reused = false;
  for (const cand of candidates) {
    try {
      await cand.url();
      page = cand;
      reused = true;
      break;
    } catch (err) {
      // 标签已被关掉：句柄还在，但操作会报错，跳过
    }
  }
  let created = false;
  if (!page) {
    try {
      page = await task.newPage();
      created = true;
    } catch (err) {
      fail('AILAB_PAGE_CREATE_FAILED', '新建页面失败，先关掉一个 pN 标签再跑', {
        cause: err && err.message,
      });
    }
  }
  let reloaded = false;
  if (url) {
    let currentUrl = '';
    try {
      currentUrl = await page.url();
    } catch (err) {
      currentUrl = '';
    }
    try {
      if (currentUrl && currentUrl === url) {
        // 同一个 hash URL：goto 不会重载 SPA，上一轮开着的 drawer / 弹窗会留在页面上盖住列表
        await page.reload({ timeout });
        reloaded = true;
      } else {
        await page.goto(url, { timeout });
      }
    } catch (err) {
      fail('AILAB_NAV_FAILED', '打不开 ' + url + '（检查登录态 / 网络 / 代理）', {
        cause: err && err.message,
      });
    }
  }
  if (waitFor) {
    try {
      await waitForVisible(page, waitFor, { timeout });
    } catch (err) {
      fail('AILAB_PAGE_READY_TIMEOUT', '页面打开后 ' + timeout + 'ms 内没等到「' + waitFor + '」可见', {
        url,
        cause: err && err.message,
      });
    }
  }
  return { page, label: page.label, reused, created, reloaded };
}

/**
 * 关闭一个页面（任务收尾或清理现场用）。
 * 入参: page
 * 返回: true / false（失败不抛错）
 */
export async function closePage(page) {
  try {
    await page.close();
    return true;
  } catch (err) {
    return false;
  }
}

/**
 * 在模型列表页搜索关键词并等结果渲染完（等 loading mask 消失 + 有数据行）。
 * 入参: page, keyword, options { timeout, expectRows=1 }
 * 返回: { keyword, rows, firstRow }
 * 失败:
 *   [AILAB_SEARCH_KEYWORD_MISSING] 搜索词为空
 *   [AILAB_SEARCH_INPUT_MISSING]   找不到搜索框 / 查询按钮
 *   [AILAB_SEARCH_EMPTY]          搜索后列表 0 行
 *   [AILAB_SEARCH_TIMEOUT]        等 <timeout>ms 列表仍在 loading 或行数不足
 */
export async function searchModel(page, keyword, options = {}) {
  const { timeout = TIMEOUTS.list, expectRows = 1 } = options;
  const word = String(keyword == null ? '' : keyword).trim();
  if (!word) fail('AILAB_SEARCH_KEYWORD_MISSING', '搜索词为空：传一个模型 ID 片段或模型名称，如 2619');

  try {
    await page.fill(SEL.searchInput, word);
  } catch (err) {
    fail('AILAB_SEARCH_INPUT_MISSING', '找不到搜索框 ' + SEL.searchInput + '（页面没加载完或已被弹窗盖住）', {
      cause: err && err.message,
    });
  }
  const clicked = await page.evaluate((labelText) => {
    const btns = [...document.querySelectorAll('button')].filter((b) => {
      const b2 = b.getBoundingClientRect();
      return b2.width > 0 && b2.height > 0;
    });
    const btn = btns.find((b) => (b.textContent || '').trim() === labelText);
    if (!btn) return false;
    btn.click();
    return true;
  }, TEXT.query);
  if (!clicked) fail('AILAB_SEARCH_INPUT_MISSING', '页面上没有可见的「查询」按钮');

  const maskVisible = () => {
    return [...document.querySelectorAll('.el-loading-mask')].some((m) => {
      const b = m.getBoundingClientRect();
      return b.width > 0 && b.height > 0 && getComputedStyle(m).display !== 'none';
    });
  };
  // 先等 loading mask 出现（最多 3s；查询很快时可能抓不到，抓不到不算失败）
  try {
    await page.waitForFunction(
      () => {
        return [...document.querySelectorAll('.el-loading-mask')].some((m) => {
          const b = m.getBoundingClientRect();
          return b.width > 0 && b.height > 0 && getComputedStyle(m).display !== 'none';
        });
      },
      undefined,
      { timeout: 3000, polling: 100 },
    );
  } catch (err) {
    // 响应太快/表格没走 loading，继续走下面的稳定判定
  }

  const needKeyword = word.length <= 20;
  try {
    await page.waitForFunction(
      ({ expectRows, word, needKeyword }) => {
        const mask = [...document.querySelectorAll('.el-loading-mask')].some((m) => {
          const b = m.getBoundingClientRect();
          return b.width > 0 && b.height > 0 && getComputedStyle(m).display !== 'none';
        });
        if (mask) return false;
        const vis = (el) => {
          const b = el.getBoundingClientRect();
          return b.width > 0 && b.height > 0;
        };
        const tables = [...document.querySelectorAll('.el-table')].filter(vis);
        const table = tables.find((t) => [...t.querySelectorAll('.el-table__header th')].some((th) => (th.textContent || '').trim() === '模型ID')) || tables[0];
        if (!table) return false;
        const rows = [...table.querySelectorAll('.el-table__body tr')].filter(vis);
        if (rows.length < expectRows) return false;
        if (!needKeyword) return true;
        return rows.some((r) => (r.textContent || '').replace(/\s+/g, '').includes(word));
      },
      { expectRows, word, needKeyword },
      { timeout, polling: 200 },
    );
  } catch (err) {
    const snapshot = await page.evaluate(() => {
      const vis = (el) => {
        const b = el.getBoundingClientRect();
        return b.width > 0 && b.height > 0;
      };
      const tables = [...document.querySelectorAll('.el-table')].filter(vis);
      const table = tables.find((t) => [...t.querySelectorAll('.el-table__header th')].some((th) => (th.textContent || '').trim() === '模型ID')) || tables[0];
      const rows = table ? [...table.querySelectorAll('.el-table__body tr')].filter(vis) : [];
      return { rows: rows.length, firstRow: rows.length ? (rows[0].textContent || '').replace(/\s+/g, ' ').trim().slice(0, 120) : null };
    });
    if (snapshot.rows === 0) {
      fail('AILAB_SEARCH_EMPTY', '搜索「' + word + '」后列表 0 行：换个搜索词，或确认模型不在回收站', { keyword: word });
    }
    fail(
      'AILAB_SEARCH_TIMEOUT',
      '等 ' + timeout + 'ms 列表还没稳定成搜索结果（仍有 loading，或可见行里没有包含「' + word + '」的行）；当前 ' +
        snapshot.rows + ' 行，首行：' + (snapshot.firstRow || ''),
      { keyword: word, rows: snapshot.rows, firstRow: snapshot.firstRow },
    );
  }
  const snapshot = await page.evaluate(() => {
    const vis = (el) => {
      const b = el.getBoundingClientRect();
      return b.width > 0 && b.height > 0;
    };
    const tables = [...document.querySelectorAll('.el-table')].filter(vis);
    const table = tables.find((t) => [...t.querySelectorAll('.el-table__header th')].some((th) => (th.textContent || '').trim() === '模型ID')) || tables[0];
    const rows = table ? [...table.querySelectorAll('.el-table__body tr')].filter(vis) : [];
    return {
      rows: rows.length,
      firstRow: rows.length ? (rows[0].textContent || '').replace(/\s+/g, ' ').trim().slice(0, 200) : null,
    };
  });
  return { keyword: word, rows: snapshot.rows, firstRow: snapshot.firstRow };
}
