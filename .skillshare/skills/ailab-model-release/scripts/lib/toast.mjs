import { TIMEOUTS } from './constants.mjs';
import { fail } from './errors.mjs';

/**
 * 装上 toast 记录器（MutationObserver）：toast 只活 2~3 秒，动作前装好才不会漏。
 * 入参: page
 * 返回: { installed: true, existed: boolean }
 * 失败: 不抛错。
 */
export async function startToastRecorder(page) {
  return page.evaluate(() => {
    if (window.__ailabToastObserver) return { installed: true, existed: true };
    window.__ailabToastLog = [];
    const grab = () => {
      const nodes = [...document.querySelectorAll('.el-message, .el-notification, .el-message-box')];
      for (const node of nodes) {
        const text = (node.textContent || '').replace(/\s+/g, ' ').trim();
        if (!text) continue;
        const kind = node.classList.contains('el-message') ? 'message' : node.classList.contains('el-notification') ? 'notification' : 'message-box';
        if (window.__ailabToastLog.some((x) => x.text === text && x.kind === kind)) continue;
        window.__ailabToastLog.push({ text, kind, t: Date.now() });
      }
    };
    grab();
    const mo = new MutationObserver(grab);
    mo.observe(document.body, { childList: true, subtree: true, characterData: true });
    window.__ailabToastObserver = mo;
    return { installed: true, existed: false };
  });
}

/**
 * 清空已记录的 toast（动作前调一次，后面只看新出现的）。
 * 入参: page
 * 返回: { cleared: true }
 */
export async function clearToasts(page) {
  return page.evaluate(() => {
    if (!window.__ailabToastObserver) {
      window.__ailabToastLog = [];
      return { cleared: true, recorder: false };
    }
    window.__ailabToastLog = [];
    return { cleared: true, recorder: true };
  });
}

/**
 * 读已记录的 toast（含 time 时间戳与 age 毫秒）。
 * 入参: page
 * 返回: [{ text, kind, t, age }]
 * 失败: 不抛错。
 */
export async function readToasts(page) {
  return page.evaluate(() => {
    const now = Date.now();
    return (window.__ailabToastLog || []).map((x) => ({ ...x, age: now - x.t }));
  });
}

/**
 * 等一条 toast，并断言文案。
 * 入参: page, options { expect=null|string|string[], timeout=8000, interval=150 }（expect 是子串匹配，数组表示任一命中即可）
 * 返回: { text, kind, all }
 * 失败:
 *   [AILAB_TOAST_TIMEOUT]  等 <timeout>ms 没有出现任何 toast
 *   [AILAB_TOAST_MISMATCH] toast 文案「x」与预期「y」不符；本次出现：…
 */
export async function waitForToast(page, options = {}) {
  const { expect = null, timeout = TIMEOUTS.toast, interval = 150 } = options;
  const wants = expect == null ? [] : Array.isArray(expect) ? expect.map(String) : [String(expect)];
  const deadline = Date.now() + timeout;
  let log = [];
  while (Date.now() < deadline) {
    log = await readToasts(page);
    if (wants.length) {
      const hit = log.find((x) => wants.some((w) => (x.text || '').includes(w)));
      if (hit) return { text: hit.text, kind: hit.kind, all: log.map((x) => x.text) };
    } else if (log.length) {
      return { text: log[0].text, kind: log[0].kind, all: log.map((x) => x.text) };
    }
    await page.waitForTimeout(interval);
  }
  if (log.length) {
    fail(
      'AILAB_TOAST_MISMATCH',
      'toast 文案与预期「' + wants.join(' / ') + '」不符；本次出现：' + log.map((x) => '[' + x.kind + ']' + x.text).join(' | '),
      { expect, toasts: log },
    );
  }
  fail('AILAB_TOAST_TIMEOUT', '等 ' + timeout + 'ms 没有出现任何 toast（.el-message / .el-notification）；检查动作是否真的触发、或 toast 是否被更快的页面跳转冲掉', {
    expect,
  });
}
