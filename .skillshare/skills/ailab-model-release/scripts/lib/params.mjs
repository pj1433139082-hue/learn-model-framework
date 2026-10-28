import { SEL, TEXT, TIMEOUTS } from './constants.mjs';
import { fail } from './errors.mjs';
import { clickTab } from './dialog.mjs';
import { readTable } from './table.mjs';

const INPUT_KEYMAP = {
  中文名称: 'name',
  英文名称: 'enName',
  数据类型: 'type',
  单位: 'unit',
  默认值: 'defaultValue',
  是否必填: 'required',
  是否数采测点: 'isPoint',
};
const OUTPUT_KEYMAP = {
  中文名称: 'name',
  英文名称: 'enName',
  参数类型: 'type',
  单位: 'unit',
  类型: 'kind',
};
const METRIC_KEYMAP = {
  中文名称: 'name',
  英文名称: 'enName',
  参数类型: 'type',
  指标角色: 'role',
};

function mapRows(headers, rows, keyMap) {
  return rows.map((cells) => {
    const row = { cells };
    for (const header of Object.keys(keyMap)) {
      const idx = headers.indexOf(header);
      if (idx >= 0) row[keyMap[header]] = cells[idx];
    }
    return row;
  });
}

/**
 * 读「参数配置」里的输入 / 输出 / 评估指标三张表（会先切到参数配置 tab）。
 * 入参: page, options { scope='.el-drawer', openTab=true, timeout=15000 }
 * 返回: {
 *   inputs:  [{ name, enName, type, unit, defaultValue, required, isPoint, cells }],
 *   outputs: [{ name, enName, type, unit, kind, cells }],
 *   metrics: [{ name, enName, type, role, cells }],
 *   summary: { inputCount, outputCount, metricCount, inputTypes: {类型: 个数}, hasAny, arrayCount }
 * }
 * 失败:
 *   [AILAB_PARAM_TABLE_MISSING] 参数配置里「x」表 <timeout>ms 内没出现（drawer 没开或 tab 没切过去）
 *   [AILAB_TABLE_NOT_FOUND]     表读不到（见 readTable）
 */
export async function readParamTables(page, options = {}) {
  const { scope = SEL.drawer, openTab = true, timeout = TIMEOUTS.form } = options;
  if (openTab) await clickTab(page, TEXT.tabParams, { scope, timeout });

  const deadline = Date.now() + timeout;
  let ready = false;
  while (Date.now() < deadline) {
    ready = await page.evaluate(() => {
      return [...document.querySelectorAll('.el-table')].some((t) => {
        const b = t.getBoundingClientRect();
        if (!(b.width > 0 && b.height > 0)) return false;
        const hs = [...t.querySelectorAll('.el-table__header th')].map((th) => (th.textContent || '').trim());
        return hs.includes('是否数采测点');
      });
    });
    if (ready) break;
    await page.waitForTimeout(250);
  }
  if (!ready) {
    fail('AILAB_PARAM_TABLE_MISSING', '参数配置里「输入参数」表 ' + timeout + 'ms 内没出现（drawer 没开，或 tab 没切到「参数配置」）', {
      timeout,
    });
  }

  const ins = await readTable(page, { scope, headerKey: ['中文名称', '数据类型', '是否数采测点'] });
  const outs = await readTable(page, { scope, headerKey: ['中文名称', '参数类型', '类型'] });
  let metrics = { headers: [], rows: [], rowCount: 0 };
  try {
    metrics = await readTable(page, { scope, headerKey: ['中文名称', '参数类型', '指标角色'] });
  } catch (err) {
    metrics = { headers: [], rows: [], rowCount: 0 };
  }

  const inputs = mapRows(ins.headers, ins.rows, INPUT_KEYMAP);
  const outputs = mapRows(outs.headers, outs.rows, OUTPUT_KEYMAP);
  const metricRows = mapRows(metrics.headers, metrics.rows, METRIC_KEYMAP);
  const inputTypes = {};
  for (const row of inputs) {
    const key = (row.type || '').trim() || '(空)';
    inputTypes[key] = (inputTypes[key] || 0) + 1;
  }
  return {
    inputs,
    outputs,
    metrics: metricRows,
    summary: {
      inputCount: inputs.length,
      outputCount: outputs.length,
      metricCount: metricRows.length,
      inputTypes,
      hasAny: inputs.some((r) => (r.type || '').trim() === 'any'),
      arrayCount: inputs.filter((r) => /array/i.test(r.type || '')).length,
    },
  };
}
