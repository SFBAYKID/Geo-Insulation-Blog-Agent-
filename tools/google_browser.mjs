import { chromium } from 'playwright';
import fs from 'node:fs';
import path from 'node:path';
const [keyword, folder, mode='collect'] = process.argv.slice(2);
if(!keyword || !folder) throw new Error('Expected keyword and evidence folder');
fs.mkdirSync(folder,{recursive:true});
const browser=await chromium.launchPersistentContext(path.resolve('storage/google-browser-profile'),{channel:'chrome',headless:false,viewport:{width:1365,height:1000},locale:'en-US'});
const page=browser.pages()[0] || await browser.newPage();
const url='https://www.google.com/search?'+new URLSearchParams({q:keyword,gl:'us',hl:'en',pws:'0'});
try {
await page.goto(url,{waitUntil:'domcontentloaded',timeout:45000});
if(mode==='setup'){
 console.log('Browser open for Google verification. Complete any challenge in this window.');
 await page.waitForFunction(()=>location.hostname==='www.google.com' && location.pathname==='/search' && document.querySelector('#search h3'),{},{timeout:1800000});
}else{
 await page.waitForSelector('#search h3',{timeout:15000});
}
const rows=await page.locator('#search').evaluate(root=>Array.from(root.querySelectorAll('h3')).map(h=>{
 const a=h.closest('a');
 const box=h.closest('div.MjjYud') || h.parentElement?.parentElement;
 return {title:h.textContent.trim(),url:a?.href,context:box?.textContent?.slice(0,1200)||'',sponsored:!!h.closest('[data-text-ad],#tads,#tadsb,[aria-label="Ads"]')};
}).filter(x=>x.url));
const results=[];const seen=new Set();
for(const row of rows){
 let u;try{u=new URL(row.url)}catch{continue}
 if(u.hostname.endsWith('google.com')||!['http:','https:'].includes(u.protocol)||seen.has(u.href))continue;
 seen.add(u.href);
 const host=u.hostname.replace(/^www\./,'');
 const kind=row.sponsored?'ad':/^(reddit.com|quora.com|stackoverflow.com)$/.test(host)||host.endsWith('.stackexchange.com')||/^(forum|forums|community)\./.test(host)?'forum':/^(youtube.com|youtu.be|tiktok.com)$/.test(host)?'video':'organic_page';
 results.push({position:results.length+1,title:row.title,url:u.href,kind});
}
if(results.filter(r=>r.kind==='organic_page').length<3)throw new Error('Google returned fewer than three verifiable web results');
fs.writeFileSync(path.join(folder,'google.html'),await page.content());
await page.screenshot({path:path.join(folder,'google.png'),fullPage:true});
fs.writeFileSync(path.join(folder,'google-search.json'),JSON.stringify({source:'google_browser',query:keyword,checked_at:new Date().toISOString(),search_url:url,location:'United States (Google gl=us; location may affect results)',results},null,2));
console.log(JSON.stringify({verified:true,results:results.slice(0,6)}));
} catch(e){
fs.writeFileSync(path.join(folder,'google-failure.html'),await page.content());
await page.screenshot({path:path.join(folder,'google-failure.png')}).catch(()=>{});
console.error('Google browser verification unavailable; see saved browser evidence.');process.exitCode=2;
}finally{await browser.close()}
