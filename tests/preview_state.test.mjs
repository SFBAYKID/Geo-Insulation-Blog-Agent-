import test from 'node:test';
import assert from 'node:assert/strict';
import {chromium} from 'playwright';
import {articleState} from '../tools/preview_state.mjs';

test('main article images stay strict while related cards are outside its scope', async () => {
  const browser = await chromium.launch({channel:'chrome',headless:true});
  try {
    const page = await browser.newPage();
    const pixel = 'data:image/svg+xml,'+encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="2" height="2"><rect width="2" height="2" fill="blue"/></svg>');
    await page.setContent(`<meta name="robots" content="noindex"><main><article id="post"><h1>Handoff</h1><img src="${pixel}" alt="Reviewed hero"><a href="https://geo-insulation.com/services/attic-insulation/">Product</a></article><section><article><a href="/related/"><img alt="" loading="lazy" src="data:image/png,broken">Related post</a></article></section></main>`);
    await page.locator('#post img').evaluate(i=>i.decode());
    const product = 'https://geo-insulation.com/services/attic-insulation/';
    const good = await page.evaluate(articleState,product);
    assert.equal(good.images.length,1);
    assert.deepEqual(good.images[0],{alt:'Reviewed hero',loaded:true});
    assert.equal(good.productLinks,1);
    assert.equal(good.noindex,true);
    await page.locator('#post img').evaluate(i=>i.alt='');
    assert.equal((await page.evaluate(articleState,product)).images[0].alt,'');
    await page.locator('#post img').evaluate(i=>{i.alt='Hero';i.src='data:image/png,broken';});
    await page.waitForFunction(()=>document.querySelector('#post img').complete);
    assert.equal((await page.evaluate(articleState,product)).images[0].loaded,false);
    await page.locator('#post a').evaluate(a=>a.remove());
    assert.equal((await page.evaluate(articleState,product)).productLinks,0);
  } finally { await browser.close(); }
});
