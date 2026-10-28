/**
 * scripts/platform/ailab/lib —— ailab（Element Plus SPA）链路的可复用辅助库。
 * 全部导出都是无副作用函数：入参给 page / taskSpace，返回结构化结果，失败抛 AilabError（err.code 可分支）。
 */

export { AILAB_URL, DEFAULT_SPACE, DEFAULT_PAGE_LABEL, TIMEOUTS, SEL, TEXT, FORBIDDEN_BUTTONS } from './constants.mjs';
export { AilabError, fail, isAilabError, describeError } from './errors.mjs';
export { rectOf, isVisibleByRect, waitForVisible, waitForGone, textOf, visibleText, waitForText } from './dom.mjs';
export { ensurePage, closePage, searchModel } from './page.mjs';
export { readMenus, openRowMoreMenu, clickMenuItem } from './menu.mjs';
export { getSelectState, waitForSelectEnabled, selectOption } from './select.mjs';
export { listContainers, waitForContainer, waitForContainerGone, clickContainerButton, clickButton, clickTab } from './dialog.mjs';
export { readTable, clickRowRadio } from './table.mjs';
export { readParamTables } from './params.mjs';
export { getFormSnapshot, findFormField, fillFormField, waitForFormReady } from './form.mjs';
export { startToastRecorder, clearToasts, readToasts, waitForToast } from './toast.mjs';
