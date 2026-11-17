// Run with node tests/regression.cjs [path/to/typescript/lib/typescript.js].
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require(process.argv[2] || 'typescript');
require.extensions['.ts'] = (module, filename) => {
  const source = fs.readFileSync(filename, 'utf8');
  module._compile(ts.transpileModule(source, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020,
  }}).outputText, filename);
};
const dates = require('../src/utils/dates.ts');
const model = require('../src/components/Gantt/model.ts');
const macro = require('../src/utils/macro.ts');
const columnar = (n = 1) => ({ ids: Array.from({length:n}, (_,i)=>i+1),
  codes: Array.from({length:n}, (_,i)=>`A${i}`), names: Array(n).fill('Actividad'),
  starts: Array(n).fill('2026-01-02'), finishes: Array(n).fill('2026-01-02') });
let failed = 0;
function test(name, fn) { try { fn(); console.log(`OK ${name}`); } catch(e) { failed++; console.error(`FAIL ${name}: ${e.message}`); } }
test('UTC, bisiesto, fecha inválida y período vacío', () => {
  assert.equal(dates.isoToDay('1970-01-01'), 0);
  assert.equal(dates.isoToDay('1969-12-31'), -1);
  assert.equal(dates.dayToIso(dates.isoToDay('2024-02-29')), '2024-02-29');
  assert.ok(Number.isNaN(dates.isoToDay('2026-02-30')));
  assert.deepEqual(dates.periodBounds('monthly', ''), ['', '']);
  assert.deepEqual(dates.periodBounds('weekly', '2026-01-01'), ['2025-12-29','2026-01-04']);
});
test('IDs no se truncan a 32 bits y duración ausente no es hito', () => {
  const c = columnar(); c.ids[0] = 2**32 + 7; c.durations = [null];
  const a = model.buildArrays(c);
  assert.equal(a.ids[0], c.ids[0]); assert.equal(a.milestone[0], 0);
  assert.ok(Number.isNaN(a.dur[0]));
});
test('Extremos parciales no producen un horizonte infinito', () => {
  const c = columnar(); c.starts=[null]; c.finishes=[null]; c.window_starts=['2026-03-01'];
  const a = model.buildArrays(c); assert.ok(Number.isFinite(a.minDay)); assert.ok(Number.isFinite(a.maxDay));
  assert.ok(a.maxDay >= a.minDay);
});
test('WBS cíclica y profunda conserva cada actividad una vez', () => {
  const c=columnar(3); c.wbs_ids=[1,2,3]; const a=model.buildArrays(c);
  const wbs=[1,2,3].map(id=>({id, project_id:1,code:`W${id}`,name:'W',parent_id:id===1?2:id===2?1:99,sort_order:0}));
  const r=model.buildRows(a,wbs,{groupByWbs:true,collapsed:new Set(),filter:null,sort:'none'});
  assert.deepEqual(Array.from(r.rows).filter(i=>i>=0).sort(),[0,1,2]);
});
test('100 000 actividades, WBS y relaciones', () => {
  const start=performance.now(); const c=columnar(100000); c.wbs_ids=c.ids.map(i=>i);
  const a=model.buildArrays(c);
  const wbs=c.ids.map(id=>({id,project_id:1,code:String(id),name:'W',parent_id:id===1?null:id-1,sort_order:0}));
  const r=model.buildRows(a,wbs,{groupByWbs:true,collapsed:new Set(),filter:null,sort:'none'});
  assert.equal(r.rows.length,200000); assert.equal(r.rowOfAct[99999]>=0,true);
  const rels=model.buildRelations(c.ids.slice(1).map((id,i)=>({id,project_id:1,pred_id:i+1,succ_id:id,type:'FS',lag_days:0})),a);
  assert.equal(rels.n,99999); assert.equal(rels.offsets[100000],99999);
  assert.equal(rels.succ[rels.edges[rels.offsets[50000]]],50001);
  console.log(`  modelo 100k: ${(performance.now()-start).toFixed(0)} ms`);
});
test('Macro: fin inclusivo, meses parciales y rangos fuera del horizonte', () => {
  const months=[0,1,2].map(m=>({s:dates.monthStartDay(2024,m),e:dates.monthStartDay(2024,m+1)-1}));
  const data=[
    {start:dates.isoToDay('2024-01-31'),finish:dates.isoToDay('2024-02-01'),hours:16},
    {start:dates.isoToDay('2024-02-29'),finish:dates.isoToDay('2024-02-29'),hours:8},
    {start:dates.isoToDay('2023-12-31'),finish:dates.isoToDay('2024-01-01'),hours:20},
    {start:dates.isoToDay('2024-04-01'),finish:dates.isoToDay('2024-04-02'),hours:30},
  ];
  assert.deepEqual(Array.from(macro.monthlyHours(months,data.length,i=>data[i])),[18,16,0]);
  assert.equal(macro.monthOffset(dates.isoToDay('2023-12-01'),months[0].s),-1);
  assert.ok(Number.isNaN(macro.monthOffset(NaN,months[0].s)));
});
test('Macro: reparto conserva HH en 100 000 ventanas de un año bisiesto', () => {
  const months=Array.from({length:12},(_,m)=>({s:dates.monthStartDay(2024,m),e:dates.monthStartDay(2024,m+1)-1}));
  const start=performance.now();
  const totals=macro.monthlyHours(months,100000,()=>({start:months[0].s,finish:months[11].e,hours:366}));
  assert.equal(totals.reduce((a,b)=>a+b,0),36600000);
  assert.equal(totals[1],2900000);
  console.log(`  macro 100k: ${(performance.now()-start).toFixed(0)} ms`);
});
test('Mover actividad conserva días laborables y fin inclusivo', () => {
  const {scheduledDates}=require('../src/utils/activityDates.ts');
  const days=['2026-01-02','2026-01-03','2026-01-04','2026-01-05'].map((date,i)=>({date,working:i===0 || i===3,hours:i===0||i===3?8:0,reason:''}));
  assert.deepEqual(scheduledDates(days,'2026-01-02',2),{planned_start:'2026-01-02',planned_finish:'2026-01-05'});
  assert.deepEqual(scheduledDates(days,'2026-01-03',1),{planned_start:'2026-01-05',planned_finish:'2026-01-05'});
  assert.deepEqual(scheduledDates(days,'2026-01-03',0),{planned_start:'2026-01-03',planned_finish:'2026-01-03'});
  assert.throws(()=>scheduledDates(days,'2026-01-03',2));
});
process.exitCode = failed ? 1 : 0;
