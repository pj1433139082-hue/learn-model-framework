import { TIMEOUTS } from './constants.mjs';
import { fail } from './errors.mjs';

/**
 * 取元素 rect（视口坐标，CSS 像素），可选先 scrollIntoView。
 * 入参:
 *   page             ego-browser Page
 *   selector         CSS 选择器
 *   options.index    第几个匹配（默认 0）
 *   options.scope    限定根节点选择器（默认 document）
 *   options.scrollIntoView 是否先滚到视口中间（默认 false）
 * 返回: { x, y, w, h, cx, cy, text, total, visible } | null（无匹配）
 * 失败: 不抛错，无匹配返回 null。
 */
export async function rectOf(page, selector, options = {}) {
  const { index = 0, scope = null, scrollIntoView = false } = options;
  return page.evaluate(
    ({ selector, index, scope, scrollIntoView }) => {
      const root = (function(){const list=[...document.querySelectorAll(scope)];if(!list.length)return document;const vis=list.filter(function(el){const b=el.getBoundingClientRect();return b.width>0&&b.height>0;});return vis.length?vis[vis.length-1]:list[list.length-1];})();
      if (!root) return null;
      const list = [...root.querySelectorAll(selector)];
      const el = list[index];
      if (!el) return null;
      if (scrollIntoView) el.scrollIntoView({ block: 'center', inline: 'center' });
      const b = el.getBoundingClientRect();
      return {
        x: Math.round(b.x),
        y: Math.round(b.y),
        w: Math.round(b.width),
        h: Math.round(b.height),
        cx: Math.round(b.x + b.width / 2),
        cy: Math.round(b.y + b.height / 2),
        text: (el.textContent || '').trim().slice(0, 120),
        total: list.length,
        visible: b.width > 0 && b.height > 0,
      };
    },
    { selector, index, scope, scrollIntoView },
  );
}

/**
 * 按 rect 判可见（teleport 到 body 的 popper 用 offsetParent 判永远为 null）。
 * 入参: page, selector, options { index, scope }
 * 返回: boolean
 * 失败: 不抛错。
 */
export async function isVisibleByRect(page, selector, options = {}) {
  const r = await rectOf(page, selector, options);
  return !!(r && r.w > 0 && r.h > 0);
}

/**
 * 等到某个元素 rect 宽高 > 0 且 display/visibility 正常。
 * 入参: page, selector, options { index, scope, timeout }
 * 返回: true
 * 失败: [AILAB_VISIBLE_TIMEOUT] 等 <timeout>ms 仍没有 rect 宽高 > 0 的「<selector>」（scope=<scope>）
 */
export async function waitForVisible(page, selector, options = {}) {
  const { index = 0, scope = null, timeout = TIMEOUTS.action } = options;
  try {
    await page.waitForFunction(
      ({ selector, scope, index }) => {
        const root = (function(){const list=[...document.querySelectorAll(scope)];if(!list.length)return document;const vis=list.filter(function(el){const b=el.getBoundingClientRect();return b.width>0&&b.height>0;});return vis.length?vis[vis.length-1]:list[list.length-1];})();
        if (!root) return false;
        const el = [...root.querySelectorAll(selector)][index];
        if (!el) return false;
        const b = el.getBoundingClientRect();
        if (!(b.width > 0 && b.height > 0)) return false;
        const st = getComputedStyle(el);
        return st.display !== 'none' && st.visibility !== 'hidden';
      },
      { selector, scope, index },
      { timeout, polling: 150 },
    );
    return true;
  } catch (err) {
    fail(
      'AILAB_VISIBLE_TIMEOUT',
      '等 ' + timeout + 'ms 仍没有 rect 宽高 > 0 的「' + selector + '」（scope=' + (scope || 'document') + '）',
      { selector, scope, index, cause: err && err.message },
    );
  }
}

/**
 * 等元素消失（rect 宽高归零）。
 * 入参: page, selector, options { index, scope, timeout }
 * 返回: true
 * 失败: [AILAB_VISIBLE_TIMEOUT] 等 <timeout>ms 后「<selector>」仍然可见
 */
export async function waitForGone(page, selector, options = {}) {
  const { index = 0, scope = null, timeout = TIMEOUTS.action } = options;
  try {
    await page.waitForFunction(
      ({ selector, scope, index }) => {
        const root = (function(){const list=[...document.querySelectorAll(scope)];if(!list.length)return document;const vis=list.filter(function(el){const b=el.getBoundingClientRect();return b.width>0&&b.height>0;});return vis.length?vis[vis.length-1]:list[list.length-1];})();
        if (!root) return true;
        const el = [...root.querySelectorAll(selector)][index];
        if (!el) return true;
        const b = el.getBoundingClientRect();
        return !(b.width > 0 && b.height > 0);
      },
      { selector, scope, index },
      { timeout, polling: 150 },
    );
    return true;
  } catch (err) {
    fail('AILAB_VISIBLE_TIMEOUT', '等 ' + timeout + 'ms 后「' + selector + '」仍然可见', {
      selector,
      scope,
      cause: err && err.message,
    });
  }
}

/**
 * 读某个元素的可见文本（走 textContent，会保留隐藏子节点文本，慎用）。
 * 入参: page, selector, options { index, scope, maxLength=200 }
 * 返回: string（无匹配返回 ''）
 * 失败: 不抛错。
 */
export async function textOf(page, selector, options = {}) {
  const { index = 0, scope = null, maxLength = 200 } = options;
  const value = await page.evaluate(
    ({ selector, index, scope, maxLength }) => {
      const root = (function(){const list=[...document.querySelectorAll(scope)];if(!list.length)return document;const vis=list.filter(function(el){const b=el.getBoundingClientRect();return b.width>0&&b.height>0;});return vis.length?vis[vis.length-1]:list[list.length-1];})();
      if (!root) return '';
      const el = [...root.querySelectorAll(selector)][index];
      if (!el) return '';
      return (el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, maxLength);
    },
    { selector, index, scope, maxLength },
  );
  return value || '';
}

/**
 * 读 body（或 scope）里可见的大段文本，用于精确断言的兜底证据。
 * 入参: page, options { scope, maxLength=1500 }
 * 返回: string
 * 失败: 不抛错。
 */
export async function visibleText(page, options = {}) {
  const { scope = null, maxLength = 1500 } = options;
  const value = await page.evaluate(
    ({ scope, maxLength }) => {
      const root = (function(){const list=[...document.querySelectorAll(scope)];if(!list.length)return document.body;const vis=list.filter(function(el){const b=el.getBoundingClientRect();return b.width>0&&b.height>0;});return vis.length?vis[vis.length-1]:list[list.length-1];})();
      if (!root) return '';
      return (root.textContent || '').replace(/\s+/g, ' ').trim().slice(0, maxLength);
    },
    { scope, maxLength },
  );
  return value || '';
}

/**
 * 等页面文本包含某个片段（用于「已选择绑定 Run ID」这类状态行）。
 * 入参: page, snippet, options { scope, timeout }
 * 返回: true
 * 失败: [AILAB_TEXT_TIMEOUT] 等 <timeout>ms 页面文本里没有「<snippet>」（scope=<scope>）
 */
export async function waitForText(page, snippet, options = {}) {
  const { scope = null, timeout = TIMEOUTS.action } = options;
  try {
    await page.waitForFunction(
      ({ scope, snippet }) => {
        const root = (function(){const list=[...document.querySelectorAll(scope)];if(!list.length)return document.body;const vis=list.filter(function(el){const b=el.getBoundingClientRect();return b.width>0&&b.height>0;});return vis.length?vis[vis.length-1]:list[list.length-1];})();
        if (!root) return false;
        return (root.textContent || '').replace(/\s+/g, ' ').includes(snippet);
      },
      { scope, snippet },
      { timeout, polling: 150 },
    );
    return true;
  } catch (err) {
    fail('AILAB_TEXT_TIMEOUT', '等 ' + timeout + 'ms 页面文本里没有「' + snippet + '」', {
      scope,
      cause: err && err.message,
    });
  }
}
