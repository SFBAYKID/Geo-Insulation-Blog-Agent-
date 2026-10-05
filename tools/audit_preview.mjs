/** Audit the protected, deployed article in a fresh browser, with no local web server. */
import fs from 'node:fs';
import puppeteer from 'puppeteer-core';
import lighthouse from 'lighthouse';
import * as chromeLauncher from 'chrome-launcher';
import {previewCookies} from './preview_auth.mjs';

const [url, output] = process.argv.slice(2);
const cookies = await previewCookies(url);
if (!cookies.length) throw new Error('The final speed audit requires the hosted preview');
const multiplier = Number(process.env.LIGHTHOUSE_CPU_SLOWDOWN_MULTIPLIER || 4);
if (!(multiplier >= 1 && multiplier <= 20)) throw new Error('Invalid CPU calibration');
const chrome = await chromeLauncher.launch({chromeFlags: ['--headless=new']});
try {
  const browser = await puppeteer.connect({browserURL: `http://127.0.0.1:${chrome.port}`});
  await browser.setCookie(...cookies);
  await browser.disconnect();
  // This is a fresh profile with no page loads or cached assets. Preserve only the
  // host-scoped authentication cookie; never add a credential header to all requests.
  const result = await lighthouse(url, {port: chrome.port, disableStorageReset: true,
    throttling: {cpuSlowdownMultiplier: multiplier},
    onlyCategories: ['performance', 'accessibility', 'best-practices', 'seo'],
    output: 'json', logLevel: 'error'});
  const report = result.lhr;
  fs.writeFileSync(output, JSON.stringify(report), {mode: 0o600});
  if (report.runtimeError || report.finalDisplayedUrl !== url) throw new Error('Hosted article audit failed or redirected');
  console.log('Hosted article Lighthouse:', JSON.stringify(Object.fromEntries(
    Object.entries(report.categories).map(([key, value]) => [key, Math.round(value.score * 100)]))));
} finally {
  await chrome.kill();
}
