import fs from 'node:fs/promises';
import path from 'node:path';
import { Workbook } from '@oai/artifact-tool';
if (process.argv[2] === '--help-csv') {
  const wb = Workbook.create();
  console.log(wb.help('*', {search:'toCSV|exportCsv|exportCSV',include:'index,examples,notes',maxChars:3500}).ndjson);
  process.exit(0);
}
const input = JSON.parse(await fs.readFile(process.argv[2], 'utf8'));
const out = path.resolve(process.argv[3]);
// Resume matching partial exports, but never overwrite completed bundles or bytes.
const previewOnly=process.argv.includes('--preview-only');
if(!previewOnly) {
  try { await fs.access(path.join(out,'manifest.json')); throw new Error('Completed bundle already exists'); }
  catch(error) { if(error.code !== 'ENOENT') throw error; }
}
await fs.mkdir(out,{recursive:true});
await fs.mkdir(path.join(out, 'inputs'),{recursive:true});
await fs.mkdir(path.join(out, 'labels'),{recursive:true});
async function saveNew(file, bytes) {
  try { await fs.writeFile(file,bytes,{flag:'wx'}); }
  catch(error) {
    if(error.code !== 'EEXIST' || (await fs.readFile(file,'utf8')) !== bytes) throw error;
  }
}
const hashes = {};
const { createHash } = await import('node:crypto');
const sha = value => createHash('sha256').update(value).digest('hex');
for (const [name, source] of Object.entries(input.gateway)) {
  if (sha(source.csv) !== source.sha256) throw new Error('Gateway source integrity failure');
  await saveNew(path.join(out,'inputs',name+'.csv'),source.csv);
  hashes[name+'.csv'] = {sha256:source.sha256, provenance:'SYNTHETIC_DEMO',revision_id:source.revision_id};
}
for (const name of ['bank_entries','ledger_entries']) {
  const workbook = Workbook.create();
  const sheet = workbook.worksheets.add(name);
  const columns = input.columns[name];
  const matrix = [columns,...input.rows[name].map(row => columns.map(column => row[column]))];
  const range=sheet.getRangeByIndexes(0,0,matrix.length,columns.length);
  // CSV is a strict application transport, not a financial-model workbook:
  // preserve exact decimal strings, ISO timestamps, and identifiers as text.
  range.setNumberFormat('@');
  range.values=matrix;
  sheet.getRangeByIndexes(0,0,1,columns.length).format={fill:'#101827',font:{bold:true,color:'#FFFFFF'}};
  range.format.columnWidth=29;
  range.format.rowHeight=32;
  range.format.wrapText=true;
  if(name==='bank_entries') sheet.getRangeByIndexes(1,1,matrix.length-1,1).setNumberFormat('yyyy-mm-dd"T"hh:mm:ss"Z"');
  sheet.getRangeByIndexes(0,5,matrix.length,1).format.columnWidth=48;
  sheet.showGridLines=false;
  // Encode authored cells losslessly; no numeric coercion or formulas in imports.
  // The library canonicalizes ISO timestamps with .000Z; removing only zero
  // milliseconds restores the supplied exact-second CSV transport convention.
  const authored=range.values.map(row=>row.map(value=>value instanceof Date ? value.toISOString().replace(/\.000Z$/, 'Z') : value ?? ''));
  if(JSON.stringify(authored)!==JSON.stringify(matrix)) throw new Error('Cell round trip changed the source values: '+JSON.stringify(authored.flatMap((row,i)=>row.flatMap((value,j)=>value===matrix[i][j]?[]:[{i,j,value,expected:matrix[i][j]}])).slice(0,5)));
  const quote=value=> /[",\r\n]/.test(value) ? '"'+value.replaceAll('"','""')+'"' : value;
  const csv=authored.map(row=>row.map(value=>quote(String(value ?? ''))).join(',')).join('\n')+'\n';
  await saveNew(path.join(out,'inputs',name+'.csv'),csv);
  hashes[name+'.csv']={sha256:sha(csv),rows:input.rows[name].length,provenance:'SYNTHETIC_DEMO'};
  console.log((await workbook.inspect({kind:'region',sheetId:name,range:'A1:D4',maxChars:650,tableMaxRows:4})).ndjson);
  const preview=await workbook.render({sheetName:name,range:`A1:${String.fromCharCode(64+columns.length)}5`,scale:1,format:'png'});
  await fs.writeFile(path.join(path.dirname(process.argv[2]),name+'.png'),new Uint8Array(await preview.arrayBuffer()));
}
if(!previewOnly) {
  await fs.writeFile(path.join(out,'manifest.json'),JSON.stringify({version:input.version,seed:input.seed,import_id:input.import_id,provenance:input.provenance,production_eligible:false,files:hashes,assumptions:input.assumptions},null,2)+'\n',{flag:'wx'});
  await fs.writeFile(path.join(out,'labels','scenario.json'),JSON.stringify(input.expectations,null,2)+'\n',{flag:'wx'});
}
console.log(JSON.stringify({output:out,files:hashes}));
