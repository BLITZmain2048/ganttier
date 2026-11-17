// Offline check using an installed TypeScript compiler; no npm/downloads.
const ts = require(process.argv[2] || 'typescript');
const path = require('node:path');
const fs = require('node:fs');
const root = path.resolve(__dirname, '..');
const config = ts.readConfigFile(path.join(root, 'tsconfig.json'), ts.sys.readFile);
console.log(`TypeScript ${ts.version}`);
const parsed = ts.parseJsonConfigFileContent(config.config, ts.sys, root);
const format = diagnostics => ts.formatDiagnosticsWithColorAndContext(diagnostics, {
  getCanonicalFileName: f => f, getCurrentDirectory: () => root, getNewLine: () => '\n',
});
let syntax = [], fullCount = 0;
for (const file of parsed.fileNames) {
  syntax.push(...(ts.transpileModule(fs.readFileSync(file,'utf8'), {
    fileName: file, compilerOptions: parsed.options, reportDiagnostics: true,
  }).diagnostics || []));
}
console.log(`Syntax: ${parsed.fileNames.length} files, ${syntax.length} diagnostics`);
if (syntax.length) console.error(format(syntax));
const files = ['src/utils/dates.ts','src/utils/macro.ts','src/utils/activityDates.ts','src/components/Gantt/model.ts','src/api/types.ts','src/api/client.ts'];
const program = ts.createProgram(files.map(f=>path.join(root,f)), parsed.options);
const diagnostics = ts.getPreEmitDiagnostics(program);
console.log(`Strict core/API: ${diagnostics.length} diagnostics`);
if (diagnostics.length) console.error(format(diagnostics));
if (process.argv.includes('--full')) {
  const full = ts.getPreEmitDiagnostics(ts.createProgram(parsed.fileNames, parsed.options));
  fullCount = full.length;
  console.log(`Full project: ${full.length} diagnostics`);
  const missing = full.filter(d => d.code === 2307);
  if (full.length) console.error(format(missing.length ? missing : full));
}
process.exitCode = syntax.length || diagnostics.length || fullCount ? 1 : 0;
