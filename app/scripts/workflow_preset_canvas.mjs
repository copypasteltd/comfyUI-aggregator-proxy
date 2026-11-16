// Read-only native ComfyUI canvas conversion. Requires the locked admin Playwright dependency.
import {chromium} from '../apps/admin_web/node_modules/@playwright/test/index.mjs';
import {readFileSync,writeFileSync,mkdirSync} from 'node:fs';
import path from 'node:path';

const flags=Object.fromEntries(process.argv.slice(2).reduce((pairs,value,index,args)=>{
 if(index%2===0)pairs.push([value,args[index+1]]);return pairs;
},[]));
for(const name of ['--catalog','--device','--source-base','--metadata','--private-output']){
 if(!flags[name])throw Error('Required '+name);
}
const source=new URL(flags['--source-base']);
if(!['http:','https:'].includes(source.protocol)||source.username||source.password||source.pathname!=='/'||source.search||source.hash)throw Error('Expected a credential-free source origin');
const catalogPath=path.resolve(flags['--catalog']),root=path.dirname(catalogPath),out=path.resolve(flags['--private-output']);
if(out===root||out.startsWith(root+path.sep))throw Error('Conversion diagnostics must stay outside the public archive');
mkdirSync(out,{recursive:true});
const catalog=JSON.parse(readFileSync(catalogPath,'utf8'));
const entries=catalog.presets.filter(e=>e.device===flags['--device']);
if(!entries.length)throw Error('No presets for this device');
const metadata=readFileSync(flags['--metadata'],'utf8');JSON.parse(metadata);
const report={format_version:1,at:new Date().toISOString(),device:flags['--device'],passed:false,
 scope:'Read-only native browser API-to-canvas conversion using explicitly supplied node metadata. No GPU execution or live runtime certification.',
 presets:[],non_read_requests_blocked:0,cleanup:{}};
let browser,context;
try{
 browser=await chromium.launch({channel:'msedge',headless:true,args:['--disable-extensions']});
 context=await browser.newContext({viewport:{width:1440,height:1000},locale:'zh-CN',reducedMotion:'reduce'});
 const page=await context.newPage();
 await page.route('**/*',async route=>{
  const request=route.request();
  if(!['GET','HEAD','OPTIONS'].includes(request.method())){report.non_read_requests_blocked++;return route.abort('blockedbyclient');}
  const url=new URL(request.url());
  if(['/object_info','/api/object_info'].includes(url.pathname))return route.fulfill({status:200,contentType:'application/json',body:metadata});
  return route.continue();
 });
 await page.goto(source.href,{waitUntil:'domcontentloaded',timeout:60000});
 await page.waitForFunction(()=>window.comfyAPI?.app?.app?.graph&&window.comfyAPI.app.app.graphToPrompt,null,{timeout:180000});
 await page.locator('#splash-loader').waitFor({state:'hidden',timeout:60000});
 for(const entry of entries){
  const directory=path.resolve(root,entry.directory);
  if(!directory.startsWith(root+path.sep))throw Error('Preset path escaped archive');
  const graph=JSON.parse(readFileSync(path.join(directory,'workflow.api.json'),'utf8'));
  const converted=await page.evaluate(async({graph,alias})=>{
   const app=window.comfyAPI.app.app;
   await app.loadApiJson(graph,alias+'.json');
   const prompt=await app.graphToPrompt();
   return {ui:app.graph.serialize(),prompt:prompt.output};
  },{graph,alias:entry.alias});
  const differences=[];
  for(const [id,node] of Object.entries(graph)){
   const actual=converted.prompt[id];
   if(!actual||actual.class_type!==node.class_type)throw Error('Canvas lost a node or changed its class');
   for(const [name,value] of Object.entries(node.inputs)){
    if(JSON.stringify(actual.inputs[name])!==JSON.stringify(value))differences.push({node_id:id,input:name});
   }
  }
  if(Object.keys(graph).length!==Object.keys(converted.prompt).length)throw Error('Unexpected canvas execution nodes');
  writeFileSync(path.join(out,entry.alias+'-canvas-private.json'),JSON.stringify({ui:converted.ui,differences},null,2)+'\n');
  report.presets.push({alias:entry.alias,nodes:Object.keys(graph).length,frontend_differences:differences});
 }
 report.passed=true;
 console.log(JSON.stringify({device:report.device,presets:report.presets.length,frontend_differences:report.presets.filter(e=>e.frontend_differences.length).length}));
}finally{
 try{if(context)await context.close();}finally{if(browser)await browser.close();report.cleanup.all_owned_browser_resources_closed=true;
  writeFileSync(path.join(out,'conversion-'+flags['--device']+'.json'),JSON.stringify(report,null,2)+'\n');}
}
