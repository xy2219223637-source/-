const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');

const source = fs.readFileSync(path.join(__dirname, '../src/lib/notificationStatus.ts'), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
const context = { exports: {} };
vm.runInNewContext(compiled, context);
const { isNotificationOutcomeUnknown } = context.exports;

test('server rejection before notification creation permits a corrected attempt', () => {
  assert.equal(isNotificationOutcomeUnknown({ response: {
    status: 503, headers: { 'x-notification-outcome': 'not_started' },
  } }), false);
});

test('transport loss, server errors and timeout require reconciliation before retrying', () => {
  for (const error of [null, {}, { response: {} }, { response: { status: 503 } },
    { response: { status: 502 } }, { response: { status: 408 } },
    { response: { status: 503, headers: { 'x-notification-outcome': 'unexpected' } } }]) {
    assert.equal(isNotificationOutcomeUnknown(error), true, JSON.stringify(error));
  }
});

test('authentication, validation and duplicate-task rejection are known failures', () => {
  for (const status of [400, 401, 403, 404, 409, 422, 429]) {
    assert.equal(isNotificationOutcomeUnknown({ response: { status } }), false, String(status));
  }
});
