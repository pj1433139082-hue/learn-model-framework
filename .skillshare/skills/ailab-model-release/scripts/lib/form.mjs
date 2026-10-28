import { SEL, TIMEOUTS } from './constants.mjs';
import { fail } from './errors.mjs';

/**
 * 读版本表单快照：每个字段的 label / 值 / 控件类型 / 是否 disabled / 是否可见，以及重名 label。
 * 入参: page, options { scope='.el-drawer' }
 * 返回: { tabs: { all, active }, fields: [{ label, formCls, kind, value, disabled, visible }], duplicates（scope 内重名）, pageDuplicates（整页重名，含版本管理弹窗筛选区的「版本别名」） }
 * 失败: 不抛错（没有 drawer 时返回空 fields）。
 */
export async function getFormSnapshot(page, options = {}) {
  const { scope = SEL.drawer } = options;
  return page.evaluate(({ scope }) => {
    const root = (function(){const list=[...document.querySelectorAll(scope)];if(!list.length)return document.body;const vis=list.filter(function(el){const b=el.getBoundingClientRect();return b.width>0&&b.height>0;});return vis.length?vis[vis.length-1]:list[list.length-1];})();
    const labelOf = (it) => ((it.querySelector('.el-form-item__label') || {}).textContent || '').trim();
    const formOf = (it) => {
      const f = it.closest('.el-form');
      return f ? f.className.split(' ').slice(0, 2).join(' ') : '';
    };
    const tabs = [...root.querySelectorAll('.el-tabs__item')].map((t) => {
      const b = t.getBoundingClientRect();
      return { text: (t.textContent || '').trim(), active: t.classList.contains('is-active'), visible: b.width > 0 && b.height > 0 };
    });
    const fields = [...root.querySelectorAll('.el-form-item')].map((it) => {
      const b = it.getBoundingClientRect();
      const wrapper = it.querySelector('.el-select__wrapper');
      const input = it.querySelector('input, textarea');
      let kind = 'other';
      if (wrapper) kind = 'select';
      else if (input && input.tagName === 'TEXTAREA') kind = 'textarea';
      else if (input && input.type === 'radio') kind = 'radio';
      else if (input) kind = 'input';
      const disabled = wrapper
        ? wrapper.classList.contains('is-disabled') || (input ? input.disabled === true : false)
        : input
          ? input.disabled === true
          : false;
      let value = null;
      if (wrapper) value = (wrapper.textContent || '').replace(/\s+/g, ' ').trim();
      else if (input && input.type !== 'radio') value = input.value;
      return {
        label: labelOf(it),
        formCls: formOf(it),
        kind,
        value,
        disabled,
        visible: b.width > 0 && b.height > 0,
      };
    });
    const seen = {};
    for (const f of fields) {
      if (!f.label) continue;
      seen[f.label] = seen[f.label] || [];
      seen[f.label].push(f.formCls);
    }
    const pageSeen = {};
    for (const it of document.querySelectorAll('.el-form-item')) {
      const label = labelOf(it);
      if (!label) continue;
      pageSeen[label] = pageSeen[label] || [];
      pageSeen[label].push(formOf(it));
    }
    const pageDuplicates = Object.keys(pageSeen)
      .filter((k) => pageSeen[k].length > 1)
      .map((k) => ({ label: k, count: pageSeen[k].length, formCls: pageSeen[k] }));
    const duplicates = Object.keys(seen)
      .filter((k) => seen[k].length > 1)
      .map((k) => ({ label: k, count: seen[k].length, formCls: seen[k] }));
    return { tabs: { all: tabs, active: (tabs.find((t) => t.active) || {}).text || null }, fields, duplicates, pageDuplicates };
  }, { scope });
}

/**
 * 找表单字段（按 label 精确匹配；同 scope 内多处命中会报错）。
 * 入参: page, label, options { scope='.el-drawer' }
 * 返回: { index, label, formCls, kind, value, disabled, visible }
 * 失败:
 *   [AILAB_FIELD_NOT_FOUND]   表单里没有 label 为「x」的字段；现有 label：…
 *   [AILAB_FIELD_AMBIGUOUS]   表单里有 <n> 个 label 为「x」的字段（同名 label），请缩小 scope
 */
export async function findFormField(page, label, options = {}) {
  const { scope = SEL.drawer } = options;
  const snap = await getFormSnapshot(page, { scope });
  const want = String(label == null ? '' : label).trim();
  const hits = snap.fields.filter((f) => f.label === want);
  if (!hits.length) {
    fail(
      'AILAB_FIELD_NOT_FOUND',
      '表单里没有 label 为「' + want + '」的字段；现有 label：' + (snap.fields.map((f) => f.label).filter(Boolean).join(' / ') || '（空）'),
      { field: want },
    );
  }
  if (hits.length > 1) {
    fail(
      'AILAB_FIELD_AMBIGUOUS',
      '表单里有 ' + hits.length + ' 个 label 为「' + want + '」的字段（同名 label，平台上「版本别名」在筛选区和表单区各有一个）；请把 scope 缩到具体表单',
      { field: want, hits },
    );
  }
  return hits[0];
}

/**
 * 填一个文本输入框 / textarea（不会点保存）。
 * 入参: page, label, value, options { scope='.el-drawer', timeout=10000 }
 * 返回: { label, from, to, kind }
 * 失败:
 *   [AILAB_FIELD_NOT_FOUND]     没有这个字段
 *   [AILAB_FIELD_AMBIGUOUS]     同名 label 多处
 *   [AILAB_FIELD_NOT_FILLABLE]  这个字段是 el-select / radio，请用 selectOption 或原生点击
 *   [AILAB_FIELD_DISABLED]      字段是 disabled
 *   [AILAB_FILL_UNCHANGED]      填完值没变
 */
export async function fillFormField(page, label, value, options = {}) {
  const { scope = SEL.drawer } = options;
  const field = await findFormField(page, label, { scope });
  if (field.kind === 'select' || field.kind === 'radio' || field.kind === 'other') {
    fail(
      'AILAB_FIELD_NOT_FILLABLE',
      '字段「' + field.label + '」是 ' + field.kind + ' 控件，不能用 fill；el-select 请用 selectOption(page, 字段, 选项)',
      { field: field.label, kind: field.kind },
    );
  }
  if (field.disabled) {
    fail('AILAB_FIELD_DISABLED', '字段「' + field.label + '」是 disabled 状态，填不进去（当前值「' + (field.value || '') + '」）', {
      field: field.label,
    });
  }
  const marked = await page.evaluate(
    ({ scope, label }) => {
      const root = (function(){const list=[...document.querySelectorAll(scope)];if(!list.length)return document.body;const vis=list.filter(function(el){const b=el.getBoundingClientRect();return b.width>0&&b.height>0;});return vis.length?vis[vis.length-1]:list[list.length-1];})();
      const labelOf = (it) => ((it.querySelector('.el-form-item__label') || {}).textContent || '').trim();
      const hits = [...root.querySelectorAll('.el-form-item')].filter((it) => labelOf(it) === String(label).trim());
      if (hits.length !== 1) return { ok: false, hits: hits.length };
      const input = hits[0].querySelector('input, textarea');
      if (!input) return { ok: false, hits: 1 };
      input.setAttribute('data-ailab-fill', '1');
      return { ok: true };
    },
    { scope, label },
  );
  if (!marked.ok) {
    fail('AILAB_FIELD_NOT_FOUND', '定位字段「' + label + '」的输入框失败（命中 ' + marked.hits + ' 个 form-item）', {
      field: label,
    });
  }
  try {
    await page.fill('[data-ailab-fill="1"]', String(value == null ? '' : value));
  } finally {
    await page.evaluate(() => {
      const el = document.querySelector('[data-ailab-fill="1"]');
      if (el) el.removeAttribute('data-ailab-fill');
    });
  }
  const after = await findFormField(page, label, { scope });
  if ((after.value || '') !== String(value == null ? '' : value)) {
    fail('AILAB_FILL_UNCHANGED', '填「' + label + '」后值没变（仍是「' + (after.value || '') + '」）', {
      field: label,
      expect: value,
      actual: after.value,
    });
  }
  return { label: field.label, from: field.value, to: after.value, kind: field.kind };
}

/**
 * 等「另存为新版本」表单填充完成（模型名称 / 版本还是空的时候，字段都是 disabled 的「请选择」，读早了会误判）。
 * 入参: page, options { scope='.el-drawer', timeout=15000 }
 * 返回: true
 * 失败: [AILAB_FORM_NOT_READY] 版本表单 <timeout>ms 内没填充好（模型名称/版本还是空）
 */
export async function waitForFormReady(page, options = {}) {
  const { scope = SEL.drawer, timeout = TIMEOUTS.form } = options;
  try {
    await page.waitForFunction(
      ({ scope }) => {
        const list = [...document.querySelectorAll(scope)];
        if (!list.length) return false;
        const vis = list.filter((el) => {
          const b = el.getBoundingClientRect();
          return b.width > 0 && b.height > 0;
        });
        const root = vis.length ? vis[vis.length - 1] : list[list.length - 1];
        if (!root) return false;
        const items = [...root.querySelectorAll('.el-form-item')];
        const labelOf = (it) => ((it.querySelector('.el-form-item__label') || {}).textContent || '').trim();
        const valueOf = (label) => {
          const it = items.find((x) => labelOf(x) === label);
          if (!it) return '';
          const input = it.querySelector('input, textarea');
          return input ? String(input.value || '').trim() : '';
        };
        return valueOf('模型名称').length > 0 && /[0-9A-Za-z]/.test(valueOf('版本'));
      },
      { scope },
      { timeout, polling: 200 },
    );
    return true;
  } catch (err) {
    fail(
      'AILAB_FORM_NOT_READY',
      '版本表单 ' + timeout + 'ms 内没填充好（模型名称 / 版本仍是空）；drawer 可能还在加载，或这压根不是「另存为新版本」表单',
      { scope },
    );
  }
}
