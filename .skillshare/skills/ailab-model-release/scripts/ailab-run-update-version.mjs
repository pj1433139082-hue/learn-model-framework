/**
 * ailab 平台「另存为新版本 → 换部署 Run → 同步参数 → 读参数表」链路。
 *
 * 参数化：搜索词 / RunID / 版本别名（三种来源，优先级从高到低）
 *   1) globalThis.__AILAB_RUN_ARGS__ = { searchWord, runId, versionAlias, ... }（在 -e 里先赋值再 import）
 *   2) 同目录的 run-update-version.config.json（UTF-8，可写中文；推荐用来传中文别名）
 *   3) 命令行参数 --search=2619 --run-id=xxx --alias=xxx（ego-browser 的 -e 里不方便，主要给包一层命令用）
 *
 * 边界：脚本到「读到参数表」就停，绝不点「保存并继续 / 发布 / 部署 / 删除 / 取消发布」。
 *
 * 跑法（cmd / PowerShell 单行，注意 -e 里不要放中文）：
 *   ego-browser.exe nodejs -e "globalThis.__AILAB_RUN_ARGS__={searchWord:'2619',runId:'bb408764ce7445389709c2bd6bda77c1',versionAlias:'array-20261028'};await import('file:///E:/beikong-code/learn-model/scripts/platform/ailab/run-update-version.mjs')"
 */

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  AILAB_URL,
  DEFAULT_PAGE_LABEL,
  DEFAULT_SPACE,
  SEL,
  TEXT,
  TIMEOUTS,
  ensurePage,
  searchModel,
  openRowMoreMenu,
  clickMenuItem,
  waitForContainer,
  waitForContainerGone,
  clickContainerButton,
  clickButton,
  clickTab,
  readTable,
  clickRowRadio,
  readParamTables,
  readToasts,
  getFormSnapshot,
  fillFormField,
  waitForFormReady,
  getSelectState,
  startToastRecorder,
  clearToasts,
  waitForToast,
  describeError,
} from './lib/index.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const CONFIG_FILE = path.join(HERE, 'run-update-version.config.json');

export const DEFAULT_CONFIG = Object.freeze({
  space: DEFAULT_SPACE,
  pageLabel: DEFAULT_PAGE_LABEL,
  searchWord: '2619',
  runId: '',
  versionAlias: '',
  modelIdMatch: null,
  switchDeployFile: true,
  syncParams: true,
  discard: false,
});

function readConfigFile(file) {
  try {
    if (!fs.existsSync(file)) return {};
    return JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch (err) {
    return {};
  }
}

function readArgv(argv) {
  const out = {};
  for (const arg of argv || []) {
    const m = /^--([A-Za-z0-9_-]+)=(.*)$/.exec(String(arg));
    if (!m) continue;
    const key = m[1].replace(/-([a-z])/g, (_, c) => c.toUpperCase());
    out[key] = m[2];
  }
  return out;
}

function truthy(value, fallback) {
  if (value === undefined || value === null || value === '') return fallback;
  if (value === false || value === 'false' || value === '0') return false;
  return true;
}

/**
 * 合并配置：DEFAULT < 配置文件 < argv < globalThis.__AILAB_RUN_ARGS__ < overrides。
 * 入参: overrides（对象）, options { file, argv }
 * 返回: 规范化后的配置（字符串 trim；布尔归一）
 * 失败: 不抛错（runId 校验在 runUpdateVersion 第 0 步）
 */
export function normalizeConfig(overrides = {}, options = {}) {
  const file = options.file || CONFIG_FILE;
  const merged = {
    ...DEFAULT_CONFIG,
    ...readConfigFile(file),
    ...readArgv(options.argv || process.argv),
    ...(globalThis.__AILAB_RUN_ARGS__ || {}),
    ...overrides,
  };
  const cfg = {
    space: String(merged.space || DEFAULT_SPACE).trim(),
    pageLabel: String(merged.pageLabel || DEFAULT_PAGE_LABEL).trim(),
    searchWord: String(merged.searchWord == null ? '' : merged.searchWord).trim(),
    runId: String(merged.runId == null ? '' : merged.runId).trim(),
    versionAlias: String(merged.versionAlias == null ? '' : merged.versionAlias).trim(),
    modelIdMatch: merged.modelIdMatch ? String(merged.modelIdMatch).trim() : null,
    switchDeployFile: truthy(merged.switchDeployFile, true),
    syncParams: truthy(merged.syncParams, true),
    discard: truthy(merged.discard, false),
  };
  if (!cfg.modelIdMatch) {
    cfg.modelIdMatch = /^[0-9]+$/.test(cfg.searchWord) ? 'MLModel-' + cfg.searchWord : null;
  }
  return cfg;
}

/**
 * 跑完整条链路（到读参数表为止）。
 * 入参:
 *   config  见 DEFAULT_CONFIG（searchWord / runId / versionAlias 是三个主参数）
 *   hooks   { log: (msg) => void }
 * 返回:
 *   { ok, config, steps: [{ step, ok, detail, ms }], form, selects, toast, params, nextManualStep, failed? }
 * 失败: 不抛错，返回 { ok:false, failed: { code, message, step } }（同时把 process.exitCode 置 1）
 */
export async function runUpdateVersion(config = {}, hooks = {}) {
  const cfg = normalizeConfig(config);
  const log = hooks.log || ((msg) => console.log(msg));
  const t0 = Date.now();
  const steps = [];
  let current = 'init';
  const result = { ok: false, config: cfg, steps, form: null, selects: null, toast: null, params: null, nextManualStep: null };

  const record = (name, detail) => {
    const rec = { step: name, ok: true, detail: detail === undefined ? null : detail, ms: Date.now() - t0 };
    steps.push(rec);
    log('  ok ' + name + (detail === undefined ? '' : ' ' + JSON.stringify(detail)));
    return rec;
  };

  try {
    if (!cfg.runId) {
      current = 'validateConfig';
      const err = new Error(
        '缺少 runId：在 run-update-version.config.json 或 globalThis.__AILAB_RUN_ARGS__ 里给 32 位 MLflow Run ID',
      );
      err.name = 'AilabError';
      err.code = 'AILAB_RUN_CONFIG';
      throw err;
    }
    const taskSpaceFn = globalThis.taskSpace;
    if (typeof taskSpaceFn !== 'function') {
      const err = new Error('没有 taskSpace：这个脚本必须用 ego-browser nodejs 跑（见本文件头注释）');
      err.name = 'AilabError';
      err.code = 'AILAB_NO_EGO_RUNTIME';
      throw err;
    }

    current = 'taskSpace';
    const task = await taskSpaceFn(cfg.space);
    log('[ailab] TaskSpace ' + task.spaceId + ' / ' + task.name + '，搜索词=' + cfg.searchWord + '，RunID=' + cfg.runId);
    record('taskSpace', { spaceId: task.spaceId, name: task.name });

    current = 'ensurePage';
    const ensured = await ensurePage(task, { label: cfg.pageLabel, url: AILAB_URL, waitFor: SEL.searchInput });
    const page = ensured.page;
    record('ensurePage', { label: ensured.label, reused: ensured.reused, created: ensured.created });

    current = 'searchModel';
    const search = await searchModel(page, cfg.searchWord);
    record('searchModel', { rows: search.rows, firstRow: (search.firstRow || '').slice(0, 100) });

    current = 'openRowMoreMenu';
    const menu = await openRowMoreMenu(page, {
      modelId: cfg.modelIdMatch,
      rowIndex: 0,
      expectItems: [TEXT.versionManager],
    });
    record('openRowMoreMenu', { rect: menu.rect, items: menu.items });

    current = 'clickMenuItem';
    await clickMenuItem(page, TEXT.versionManager);
    const versionDialog = await waitForContainer(page, { titleContains: TEXT.versionManager, timeout: TIMEOUTS.dialog });
    record('clickMenuItem(版本管理)', { dialog: versionDialog.title });

    current = 'saveAsNewVersion';
    await clickContainerButton(page, {
      titleContains: TEXT.versionManager,
      buttonText: TEXT.saveAsNewVersion,
      timeout: TIMEOUTS.dialog,
    });
    const drawer = await waitForContainer(page, { kind: 'drawer', timeout: TIMEOUTS.dialog });
    record('clickContainerButton(另存为新版本)', { drawer: drawer.kind, buttons: drawer.buttons.slice(-6) });

    current = 'formSnapshot';
    await clickTab(page, TEXT.tabModelInfo);
    await waitForFormReady(page, { timeout: TIMEOUTS.form });
    const form = await getFormSnapshot(page);
    const visibleFields = form.fields.filter((f) => f.visible);
    result.form = { tabs: form.tabs, duplicates: form.duplicates, pageDuplicates: form.pageDuplicates, fields: visibleFields };
    record('getFormSnapshot', {
      fields: visibleFields.length,
      duplicates: form.duplicates.map((d) => d.label),
      disabledSelects: visibleFields.filter((f) => f.kind === 'select' && f.disabled).map((f) => f.label),
    });

    current = 'selectStates';
    const modelType = await getSelectState(page, '模型类型');
    const category = await getSelectState(page, '分类');
    result.selects = {
      模型类型: { disabled: modelType.disabled, value: modelType.value },
      分类: { disabled: category.disabled, value: category.value },
    };
    record('getSelectState(模型类型/分类)', result.selects);

    if (cfg.versionAlias) {
      current = 'fillVersionAlias';
      const filled = await fillFormField(page, '版本别名', cfg.versionAlias);
      record('fillFormField(版本别名)', filled);
    }

    current = 'pickRun';
    await clickTab(page, TEXT.tabDeployFile);
    const runTable = await readTable(page, { headerKey: ['Run ID', 'Experiment 名称'] });
    record('readTable(Run 列表)', { rowCount: runTable.rowCount, headers: runTable.headers });
    const picked = await clickRowRadio(page, { matchText: cfg.runId });
    record('clickRowRadio(目标 Run)', { row: (picked.rowText || '').slice(0, 120) });

    if (cfg.switchDeployFile) {
      current = 'switchDeployFile';
      // 注意：切换部署文件 / 同步输入输出参数 两个按钮在「参数配置」tab 面板里，藏在别的 tab 时 rect 是 0×0
      await clickTab(page, TEXT.tabParams);
      await clickButton(page, TEXT.switchDeployFile);
      const picker = await waitForContainer(page, { titleContains: TEXT.pickDeployFile, timeout: TIMEOUTS.dialog });
      record('clickButton(切换部署文件)', { dialog: picker.title });
      await clickRowRadio(page, { rootTitle: TEXT.pickDeployFile, matchText: cfg.runId });
      record('clickRowRadio(选择部署文件)', { runId: cfg.runId });
      await clickContainerButton(page, { titleContains: TEXT.pickDeployFile, buttonText: TEXT.confirmPick });
      await waitForContainerGone(page, { titleContains: TEXT.pickDeployFile });
      record('clickContainerButton(确定选择)', { closed: TEXT.pickDeployFile });
    }

    if (cfg.syncParams) {
      current = 'syncParams';
      await clickTab(page, TEXT.tabParams);
      await startToastRecorder(page);
      await clearToasts(page);
      await clickButton(page, TEXT.syncParams);
      const toast = await waitForToast(page, { expect: ['同步', '成功'], timeout: TIMEOUTS.toast });
      await page.waitForTimeout(1200);
      const allToasts = await readToasts(page);
      result.toast = toast.text;
      result.toasts = allToasts.map((x) => x.text);
      record('clickButton(同步输入输出参数)', { toasts: result.toasts });
    }

    current = 'readParamTables';
    const params = await readParamTables(page, { openTab: true });
    result.params = { summary: params.summary, inputs: params.inputs, outputs: params.outputs };
    record('readParamTables', params.summary);

    if (cfg.discard) {
      current = 'discard';
      await clickButton(page, TEXT.cancel);
      record('clickButton(取消)', { drawer: 'discarded' });
    }

    result.ok = true;
    result.nextManualStep =
      '参数表已读到。要真正落库仍需人工：①确认输入参数里 16 个测点参数的类型是 array（不是 any）②再点「保存并继续」与「发布」——脚本不点这几个按钮。';
    return result;
  } catch (err) {
    const described = describeError(err);
    result.ok = false;
    result.failed = { ...described, step: current };
    if (typeof process !== 'undefined') process.exitCode = 1;
    log('  FAIL ' + described.code + ' @' + current + ': ' + described.message);
    return result;
  }
}

const autorun = !globalThis.__AILAB_NO_AUTORUN__;
if (autorun) {
  const cfg = normalizeConfig();
  const out = await runUpdateVersion(cfg);
  console.log(JSON.stringify(out, null, 2));
  if (out.ok && out.params) {
    console.log(
      '[ailab] 输入参数 ' + out.params.summary.inputCount + ' 个，类型分布 ' +
        JSON.stringify(out.params.summary.inputTypes) + '；输出参数 ' + out.params.summary.outputCount + ' 个',
    );
  }
}
