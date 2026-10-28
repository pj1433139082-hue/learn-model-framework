import { SEL, TIMEOUTS } from './constants.mjs';
import { fail } from './errors.mjs';

/**
 * 读一个 el-select 字段的状态（是否 is-disabled、当前值、rect）。
 * 入参:
 *   page
 *   field    表单 label 文本（如 '模型类型'）或数字下标（第几个 el-select，0 基）
 *   options.scope  限定容器，默认 .el-drawer（版本表单在 drawer 里）
 * 返回: { found, label, disabled, value, visible, rect, wrapperCls, index }
 *       找不到时返回 { found:false, labels:[...] }
 * 失败: 不抛错。
 */
export async function getSelectState(page, field, options = {}) {
  const { scope = SEL.drawer } = options;
  return page.evaluate(
    ({ scope, field }) => {
      const root = (function(){const list=[...document.querySelectorAll(scope)];if(!list.length)return document.body;const vis=list.filter(function(el){const b=el.getBoundingClientRect();return b.width>0&&b.height>0;});return vis.length?vis[vis.length-1]:list[list.length-1];})();
      const items = [...root.querySelectorAll('.el-form-item')];
      const labelOf = (it) => ((it.querySelector('.el-form-item__label') || {}).textContent || '').trim();
      let item = null;
      if (typeof field === 'number') {
        const withSelect = items.filter((it) => it.querySelector('.el-select'));
        item = withSelect[field] || null;
      } else {
        const want = String(field).trim();
        item = items.find((it) => labelOf(it) === want) || null;
        if (!item) item = items.find((it) => labelOf(it).startsWith(want)) || null;
      }
      if (!item) {
        return { found: false, labels: items.map(labelOf).filter(Boolean) };
      }
      const wrapper = item.querySelector('.el-select__wrapper');
      if (!wrapper) {
        return { found: false, label: labelOf(item), labels: items.map(labelOf).filter(Boolean), noSelect: true };
      }
      const b = wrapper.getBoundingClientRect();
      const inner = item.querySelector('.el-select');
      const input = item.querySelector('input');
      const disabled =
        wrapper.classList.contains('is-disabled') ||
        (inner ? inner.classList.contains('is-disabled') : false) ||
        (input ? input.disabled === true : false);
      return {
        found: true,
        label: labelOf(item),
        disabled,
        value: (wrapper.textContent || '').replace(/\s+/g, ' ').trim(),
        visible: b.width > 0 && b.height > 0,
        rect: {
          x: Math.round(b.x),
          y: Math.round(b.y),
          w: Math.round(b.width),
          h: Math.round(b.height),
          cx: Math.round(b.x + b.width / 2),
          cy: Math.round(b.y + b.height / 2),
        },
        wrapperCls: wrapper.className,
        index: items.indexOf(item),
      };
    },
    { scope, field },
  );
}

/**
 * 等某个 el-select 从 is-disabled 变成可用。
 * 入参: page, field, options { scope, timeout=8000, interval=200 }
 * 返回: getSelectState 的结果（disabled=false）
 * 失败: [AILAB_SELECT_ENABLE_TIMEOUT] 等待「x」可用超时（<timeout>ms）：仍是 is-disabled
 */
export async function waitForSelectEnabled(page, field, options = {}) {
  const { scope = SEL.drawer, timeout = TIMEOUTS.popper, interval = 200 } = options;
  const deadline = Date.now() + timeout;
  let state = null;
  while (Date.now() < deadline) {
    state = await getSelectState(page, field, { scope });
    if (state.found && !state.disabled) return state;
    await page.waitForTimeout(interval);
  }
  fail(
    'AILAB_SELECT_ENABLE_TIMEOUT',
    '等待「' + String(field) + '」可用超时（' + timeout + 'ms）：仍是 is-disabled 状态' +
      (state && state.value ? '（当前值「' + state.value + '」）' : ''),
    { field, timeout, state },
  );
}

/** 页内：找当前可见的 el-select-dropdown（teleport 到 body、position:fixed，不能用 offsetParent 判可见）。 */
async function readVisibleDropdown(page) {
  return page.evaluate(() => {
    const drops = [...document.querySelectorAll('.el-select-dropdown')].filter((d) => {
      const b = d.getBoundingClientRect();
      return b.width > 0 && b.height > 0;
    });
    if (!drops.length) return null;
    const d = drops[drops.length - 1];
    const b = d.getBoundingClientRect();
    return {
      rect: { x: Math.round(b.x), y: Math.round(b.y), w: Math.round(b.width), h: Math.round(b.height) },
      items: [...d.querySelectorAll('.el-select-dropdown__item')].map((i) => {
        const ib = i.getBoundingClientRect();
        return {
          text: (i.textContent || '').replace(/\s+/g, ' ').trim(),
          selected: i.classList.contains('is-selected'),
          disabled: i.classList.contains('is-disabled'),
          cx: Math.round(ib.x + ib.width / 2),
          cy: Math.round(ib.y + ib.height / 2),
          w: Math.round(ib.width),
          h: Math.round(ib.height),
        };
      }),
    };
  });
}

/**
 * 选一个 el-select 的选项（下拉 popper 挂在 body 上、position: fixed）。
 * 入参:
 *   page
 *   fieldLabelOrIndex  表单 label 文本或 el-select 下标（0 基）
 *   optionText         选项可见文本（先精确匹配，再包含匹配）
 *   options.scope        默认 .el-drawer
 *   options.waitEnabled  禁用时等多久（ms）；0 表示直接报错（默认 0）
 *   options.timeout      点开后等 popper / 等值生效的上限（默认 8000）
 * 返回: { field, label, from, to, options: [选项文本], openedBy }
 * 失败:
 *   [AILAB_SELECT_NOT_FOUND]        表单里没有 label 为「x」的 el-select（现有 label：…）
 *   [AILAB_SELECT_DISABLED]         字段是 is-disabled，点它不会有反应（当前值「x」）
 *   [AILAB_SELECT_ENABLE_TIMEOUT]   等可用超时（见 waitForSelectEnabled）
 *   [AILAB_SELECT_NOT_VISIBLE]      字段 rect 0×0（drawer 停在别的 tab，先 clickTab）
 *   [AILAB_SELECT_POPPER_TIMEOUT]   点开后 <timeout>ms 没出现可见的 .el-select-dropdown
 *   [AILAB_SELECT_OPTION_NOT_FOUND] 下拉里没有「x」，当前选项：…
 *   [AILAB_SELECT_NOT_APPLIED]      点了选项但字段值没变
 */
export async function selectOption(page, fieldLabelOrIndex, optionText, options = {}) {
  const { scope = SEL.drawer, waitEnabled = 0, timeout = TIMEOUTS.popper } = options;
  const want = String(optionText == null ? '' : optionText).trim();
  if (!want) fail('AILAB_SELECT_OPTION_NOT_FOUND', 'selectOption 的 optionText 为空');

  let state = await getSelectState(page, fieldLabelOrIndex, { scope });
  if (!state.found) {
    fail(
      'AILAB_SELECT_NOT_FOUND',
      '表单里没有 label 为「' + String(fieldLabelOrIndex) + '」的 el-select；现有 label：' +
        ((state.labels || []).join(' / ') || '（空）'),
      { field: fieldLabelOrIndex, labels: state.labels },
    );
  }
  if (state.disabled) {
    if (waitEnabled > 0) {
      state = await waitForSelectEnabled(page, fieldLabelOrIndex, { scope, timeout: waitEnabled });
    } else {
      fail(
        'AILAB_SELECT_DISABLED',
        '字段「' + state.label + '」是 is-disabled 状态（当前值「' + state.value +
          '」），点它不会有任何反应；确需可选请传 { waitEnabled: <ms> } 等它变可用，或确认这个字段本来就该锁死',
        { field: fieldLabelOrIndex, value: state.value, wrapperCls: state.wrapperCls },
      );
    }
  }
  if (!state.visible) {
    fail(
      'AILAB_SELECT_NOT_VISIBLE',
      '字段「' + state.label + '」当前 rect 为 0×0（不可见）：版本表单分三个 tab，先 clickTab(page, "模型信息") 再选',
      { field: fieldLabelOrIndex, rect: state.rect },
    );
  }

  const from = state.value;
  let openedBy = 'mouse';
  const target = await page.evaluate(
    ({ scope, field }) => {
      const root = (function(){const list=[...document.querySelectorAll(scope)];if(!list.length)return document.body;const vis=list.filter(function(el){const b=el.getBoundingClientRect();return b.width>0&&b.height>0;});return vis.length?vis[vis.length-1]:list[list.length-1];})();
      const items = [...root.querySelectorAll('.el-form-item')];
      const labelOf = (it) => ((it.querySelector('.el-form-item__label') || {}).textContent || '').trim();
      let item = null;
      if (typeof field === 'number') {
        item = items.filter((it) => it.querySelector('.el-select'))[field] || null;
      } else {
        const want = String(field).trim();
        item = items.find((it) => labelOf(it) === want) || items.find((it) => labelOf(it).startsWith(want)) || null;
      }
      if (!item) return null;
      const wrapper = item.querySelector('.el-select__wrapper');
      if (!wrapper) return null;
      wrapper.scrollIntoView({ block: 'center', inline: 'center' });
      const b = wrapper.getBoundingClientRect();
      return {
        x: Math.round(b.x + b.width - 16),
        y: Math.round(b.y + b.height / 2),
        cx: Math.round(b.x + b.width / 2),
        cy: Math.round(b.y + b.height / 2),
      };
    },
    { scope, field: fieldLabelOrIndex },
  );
  if (!target) fail('AILAB_SELECT_NOT_FOUND', '点开「' + state.label + '」时找不到它的 .el-select__wrapper');

  await page.mouse.move(target.cx, target.cy, { label: 'hover 下拉字段' });
  await page.mouse.click(target.x, target.y, { label: '点开下拉字段' });
  let dropdown = null;
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    dropdown = await readVisibleDropdown(page);
    if (dropdown) break;
    await page.waitForTimeout(150);
  }
  if (!dropdown) {
    openedBy = 'dom';
    await page.evaluate(
      ({ scope, field }) => {
        const root = (function(){const list=[...document.querySelectorAll(scope)];if(!list.length)return document.body;const vis=list.filter(function(el){const b=el.getBoundingClientRect();return b.width>0&&b.height>0;});return vis.length?vis[vis.length-1]:list[list.length-1];})();
        const items = [...root.querySelectorAll('.el-form-item')];
        const labelOf = (it) => ((it.querySelector('.el-form-item__label') || {}).textContent || '').trim();
        const want = String(field).trim();
        const item =
          typeof field === 'number'
            ? items.filter((it) => it.querySelector('.el-select'))[field]
            : items.find((it) => labelOf(it) === want) || items.find((it) => labelOf(it).startsWith(want));
        const wrapper = item && item.querySelector('.el-select__wrapper');
        if (wrapper) wrapper.click();
      },
      { scope, field: fieldLabelOrIndex },
    );
    const deadline2 = Date.now() + timeout;
    while (Date.now() < deadline2) {
      dropdown = await readVisibleDropdown(page);
      if (dropdown) break;
      await page.waitForTimeout(150);
    }
  }
  if (!dropdown) {
    fail(
      'AILAB_SELECT_POPPER_TIMEOUT',
      '点开「' + state.label + '」后 ' + timeout + 'ms 内没有出现可见的 .el-select-dropdown（判定用 getBoundingClientRect 宽高 > 0，不要用 offsetParent）',
      { field: fieldLabelOrIndex, rect: state.rect },
    );
  }

  const optionTexts = dropdown.items.map((i) => i.text);
  let hit = dropdown.items.find((i) => i.text === want);
  if (!hit) hit = dropdown.items.find((i) => i.text.includes(want));
  if (!hit) {
    fail(
      'AILAB_SELECT_OPTION_NOT_FOUND',
      '「' + state.label + '」下拉里没有「' + want + '」，当前选项：' + (optionTexts.join(' / ') || '（空）'),
      { field: fieldLabelOrIndex, optionText: want, options: optionTexts },
    );
  }
  if (hit.disabled) {
    fail('AILAB_SELECT_OPTION_NOT_FOUND', '「' + state.label + '」的选项「' + hit.text + '」是 is-disabled，选不了', {
      field: fieldLabelOrIndex,
      optionText: hit.text,
    });
  }

  const pick = async (mode) => {
    if (mode === 'mouse') {
      const box = await page.evaluate((text) => {
        const drops = [...document.querySelectorAll('.el-select-dropdown')].filter((d) => {
          const b = d.getBoundingClientRect();
          return b.width > 0 && b.height > 0;
        });
        const drop = drops[drops.length - 1];
        if (!drop) return { ok: false, reason: 'no-drop' };
        const li = [...drop.querySelectorAll('.el-select-dropdown__item')].find(
          (i) => (i.textContent || '').replace(/\s+/g, ' ').trim() === text,
        );
        if (!li) return { ok: false, reason: 'no-item' };
        const b = li.getBoundingClientRect();
        const cx = Math.round(b.x + b.width / 2);
        const cy = Math.round(b.y + b.height / 2);
        const top = document.elementFromPoint(cx, cy);
        return {
          ok: true,
          cx,
          cy,
          topmost: !!(top && (top === li || li.contains(top))),
          underCursor: top ? top.tagName + '.' + String(top.className || '').split(' ').slice(0, 2).join('.') : null,
        };
      }, hit.text);
      if (!box.ok) return { mode, ...box };
      if (!box.topmost) return { mode, ok: false, reason: 'covered', underCursor: box.underCursor };
      await page.mouse.click(box.cx, box.cy, { label: '选下拉选项' });
      return { mode, ok: true };
    }
    const res = await page.evaluate((text) => {
      const drops = [...document.querySelectorAll('.el-select-dropdown')].filter((d) => {
        const b = d.getBoundingClientRect();
        return b.width > 0 && b.height > 0;
      });
      const drop = drops[drops.length - 1];
      if (!drop) return { ok: false, reason: 'no-drop' };
      const li = [...drop.querySelectorAll('.el-select-dropdown__item')].find(
        (i) => (i.textContent || '').replace(/\s+/g, ' ').trim() === text,
      );
      if (!li) return { ok: false, reason: 'no-item' };
      li.click();
      return { ok: true };
    }, hit.text);
    return { mode, ...res };
  };

  const waitApplied = async (windowMs) => {
    const deadline = Date.now() + windowMs;
    while (Date.now() < deadline) {
      const state2 = await getSelectState(page, fieldLabelOrIndex, { scope });
      if (state2.found && state2.value.includes(hit.text)) return state2.value;
      await page.waitForTimeout(150);
    }
    return null;
  };

  const attempts = [];
  attempts.push(await pick('mouse'));
  let appliedValue = attempts[attempts.length - 1].ok ? await waitApplied(Math.min(2500, timeout)) : null;
  if (!appliedValue) {
    // 坐标点击不生效时退回 DOM click（Element Plus 的 li 绑的是 click 处理器）
    const second = await pick('dom');
    attempts.push(second);
    if (second.ok) appliedValue = await waitApplied(timeout);
  }

  if (!appliedValue) {
    fail(
      'AILAB_SELECT_NOT_APPLIED',
      '点了「' + state.label + '」的选项「' + hit.text + '」但字段值没变（仍是「' + from + '」）；尝试记录：' +
        JSON.stringify(attempts) + '。坐标点击被盖住时（underCursor 不是选项）DOM click 兜底也会走一遍',
      { field: fieldLabelOrIndex, optionText: hit.text, attempts },
    );
  }
  return { field: fieldLabelOrIndex, label: state.label, from, to: appliedValue, options: optionTexts, openedBy, attempts };
}
