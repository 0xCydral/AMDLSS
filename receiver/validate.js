// SPDX-License-Identifier: GPL-2.0-or-later
// What a report may contain. The Worker stores only what passes this module, re-serialized from the parsed values, and
// amdlss/reports.py holds the same rules in Python: keep the two in step.

export const MAX_BODY_BYTES = 8 << 20;
export const MAX_FILES = 200;
export const MAX_NAME_CHARS = 160;
export const MAX_SEGMENTS = 5;
export const MAX_LABEL_CHARS = 120;
export const ALLOWED_EXTENSIONS = new Set(['log', 'ini', 'json', 'txt']);

// A segment is letters, digits, "_", "." and "-"; it may not start or end with "." (that also rules out "..").
const SEGMENT = /^[A-Za-z0-9_-](?:[A-Za-z0-9_.-]*[A-Za-z0-9_-])?$/;
// Device names Windows treats specially in any folder, with or without an extension.
const DEVICE = /^(?:con|prn|aux|nul|com[0-9]|lpt[0-9])$/i;
// NUL, the C0 controls except tab, line feed and carriage return, DEL and the C1 controls (a terminal obeys those).
const CONTROL = /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f]/;

/** The reason `name` may not be a file name in a report, or null when it may. */
export function nameProblem(name) {
  if (typeof name !== 'string' || name.length === 0 || name.length > MAX_NAME_CHARS) {
    return 'a file name is empty or too long';
  }
  const segments = name.split('/');
  if (segments.length > MAX_SEGMENTS) return 'a file name has more than 5 parts';
  for (const segment of segments) {
    if (!SEGMENT.test(segment) || segment.includes('..')) return 'a file name has characters that are not allowed';
    if (DEVICE.test(segment.split('.')[0])) return 'a file name is a reserved device name';
  }
  const last = segments[segments.length - 1];
  const dot = last.lastIndexOf('.');
  if (dot < 1 || !ALLOWED_EXTENSIONS.has(last.slice(dot + 1).toLowerCase())) {
    return 'a file name does not end in .log, .ini, .json or .txt';
  }
  return null;
}

/** The reason `text` may not be a file's content, or null when it may. */
export function textProblem(text) {
  if (typeof text !== 'string') return 'a file is not text';
  if (CONTROL.test(text)) return 'a file holds control characters';
  if (!text.isWellFormed()) return 'a file is not valid Unicode text';
  return null;
}

function labelProblem(label, what) {
  if (typeof label !== 'string' || label.length === 0 || [...label].length > MAX_LABEL_CHARS) {
    return `${what} must be a text of 1 to ${MAX_LABEL_CHARS} characters`;
  }
  if (CONTROL.test(label) || /[\n\r\t]/.test(label) || !label.isWellFormed()) return `${what} is not printable text`;
  return null;
}

/** {ok: true, report: {app, version, game, files}} with the values copied out of `value`, or {ok: false, error}. */
export function validateReport(value) {
  const fail = (error) => ({ ok: false, error });
  if (value === null || typeof value !== 'object' || Array.isArray(value)) return fail('the report must be an object');
  const keys = Object.keys(value);
  if (keys.length !== 4 || !['app', 'version', 'game', 'files'].every((key) => keys.includes(key))) {
    return fail('the report must have exactly app, version, game and files');
  }
  if (value.app !== 'AMDLSS') return fail('app must be AMDLSS');
  const problem = labelProblem(value.version, 'version') || labelProblem(value.game, 'game');
  if (problem) return fail(problem);
  const files = value.files;
  if (files === null || typeof files !== 'object' || Array.isArray(files)) return fail('files must be an object');
  const names = Object.keys(files);
  if (names.length === 0 || names.length > MAX_FILES) return fail(`files must hold 1 to ${MAX_FILES} files`);
  const clean = [];
  for (const name of names) {
    const reason = nameProblem(name) || textProblem(files[name]);
    if (reason) return fail(reason);
    clean.push([name, files[name]]);
  }
  return { ok: true, report: { app: 'AMDLSS', version: value.version, game: value.game, files: Object.fromEntries(clean) } };
}
