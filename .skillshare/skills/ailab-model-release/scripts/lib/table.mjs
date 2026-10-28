import { SEL, TIMEOUTS } from './constants.mjs';
import { fail } from './errors.mjs';

const ROOT_SNIPPET = ''; // 说明：resolveRoot 逻辑在每个 evaluate 内单独实现（page.evaluate 只序列化函数体）

/**
 * 读一个 el-table（按表头签名或 table-title 定位）。
 * 入参:
 *   page
 *   options.scope        限定根节点选择器（默认 .el-drawer）
 *   options.rootTitle    改为「标题含该文本的可见弹窗/抽屉」做根
 *   options.headerKey    表头里必须全部出现的列名，如 ['Run ID', 'Experiment 名称']
 *   options.titleContains 用 table.title 文本定位（如 '输入参数'）
 *   options.maxRows      最多返回多少行（默认 50）
 * 返回: { headers, rows: string[][], rowCount }
 * 失败: [AILAB_TABLE_NOT_FOUND] 没找到表（scope / 表头 / 标题都不匹配）；当前可见表头：…
 */
export async function readTable(page, options = {}) {
  const { scope = SEL.drawer, rootTitle = null, headerKey = null, titleContains = null, maxRows = 50 } = options;
  const res = await page.evaluate(
    ({ scope, rootTitle, headerKey, titleContains, maxRows }) => {
      let root = null;
      if (rootTitle) {
        const conts = [...document.querySelectorAll('.el-dialog, .el-drawer')].filter((d) => {
          const b = d.getBoundingClientRect();
          return b.width > 0 && b.height > 0;
        });
        root = conts
          .filter((d) => ((d.querySelector('.el-dialog__title, .el-drawer__title') || {}).textContent || '').includes(rootTitle))
          .pop() || null;
      }
      if (!root) root = (function(){const list=[...document.querySelectorAll(scope)];if(!list.length)return document.body;const vis=list.filter(function(el){const b=el.getBoundingClientRect();return b.width>0&&b.height>0;});return vis.length?vis[vis.length-1]:list[list.length-1];})();
      if (!root) return { ok: false, headersList: [] };
      const visible = (el) => {
        const b = el.getBoundingClientRect();
        return b.width > 0 && b.height > 0;
      };
      const headersOf = (t) =>
        [...t.querySelectorAll('.el-table__header th')].map((th) => (th.textContent || '').trim()).filter(Boolean);
      const tables = [...root.querySelectorAll('.el-table')].filter(visible);
      const headersList = tables.map(headersOf);
      let table = null;
      if (headerKey && headerKey.length) {
        table = tables.find((t) => {
          const hs = headersOf(t);
          return headerKey.every((k) => hs.includes(k));
        }) || null;
      }
      if (!table && titleContains) {
        const titles = [...root.querySelectorAll('.table-title')].filter((t) => (t.textContent || '').includes(titleContains));
        for (const t of titles) {
          // 4 = Node.DOCUMENT_POSITION_FOLLOWING：找文档顺序里位于标题之后的第一个表
          const next = tables.find((tb) => (t.compareDocumentPosition(tb) & 4) !== 0);
          if (next) {
            table = next;
            break;
          }
        }
      }
      if (!table) table = tables[0] || null;
      if (!table) return { ok: false, headersList };
      const headers = headersOf(table);
      const allRows = [...table.querySelectorAll('.el-table__body tr')].filter(visible);
      const rows = allRows.slice(0, maxRows).map((tr) =>
        [...tr.querySelectorAll('td')].map((td) => (td.textContent || '').replace(/\s+/g, ' ').trim()),
      );
      return { ok: true, headers, rows, rowCount: allRows.length, headersList };
    },
    { scope, rootTitle, headerKey, titleContains, maxRows },
  );
  if (!res.ok) {
    fail(
      'AILAB_TABLE_NOT_FOUND',
      '没找到表（scope=' + (rootTitle || scope) + (headerKey ? '，表头需含 ' + headerKey.join('+') : '') +
        (titleContains ? '，标题含「' + titleContains + '」' : '') + '）；当前可见表头：' +
        (res.headersList.length ? res.headersList.map((h) => h.join('|')).join(' ; ') : '（无可见表）'),
      { scope, rootTitle, headerKey, titleContains },
    );
  }
  return { headers: res.headers, rows: res.rows, rowCount: res.rowCount };
}

/**
 * 点某一行里的单选 / 复选框（Run 列表、部署文件选择弹窗都用它）。
 * 入参: page, options { scope='.el-drawer', rootTitle=null, matchText, timeout=8000 }
 * 返回: { rowText, clicked }
 * 失败:
 *   [AILAB_ROW_NOT_FOUND]   等 <timeout>ms 仍没有包含「x」的行（弹窗刚开时表格还在异步加载）
 *   [AILAB_ROW_NO_RADIO]    该行没有单选/复选框
 *   [AILAB_ROW_NOT_SELECTED] 点了但 <timeout>ms 后仍未呈选中态
 */
export async function clickRowRadio(page, options = {}) {
  const { scope = SEL.drawer, rootTitle = null, matchText, timeout = TIMEOUTS.action } = options;
  const want = String(matchText == null ? '' : matchText).trim();
  if (!want) fail('AILAB_ROW_NOT_FOUND', 'clickRowRadio 的 matchText 为空');

  const locateAndClick = () =>
    page.evaluate(
      ({ scope, rootTitle, want }) => {
        let root = null;
        if (rootTitle) {
          const conts = [...document.querySelectorAll('.el-dialog, .el-drawer')].filter((d) => {
            const b = d.getBoundingClientRect();
            return b.width > 0 && b.height > 0;
          });
          root = conts
            .filter((d) => ((d.querySelector('.el-dialog__title, .el-drawer__title') || {}).textContent || '').includes(rootTitle))
            .pop() || null;
        }
        if (!root) root = scope ? document.querySelector(scope) : document.body;
        if (!root) return { ok: false, reason: 'no-root' };
        const rows = [...root.querySelectorAll('.el-table__body tr')].filter((r) => {
          const b = r.getBoundingClientRect();
          return b.width > 0 && b.height > 0;
        });
        const row = rows.find((r) => (r.textContent || '').replace(/\s+/g, ' ').includes(want));
        if (!row) {
          return {
            ok: false,
            reason: 'no-row',
            rows: rows.map((r) => (r.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 80)),
          };
        }
        const cue =
          row.querySelector('.el-radio__inner') ||
          row.querySelector('.el-checkbox__inner') ||
          row.querySelector('input[type="radio"]') ||
          row.querySelector('input[type="checkbox"]') ||
          row.querySelector('.el-radio') ||
          row.querySelector('.el-checkbox');
        if (!cue) {
          return { ok: false, reason: 'no-radio', rowText: (row.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 120) };
        }
        cue.click();
        const tag = cue.tagName + (cue.className ? '.' + String(cue.className).split(' ')[0] : '');
        return { ok: true, clicked: tag, rowText: (row.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 120) };
      },
      { scope, rootTitle, want },
    );

  // 弹窗刚打开时表格数据还在异步加载，行可能是空的：轮询等行出现
  const deadline = Date.now() + timeout;
  let res = null;
  while (Date.now() < deadline) {
    res = await locateAndClick();
    if (res.ok) break;
    if (res.reason === 'no-radio') {
      fail('AILAB_ROW_NO_RADIO', '行「' + want + '」里没有单选/复选框：' + res.rowText, { matchText: want });
    }
    res = null;
    await page.waitForTimeout(250);
  }
  if (!res || !res.ok) {
    fail(
      'AILAB_ROW_NOT_FOUND',
      '等 ' + timeout + 'ms 表里仍没有包含「' + want + '」的行（范围：' + (rootTitle || scope) + '）',
      { matchText: want, scope, rootTitle },
    );
  }

  const verifyDeadline = Date.now() + timeout;
  while (Date.now() < verifyDeadline) {
    const selected = await page.evaluate(
      ({ scope, rootTitle, want }) => {
        let root = null;
        if (rootTitle) {
          const conts = [...document.querySelectorAll('.el-dialog, .el-drawer')].filter((d) => {
            const b = d.getBoundingClientRect();
            return b.width > 0 && b.height > 0;
          });
          root = conts
            .filter((d) => ((d.querySelector('.el-dialog__title, .el-drawer__title') || {}).textContent || '').includes(rootTitle))
            .pop() || null;
        }
        if (!root) root = scope ? document.querySelector(scope) : document.body;
        if (!root) return false;
        const row = [...root.querySelectorAll('.el-table__body tr')].find((r) =>
          (r.textContent || '').replace(/\s+/g, ' ').includes(want),
        );
        if (!row) return false;
        return !!row.querySelector(
          'input[type="radio"]:checked, input[type="checkbox"]:checked, .el-radio.is-checked, .el-checkbox.is-checked',
        );
      },
      { scope, rootTitle, want },
    );
    if (selected) return { rowText: res.rowText, clicked: res.clicked };
    await page.waitForTimeout(150);
  }
  fail('AILAB_ROW_NOT_SELECTED', '点了「' + want + '」那一行的' + res.clicked + '，但 ' + timeout + 'ms 后仍未呈选中态', {
    matchText: want,
    clicked: res.clicked,
  });
}
