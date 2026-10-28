/**
 * 链路统一错误：err.code 给程序分支，err.message 已经是「[CODE] 中文文案」。
 */

export class AilabError extends Error {
  constructor(code, message, detail) {
    super('[' + code + '] ' + message);
    this.name = 'AilabError';
    this.code = code;
    this.detail = detail === undefined ? null : detail;
  }
}

/**
 * 抛一个带错误码的 AilabError。
 * 入参: code(字符串大写下划线), message(给人看的中文), detail(可选，结构化上下文)
 * 返回: 不返回（throw）
 */
export function fail(code, message, detail) {
  throw new AilabError(code, message, detail);
}

export function isAilabError(err) {
  return !!err && err.name === 'AilabError';
}

/** 把任意异常收敛成 { code, message }，用于回报。 */
export function describeError(err) {
  if (isAilabError(err)) return { code: err.code, message: err.message, detail: err.detail };
  return { code: 'AILAB_UNEXPECTED', message: (err && err.message) || String(err) };
}
