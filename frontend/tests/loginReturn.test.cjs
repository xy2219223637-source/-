const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');

const source = fs.readFileSync(path.join(__dirname, '../src/lib/loginReturn.ts'), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
const context = { exports: {}, URL, window: { location: { origin: 'https://scripts.example.com', pathname: '/director-inspiration/23/workspace', search: '?tab=script', hash: '#notes' } } };
vm.runInNewContext(compiled, context);
const { safeReturnPath, loginUrl } = context.exports;

test('login returns to the original script, preserving query and fragment', () => {
  const original = '/director-inspiration/23/workspace?tab=script#notes';
  assert.equal(safeReturnPath(original), original);
  const login = new URL(loginUrl(), context.window.location.origin);
  assert.equal(login.pathname, '/login');
  assert.equal(login.searchParams.get('returnTo'), original);
});

test('external, encoded protocol-relative and login-loop targets are rejected', () => {
  for (const value of [null, '', 'https://evil.example', '//evil.example', '/%2fevil.example', '/a/..//evil.example', '/%2e//evil.example', '/\\evil.example', '/%5cevil.example', '/login', '/log%69n', '/login?returnTo=/login', '/bad%00path', '/bad\npath', '/%']) {
    assert.equal(safeReturnPath(value), '/', String(value));
  }
});
