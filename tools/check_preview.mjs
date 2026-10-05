import {chromium} from 'playwright';
import fs from 'node:fs';
import {previewCookies} from './preview_auth.mjs';
import {articleState} from './preview_state.mjs';
const [url,product,folder,mode]=process.argv.slice(2);
const published=mode==='--published';
if(published && new URL(url).origin!=='https://geo-insulation.com')throw new Error('Unexpected production host');
const cookies=published?[]:await previewCookies(url);
const b=await chromium.launch({channel:'chrome',headless:true});
const reports=[];
try{
 const context=await b.newContext();
 await context.addCookies(cookies);
 const p=await context.newPage();
 for(const width of [320,390,1440]){
  await p.setViewportSize({width,height:1000});
  const r=await p.goto(url,{waitUntil:'networkidle',timeout:45000});
  if(r.status()!==200)throw new Error('Preview did not return 200');
  await p.locator('article').first().waitFor();
  const state=await p.evaluate(articleState,product);
  if(state.overflow||state.headings!==1||!state.productLinks||(published?state.noindex:!state.noindex)||!state.images.length||state.images.some(i=>!i.alt||!i.loaded))throw new Error('Preview failed responsive, image, product link or noindex checks: '+JSON.stringify(state));
  const exercise=p.locator('[data-reader-exercise]');
  if(await exercise.count()){
   const summaries=exercise.locator('summary');
   for(let i=0;i<await summaries.count();i++){
    const summary=summaries.nth(i);
    await summary.focus();await p.keyboard.press('Enter');
    if(!await summary.evaluate(el=>el.parentElement.open))throw new Error('Exercise does not open with the keyboard');
    await p.keyboard.press('Enter');
    if(await summary.evaluate(el=>el.parentElement.open))throw new Error('Exercise does not close with the keyboard');
   }
   await p.evaluate(()=>scrollTo(0,0));
  }
  await p.screenshot({path:`${folder}/preview-${width}-opening.png`});
  await p.screenshot({path:`${folder}/preview-${width}.png`,fullPage:true});reports.push({width,...state});
 }
 fs.writeFileSync(`${folder}/browser-qa.json`,JSON.stringify(reports,null,2));
 console.log(published?'Live responsive layout, art, product links and indexing checks passed.':'Responsive preview, art, product links and draft indexing checks passed.');
}finally{await b.close()}
