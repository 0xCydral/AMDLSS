// SPDX-License-Identifier: GPL-2.0-or-later
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { MAX_FILES, nameProblem, textProblem, validateReport } from './validate.js';

const report = (files = { 'files.txt': 'ok\n' }, extra = {}) => ({ app: 'AMDLSS', version: '0.1.0', game: 'Game', files, ...extra });

test('the names a zip of logs uses are accepted', () => {
  for (const name of ['files.txt', 'system.txt', 'amdlss-install.log', 'exe-folder/OptiScaler.log',
    'exe-folder/HelixSR/helixsr-install.json', 'fsr-dll-folder-2/helixsr.ini', 'a/b/c.LOG', 'a/b/c/d/e.log',
    'Game/history/20261013-141500/exe-folder/HelixSR/helixsr.log', 'a/b/c/d/e/f/g.log']) {
    assert.equal(nameProblem(name), null, name);
  }
});

test('traversal, absolute, hidden and odd names are refused', () => {
  const bad = ['', '../x.log', 'a/../x.log', '/etc/passwd.txt', '.hidden.log', 'a/.x.log', 'a/./x.log', 'a//x.log',
    'a/b/c/d/e/f/g/h.log', 'a\\b.log', 'C:/x.log', 'x.log ', 'x .log', 'x.log.', 'x..log', 'noext', '.log', 'x.exe', 'x.dll',
    'x.log.exe', 'x.sh', 'x.zip', 'x.log/', 'a./b.log', 'nul.log', 'COM1.txt', 'a/aux.ini', 'caf\u00e9.log',
    'x\u0000.log', 'x\n.log', 'a'.repeat(157) + '.log', '__proto__'];
  for (const name of bad) assert.notEqual(nameProblem(name), null, JSON.stringify(name));
  assert.notEqual(nameProblem(42), null);
  assert.notEqual(nameProblem(undefined), null);
});

test('text may hold tab, newline and carriage return and no other control character', () => {
  assert.equal(textProblem('a\tb\r\nc\n'), null);
  assert.equal(textProblem('caf\u00e9 \u2014 \u{1F600}'), null);
  for (const bad of ['a\u0000b', '\u0001', '\u0008', '\u000b', '\u000c', '\u001b[31m', '\u007f', '\u009b', 'x\ud800']) {
    assert.notEqual(textProblem(bad), null, JSON.stringify(bad));
  }
  assert.notEqual(textProblem(5), null);
  assert.notEqual(textProblem(null), null);
});

test('a good report comes back as a copy holding only the four fields', () => {
  const files = { 'files.txt': 'a', 'exe-folder/OptiScaler.log': 'b' };
  const result = validateReport(report(files));
  assert.equal(result.ok, true);
  assert.deepEqual(result.report, report(files));
  assert.notEqual(result.report.files, files);
});

test('the report must be exactly app, version, game and files', () => {
  for (const bad of [null, [], 'x', 5, {}, report({ 'a.txt': 'x' }, { extra: 1 }), { ...report(), app: 'Other' },
    { ...report(), app: undefined }, { app: 'AMDLSS', version: '1', game: 'g' }]) {
    assert.equal(validateReport(bad).ok, false, JSON.stringify(bad));
  }
});

test('version and game are short printable strings', () => {
  for (const field of ['version', 'game']) {
    for (const bad of ['', 'x'.repeat(121), 'a\nb', 'a\u0000b', 'a\u001bb', 5, null, 'x\ud800']) {
      assert.equal(validateReport({ ...report(), [field]: bad }).ok, false, `${field} ${JSON.stringify(bad)}`);
    }
    assert.equal(validateReport({ ...report(), [field]: 'x'.repeat(120) }).ok, true);
    assert.equal(validateReport({ ...report(), [field]: 'Caf\u00e9: Game\u00ae' }).ok, true);
  }
});

test('files: an object of 1 to 200 allowed names with text values', () => {
  assert.equal(validateReport(report({})).ok, false);
  assert.equal(validateReport(report([])).ok, false);
  assert.equal(validateReport(report('x')).ok, false);
  const many = (count) => Object.fromEntries(Array.from({ length: count }, (_, i) => [`f${i}.txt`, 'x']));
  assert.equal(validateReport(report(many(MAX_FILES))).ok, true);
  assert.equal(validateReport(report(many(MAX_FILES + 1))).ok, false);
  assert.equal(validateReport(report({ '../x.log': 'x' })).ok, false);
  assert.equal(validateReport(report({ 'a.log': 'x\u0000' })).ok, false);
  assert.equal(validateReport(report({ 'a.log': 5 })).ok, false);
  assert.equal(validateReport(report({ 'a.log': ['x'] })).ok, false);
});

test('a __proto__ key in the JSON is a refused name, not a way to change the prototype', () => {
  const parsed = JSON.parse('{"app":"AMDLSS","version":"1","game":"g","files":{"__proto__":"x"}}');
  assert.equal(validateReport(parsed).ok, false);
  assert.equal(({}).polluted, undefined);
});
