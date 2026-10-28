/**
 * ailab 平台（Element Plus SPA）链路常量。
 * 只放常量与选择器，不做任何 IO 或页面操作。
 */

export const AILAB_URL =
  'https://ailab.bewg.net.cn/#/model-management/model-develop/machine/learning';

/** 历史脚本沿用的 TaskSpace 名与页面标签（同一个 space 才能复用登录态与现场）。 */
export const DEFAULT_SPACE = 'ailab-model-params-20261028';
export const DEFAULT_PAGE_LABEL = 'p2';

/** 所有等待都用毫秒，调用方可以逐项覆盖。 */
export const TIMEOUTS = Object.freeze({
  nav: 45000,
  list: 20000,
  action: 10000,
  menu: 8000,
  popper: 8000,
  dialog: 15000,
  toast: 8000,
  form: 15000,
});

export const SEL = Object.freeze({
  searchInput: 'input[placeholder="搜索模型名称"]',
  tableRow: '.el-table__body tr',
  table: '.el-table',
  tableTitle: '.table-title',
  dropdownLink: '.el-dropdown-link',
  dropdownMenu: '.el-dropdown-menu',
  dropdownMenuItem: '.el-dropdown-menu__item',
  dialog: '.el-dialog',
  drawer: '.el-drawer',
  container: '.el-dialog, .el-drawer',
  dialogTitle: '.el-dialog__title',
  drawerTitle: '.el-drawer__title',
  tabItem: '.el-tabs__item',
  form: '.el-form',
  formItem: '.el-form-item',
  formItemLabel: '.el-form-item__label',
  select: '.el-select',
  selectWrapper: '.el-select__wrapper',
  selectDropdown: '.el-select-dropdown',
  selectOption: '.el-select-dropdown__item',
  toast: '.el-message, .el-notification',
  messageBox: '.el-message-box',
});

/** 页面上的固定按钮 / tab 文案，集中一处便于平台改版时统一替换。 */
export const TEXT = Object.freeze({
  query: '查询',
  reset: '重置',
  more: '更多',
  versionManager: '版本管理',
  saveAsNewVersion: '另存为新版本',
  switchDeployFile: '切换部署文件',
  syncParams: '同步输入输出参数',
  pickDeployFile: '选择部署文件',
  confirmPick: '确定选择',
  saveAndContinue: '保存并继续',
  cancel: '取消',
  tabModelInfo: '模型信息',
  tabDeployFile: '绑定部署文件',
  tabParams: '参数配置',
  inputParams: '输入参数',
  outputParams: '输出参数',
  metrics: '评估指标',
});

/** 平台上会改数据的按钮：库与脚本一律不点，只用于在报错里点名。 */
export const FORBIDDEN_BUTTONS = Object.freeze([
  '保存并继续',
  '发布',
  '部署',
  '删除',
  '取消发布',
]);
