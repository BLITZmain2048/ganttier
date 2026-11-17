// Offline: node src/components/Gantt/resource-profile.test.cjs [typescript/lib/typescript.js]
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require(process.argv[2] || 'typescript');
require.extensions['.ts'] = (module, filename) => module._compile(ts.transpileModule(
  fs.readFileSync(filename, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }
).outputText, filename);
const { buildProfile, selectProfile, visiblePeriods } = require('./resourceProfileModel.ts');
const { createViewportChannel } = require('./viewport.ts');
const { isoToDay, dayToIso } = require('../../utils/dates.ts');

const daily = { period: 'day', labels: ['2026-01-01', '2026-01-02', '2026-01-04'],
  series: Array.from({ length: 8 }, (_, i) => ({ key: `S${i}`, load: [i, i * 2, i], capacity: [2, 0, 3] })) };
const model = buildProfile(daily, 'day');
assert.deepEqual(model.series.slice(0, 5).map(s => s.key), ['S7', 'S6', 'S5', 'S4', 'S3']);
const selected = selectProfile(model, new Set(['S7', 'S1']));
assert.deepEqual([...selected.load], [8, 16, 8]);
assert.deepEqual([...selected.capacity], [4, 0, 6]);
assert.equal(selected.max, 16);
assert.equal(selected.load[0] - selected.capacity[0], 4);
assert.deepEqual([...selectProfile(model, new Set()).capacity], [0, 0, 0]);
console.log('OK ranking, selected capacity, overload and empty selection');

const day = isoToDay('2026-01-01');
assert.deepEqual(visiblePeriods(model, day, day + 1), [0, 1]);
assert.deepEqual(visiblePeriods(model, day + 0.5, day + 1.5), [0, 2]);
assert.deepEqual(visiblePeriods(model, day + 2, day + 3), [2, 2]);
assert.deepEqual(visiblePeriods(model, day - 3, day), [0, 0]);
assert.deepEqual(visiblePeriods(model, day + 4, day + 5), [3, 3]);
const weekly = buildProfile({ period: 'week', labels: ['2025-12-29', '2026-01-05'], series: [] }, 'week');
assert.equal(weekly.span, 7);
assert.deepEqual(visiblePeriods(weekly, day, day + 1), [0, 1]);
assert.deepEqual(visiblePeriods(weekly, day + 4, day + 5), [1, 2]);
assert.throws(() => buildProfile(daily, 'week'));
assert.throws(() => buildProfile({ ...daily, labels: ['2026-02-30', ...daily.labels.slice(1)] }, 'day'));
assert.throws(() => buildProfile({ ...daily, labels: [...daily.labels].reverse() }, 'day'));
assert.throws(() => buildProfile({ ...daily, series: [{ key: 'X', load: [1], capacity: [] }] }, 'day'));
assert.throws(() => buildProfile({ ...daily, series: [daily.series[0], daily.series[0]] }, 'day'));
assert.throws(() => buildProfile({ ...daily, series: [{ ...daily.series[0], load: [1, NaN, 2] }] }, 'day'));
console.log('OK day/week boundaries, gaps and invalid responses');

const channel = createViewportChannel();
let calls = 0;
const unsubscribe = channel.subscribe(() => calls++);
const v = { originDay: day - 45, dayWidth: 7, scrollX: 266.5, width: 900 };
channel.publish(v); channel.publish({ ...v });
assert.equal(calls, 1, 'vertical scroll/repeated viewport must not notify');
for (const width of [0.15, 2.4, 3.999, 4, 7, 26, 60]) {
  const next = { ...v, dayWidth: width, scrollX: 105.25 };
  channel.publish(next);
  const received = channel.get();
  assert.deepEqual(received, next);
  const x = (day - received.originDay) * received.dayWidth - received.scrollX;
  assert.equal(x, (day - next.originDay) * width - next.scrollX);
}
const before = calls; unsubscribe(); channel.publish(v); assert.equal(calls, before);
console.log('OK exact viewport, fractional scroll, zoom and unsubscribe');

const many = buildProfile({ period: 'day', labels: Array.from({ length: 100000 }, (_, i) => dayToIso(day + i)), series: [] }, 'day');
const start = performance.now();
for (let i = 0; i < 10000; i++) {
  const left = (i * 7919) % 99900;
  assert.deepEqual(visiblePeriods(many, day + left + 0.25, day + left + 20.25), [left, left + 21]);
}
console.log(`OK 10000 visible-range lookups over 100000 periods: ${Math.round(performance.now() - start)} ms`);

const root = path.resolve(__dirname, '../../..');
const config = ts.readConfigFile(path.join(root, 'tsconfig.json'), ts.sys.readFile);
const parsed = ts.parseJsonConfigFileContent(config.config, ts.sys, root);
const diagnostics = ts.getPreEmitDiagnostics(ts.createProgram(
  ['resourceProfileModel.ts', 'viewport.ts'].map(file => path.join(__dirname, file)), parsed.options));
if (diagnostics.length) console.error(ts.formatDiagnosticsWithColorAndContext(diagnostics, {
  getCanonicalFileName: f => f, getCurrentDirectory: () => root, getNewLine: () => '\n',
}));
assert.equal(diagnostics.length, 0);
console.log(`OK TypeScript ${ts.version}: strict profile model and viewport`);
