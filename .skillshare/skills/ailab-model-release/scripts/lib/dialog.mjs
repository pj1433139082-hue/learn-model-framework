import { SEL, TIMEOUTS } from './constants.mjs';
import { fail } from './errors.mjs';

/**
 * 列出页面上所有弹窗 / 抽屉（含不可见的），用于排错与断言。
 * 入参: page
 * 返回: [{ kind:'dialog'|'drawer', title, visible, rect, buttons }]
 * 失败: 不抛错。
 */
export async function listContainers(page) {
  return page.evaluate(() => {
    const rectOf = (el) => {
      const b = el.getBoundingClientRect();
      return { x: Math.round(b.x), y: Math.round(b.y), w: Math.round(b.width), h: Math.round(b.height) };
    };
    return [...document.querySelectorAll('.el-dialog, .el-drawer')].map((d) => {
      const r = rectOf(d);
      return {
        kind: d.classList.contains('el-drawer') ? 'drawer' : 'dialog',
        title: ((d.querySelector('.el-dialog__title, .el-drawer__title') || {}).textContent || '').trim(),
        visible: r.w > 0 && r.h > 0,
        rect: r,
        buttons: [...d.querySelectorAll('button')]
          .map((b) => (b.textContent || '').trim())
          .filter(Boolean)
          .slice(0, 40),
      };
    });
  });
}

async function findContainer(page, { titleContains = null, kind = null }) {
  return page.evaluate(
    ({ titleContains, kind }) => {
      const all = [...document.querySelectorAll('.el-dialog, .el-drawer')].map((d) => {
        const b = d.getBoundingClientRect();
        const isDrawer = d.classList.contains('el-drawer');
        return {
          kind: isDrawer ? 'drawer' : 'dialog',
          title: ((d.querySelector('.el-dialog__title, .el-drawer__title') || {}).textContent || '').trim(),
          visible: b.width > 0 && b.height > 0,
          w: Math.round(b.width),
          h: Math.round(b.height),
          buttons: [...d.querySelectorAll('button')].map((x) => (x.textContent || '').trim()).filter(Boolean),
        };
      });
      const matches = all.filter((c) => {
        if (!c.visible) return false;
        if (kind && c.kind !== kind) return false;
        if (titleContains && !c.title.includes(titleContains)) return false;
        return true;
      });
      return { hit: matches.length ? matches[matches.length - 1] : null, visible: all.filter((c) => c.visible) };
    },
    { titleContains, kind },
  );
}

/**
 * 等一个可见弹窗/抽屉出现。
 * 入参: page, options { titleContains=null, kind='dialog'|'drawer'|null, timeout=15000 }
 * 返回: { kind, title, rect, buttons }
 * 失败: [AILAB_CONTAINER_TIMEOUT] 等 <timeout>ms 没等到标题含「x」的可见弹窗；当前可见：…
 */
export async function waitForContainer(page, options = {}) {
  const { titleContains = null, kind = null, timeout = TIMEOUTS.dialog } = options;
  const deadline = Date.now() + timeout;
  let last = null;
  while (Date.now() < deadline) {
    last = await findContainer(page, { titleContains, kind });
    if (last.hit) return last.hit;
    await page.waitForTimeout(200);
  }
  const visibleDesc = (last && last.visible && last.visible.length)
    ? last.visible.map((c) => c.kind + '(' + (c.title || '无标题') + ')').join(' / ')
    : '（没有可见弹窗）';
  fail(
    'AILAB_CONTAINER_TIMEOUT',
    '等 ' + timeout + 'ms 没等到' + (kind ? kind : '弹窗/抽屉') +
      (titleContains ? '（标题含「' + titleContains + '」）' : '') + '；当前可见：' + visibleDesc,
    { titleContains, kind },
  );
}

/**
 * 等一个弹窗/抽屉消失（rect 归零）。
 * 入参: page, options { titleContains=null, kind=null, timeout=10000 }
 * 返回: true
 * 失败: [AILAB_CONTAINER_TIMEOUT] 等 <timeout>ms 「x」仍然开着
 */
export async function waitForContainerGone(page, options = {}) {
  const { titleContains = null, kind = null, timeout = TIMEOUTS.action } = options;
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const last = await findContainer(page, { titleContains, kind });
    if (!last.hit) return true;
    await page.waitForTimeout(200);
  }
  fail('AILAB_CONTAINER_TIMEOUT', '等 ' + timeout + 'ms「' + (titleContains || kind || '弹窗') + '」仍然开着', {
    titleContains,
    kind,
  });
}

/**
 * 点某个弹窗里的按钮（如「另存为新版本」「确定选择」）。
 * 入参: page, options { titleContains, buttonText, kind=null, timeout=10000 }
 * 返回: { title, buttonText, allButtons }
 * 失败:
 *   [AILAB_CONTAINER_TIMEOUT]   弹窗没出现（见 waitForContainer）
 *   [AILAB_BUTTON_NOT_FOUND]    弹窗「x」里没有可点的按钮「y」；现有：…
 */
export async function clickContainerButton(page, options = {}) {
  const { titleContains = null, buttonText, kind = null, timeout = TIMEOUTS.dialog } = options;
  const want = String(buttonText == null ? '' : buttonText).trim();
  if (!want) fail('AILAB_BUTTON_NOT_FOUND', 'clickContainerButton 的 buttonText 为空');

  const deadline = Date.now() + timeout;
  let last = null;
  while (Date.now() < deadline) {
    last = await page.evaluate(
      ({ titleContains, kind, want }) => {
        const conts = [...document.querySelectorAll('.el-dialog, .el-drawer')].map((d) => {
          const b = d.getBoundingClientRect();
          const isDrawer = d.classList.contains('el-drawer');
          return {
            el: d,
            isDrawer,
            title: ((d.querySelector('.el-dialog__title, .el-drawer__title') || {}).textContent || '').trim(),
            visible: b.width > 0 && b.height > 0,
          };
        }).filter((c) => c.visible && (!kind || (kind === 'drawer' ? c.isDrawer : !c.isDrawer)) && (!titleContains || c.title.includes(titleContains)));

        const titles = conts.map((c) => c.title || (c.isDrawer ? '无标题抽屉' : '无标题弹窗'));
        for (const c of conts.reverse()) {
          const buttons = [...c.el.querySelectorAll('button')].filter((b) => {
            const bb = b.getBoundingClientRect();
            return bb.width > 0 && bb.height > 0;
          });
          const btn = buttons.find((b) => (b.textContent || '').trim() === want);
          if (btn) {
            btn.click();
            return { ok: true, title: c.title, buttonText: want, allButtons: buttons.map((b) => (b.textContent || '').trim()).filter(Boolean) };
          }
        }
        return { ok: false, titles, allButtons: [] };
      },
      { titleContains, kind, want },
    );
    if (last.ok) return last;
    await page.waitForTimeout(250);
  }
  const listRes = await listContainers(page);
  fail(
    'AILAB_BUTTON_NOT_FOUND',
    '在' + (titleContains ? '弹窗「' + titleContains + '」' : '可见弹窗/抽屉') + '里找不到可点的按钮「' + want +
      '」；当前可见：' + (listRes.filter((c) => c.visible).map((c) => (c.title || c.kind) + '[' + c.buttons.join(',') + ']').join(' / ') || '（无）'),
    { titleContains, buttonText: want },
  );
}

/**
 * 在指定容器（或整页）里点一个可见按钮，用于 drawer 里的「切换部署文件」「同步输入输出参数」「取消」。
 * 入参: page, buttonText, options { scope='.el-drawer', rootTitle=null, timeout=10000 }
 * 返回: { buttonText, from }
 * 失败:
 *   [AILAB_BUTTON_HIDDEN]     按钮存在但不可见（在别的 tab 面板里，先 clickTab）
 *   [AILAB_BUTTON_NOT_FOUND]  没找到按钮「x」；当前可见按钮：…
 */
export async function clickButton(page, buttonText, options = {}) {
  const { scope = SEL.drawer, rootTitle = null, timeout = TIMEOUTS.action } = options;
  const want = String(buttonText == null ? '' : buttonText).trim();
  if (!want) fail('AILAB_BUTTON_NOT_FOUND', 'clickButton 的 buttonText 为空');
  const deadline = Date.now() + timeout;
  let last = null;
  while (Date.now() < deadline) {
    last = await page.evaluate(
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
        if (!root) root = (function(){const list=[...document.querySelectorAll(scope)];if(!list.length)return document.body;const vis=list.filter(function(el){const b=el.getBoundingClientRect();return b.width>0&&b.height>0;});return vis.length?vis[vis.length-1]:list[list.length-1];})();
        if (!root) return { ok: false, buttons: [] };
        const all = [...root.querySelectorAll('button')].map((b) => {
          const bb = b.getBoundingClientRect();
          return { el: b, text: (b.textContent || '').trim(), visible: bb.width > 0 && bb.height > 0 };
        }).filter((x) => x.text);
        const buttons = all.filter((x) => x.visible);
        const btn = buttons.find((x) => x.text === want);
        if (!btn) {
          const hiddenHit = all.some((x) => !x.visible && x.text === want);
          return {
            ok: false,
            buttons: buttons.map((x) => x.text),
            hiddenHit,
            pane: (() => {
              const all2 = all.filter((x) => x.text === want);
              if (!all2.length) return null;
              let el = all2[0].el;
              for (let i = 0; i < 10 && el; i += 1) {
                if (el.classList && el.classList.contains('el-tab-pane')) {
                  const tabs = [...root.querySelectorAll('.el-tabs__item')].map((t) => (t.textContent || '').trim());
                  return { paneHidden: getComputedStyle(el).display === 'none', tabs };
                }
                el = el.parentElement;
              }
              return null;
            })(),
          };
        }
        btn.el.click();
        return { ok: true, buttons: buttons.map((x) => x.text) };
      },
      { scope, rootTitle, want },
    );
    if (last.ok) return { buttonText: want, from: rootTitle || scope };
    await page.waitForTimeout(250);
  }
  if (last && last.hiddenHit) {
    const pane = last.pane;
    fail(
      'AILAB_BUTTON_HIDDEN',
      '按钮「' + want + '」在 DOM 里存在但不可见（rect 0×0）' +
        (pane && pane.paneHidden
          ? '：它属于一个 display:none 的 tab 面板，当前可见 tab 有 ' + (pane.tabs || []).join(' / ') + '，先 clickTab 切到对应 tab'
          : '：多半在别的 tab 面板或折叠区里，先 clickTab 切过去') +
        '；当前可见按钮：' + ((last.buttons || []).join(' / ') || '（无）'),
      { buttonText: want, scope, rootTitle, pane },
    );
  }
  fail(
    'AILAB_BUTTON_NOT_FOUND',
    '没找到可见按钮「' + want + '」（范围：' + (rootTitle ? '弹窗「' + rootTitle + '」' : scope) + '）；当前可见按钮：' +
      ((last && last.buttons) || []).join(' / '),
    { buttonText: want, scope, rootTitle },
  );
}

/**
 * 切换版本表单里的 tab（模型信息 / 绑定部署文件 / 参数配置）。
 * 入参: page, tabText, options { scope='.el-drawer', timeout=10000 }
 * 返回: { tab, activeBefore, activeAfter }
 * 失败:
 *   [AILAB_TAB_NOT_FOUND]    drawer 里没有 tab「x」；现有 tab：…
 *   [AILAB_TAB_NOT_ACTIVE]   点了 tab 但 <timeout>ms 后它仍不是 is-active
 */
export async function clickTab(page, tabText, options = {}) {
  const { scope = SEL.drawer, timeout = TIMEOUTS.action } = options;
  const want = String(tabText == null ? '' : tabText).trim();
  const res = await page.evaluate(
    ({ scope, want }) => {
      const root = (function(){const list=[...document.querySelectorAll(scope)];if(!list.length)return document.body;const vis=list.filter(function(el){const b=el.getBoundingClientRect();return b.width>0&&b.height>0;});return vis.length?vis[vis.length-1]:list[list.length-1];})();
      const tabs = [...root.querySelectorAll('.el-tabs__item')].filter((t) => {
        const b = t.getBoundingClientRect();
        return b.width > 0 && b.height > 0;
      });
      const texts = tabs.map((t) => (t.textContent || '').trim());
      const activeBefore = (tabs.find((t) => t.classList.contains('is-active')) || {}).textContent;
      const tab = tabs.find((t) => (t.textContent || '').trim() === want) || tabs.find((t) => (t.textContent || '').trim().includes(want));
      if (!tab) return { ok: false, texts };
      if (!tab.classList.contains('is-active')) tab.click();
      return { ok: true, texts, activeBefore: (activeBefore || '').trim() };
    },
    { scope, want },
  );
  if (!res.ok) {
    fail('AILAB_TAB_NOT_FOUND', '找不到 tab「' + want + '」；现有 tab：' + ((res.texts || []).join(' / ') || '（空）'), {
      tabText: want,
      texts: res.texts,
    });
  }
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const active = await page.evaluate(
      ({ scope, want }) => {
        const root = (function(){const list=[...document.querySelectorAll(scope)];if(!list.length)return document.body;const vis=list.filter(function(el){const b=el.getBoundingClientRect();return b.width>0&&b.height>0;});return vis.length?vis[vis.length-1]:list[list.length-1];})();
        const tab = [...root.querySelectorAll('.el-tabs__item')].find((t) => (t.textContent || '').trim() === want);
        return !!(tab && tab.classList.contains('is-active'));
      },
      { scope, want },
    );
    if (active) return { tab: want, activeBefore: res.activeBefore, activeAfter: want };
    await page.waitForTimeout(150);
  }
  fail('AILAB_TAB_NOT_ACTIVE', '点了 tab「' + want + '」但 ' + timeout + 'ms 后它仍不是 is-active', { tabText: want });
}
