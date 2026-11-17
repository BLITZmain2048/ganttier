const assert = require('node:assert/strict');
const fs = require('node:fs');
const ts = require(process.argv[2] || 'typescript');
require.extensions['.ts'] = (module, filename) => module._compile(ts.transpileModule(fs.readFileSync(filename,'utf8'), {
  compilerOptions: {module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020},
}).outputText,filename);
const {api,ApiError}=require('../src/api/client.ts');
const json = body => new Response(JSON.stringify(body),{headers:{'content-type':'application/json'}});
(async()=>{
  global.fetch=async()=>new Response(null,{status:204});
  assert.equal(await api.addProgress(1,[]),true);
  assert.equal(await api.setDataDate(1,'2026-09-26'),true);
  assert.equal(await api.applyRun(1,3),true);
  console.log('OK 204 no confunde éxito con fallo de useAction');
  let calls=0;
  global.fetch=async url=>{
    calls++;
    assert.ok(url.startsWith('/api/projects/1/activities?'));
    const q=new URL(url,'http://localhost').searchParams;
    assert.equal(q.get('q'),'A&B');
    return json(q.get('offset')==='0'?[{id:12,code:'A&B-extra'}]:[{id:20,code:'A&B'}]);
  };
  assert.equal((await api.activity(1,20,'A&B')).id,20); assert.equal(calls,2);
  console.log('OK detalle por colección, paginación con límite del servidor y código escapado');
  global.fetch=async()=>json([]);
  await assert.rejects(api.activity(1,99,'X'),e=>e instanceof ApiError && e.status===404);
  global.fetch=async()=>new Response('<html>index</html>',{headers:{'content-type':'text/html'}});
  await assert.rejects(api.columnar(1),e=>e instanceof ApiError && e.message.includes('JSON'));
  global.fetch=async()=>new Response(JSON.stringify({detail:'Datos inválidos',errors:[{row:2,message:'HH negativas'}]}),{status:422,headers:{'content-type':'application/json'}});
  await assert.rejects(api.addProgress(1,[]),e=>e.status===422 && e.body.errors[0].row===2);
  console.log('OK errores 404, respuesta HTML y validación 422');
})().catch(e=>{console.error(e);process.exitCode=1;});
