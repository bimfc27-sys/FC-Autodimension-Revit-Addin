const { chromium, devices } = require('playwright');
const S = process.argv[2];
const MODE = process.argv[3] || 'desktop';
const B = 'https://localhost:8443';
const SECRETS = ['Pass9', 'rtsp://', 'relay-site'];

async function frames(page) {
  return page.evaluate(() => [...document.querySelectorAll('.tile')].map(t => {
    const v = t.querySelector('video');
    return { name: t.querySelector('.name')?.textContent, w: v?.videoWidth || 0, h: v?.videoHeight || 0, t: v ? +v.currentTime.toFixed(2) : -1,
             foot: t.querySelector('.tile-foot .grow')?.textContent };
  }));
}

(async () => {
  const browser = await chromium.launch({ args: ['--autoplay-policy=no-user-gesture-required'] });
  const ctxOpts = MODE === 'iphone' ? { ...devices['iPhone 13'], ignoreHTTPSErrors: true } : { viewport: { width: 1440, height: 900 }, ignoreHTTPSErrors: true };
  const ctx = await browser.newContext(ctxOpts);
  const page = await ctx.newPage();
  if (process.argv[4] === 'nowebrtc') await page.route('**/whep', r => r.abort());
  const leaks = [];
  page.on('console', m => { if (SECRETS.some(s => m.text().includes(s))) leaks.push('console: ' + m.text()); });
  page.on('response', async r => {
    if (!r.url().includes('/api/') || r.url().includes('/hls/')) return;
    try { const b = await r.text(); for (const s of SECRETS) if (b.includes(s)) leaks.push(`${r.url()} contains ${s}`); } catch {}
  });
  await page.goto(B + '/login');
  await page.fill('input[type=email]', 'owner@example.com');
  await page.fill('input[type=password]', 'Lab-Owner-Pass9');
  await page.click('button:has-text("Sign in")');
  await page.waitForURL('**/dashboard');
  if (MODE === 'iphone') await page.click('.bottom-nav button:has-text("Cameras")');
  await page.waitForSelector('.cam-item');
  console.log('camera list:', (await page.locator('.cam-item').allInnerTexts()).map(t => t.replace(/\s+/g, ' ').trim()));
  await page.screenshot({ path: `${S}/lab/${MODE}-list.png` });
  // play every camera through "Play all" of each connection group
  const playAll = page.locator('.section-title button:has-text("Play all")');
  const n = await playAll.count();
  const cams = await page.evaluate(async () => (await (await fetch('/api/cameras')).json()));
  // open all 4 via the API-backed list: click each row
  if (MODE === 'iphone') {
    for (const c of cams) { await page.click('.bottom-nav button:has-text("Cameras")'); await page.click(`.cam-item:has-text("${c.name}")`); }
  } else {
    for (const c of cams) await page.click(`.cam-item:has-text("${c.name}")`);
  }
  await page.waitForTimeout(9000);
  const f1 = await frames(page);
  await page.waitForTimeout(3000);
  const f2 = await frames(page);
  let allOk = true;
  for (const a of f1) {
    const b = f2.find(x => x.name === a.name);
    const ok = a.w > 0 && b.t > a.t;
    allOk = allOk && ok;
    console.log(`${ok ? 'PLAYING' : 'NOT PLAYING'}  ${a.name.padEnd(32)} ${a.w}x${a.h}  t ${a.t}s -> ${b.t}s  | ${b.foot}`);
  }
  await page.screenshot({ path: `${S}/lab/${MODE}-live4.png` });
  // quality switch on first gateway camera
  const tile = page.locator('.tile:has-text("Office entrance")');
  await tile.locator('select[aria-label="Stream quality"]').selectOption('low');
  await page.waitForTimeout(8000);
  const q = (await frames(page)).find(x => x.name.startsWith('Office'));
  console.log('after switching Office entrance to SD:', `${q.w}x${q.h}`, q.foot);
  // 9-feed layout
  await page.click('button[aria-label="9 feeds"]');
  await page.waitForTimeout(1500);
  console.log('layout 9 class:', await page.getAttribute('.grid', 'class'), 'tiles:', await page.locator('.tile').count());
  await page.screenshot({ path: `${S}/lab/${MODE}-live9.png` });
  console.log('LEAKS:', leaks.length ? leaks : 'none');
  console.log('RESULT:', allOk ? 'ALL FEEDS PLAYING' : 'SOME FEEDS NOT PLAYING');
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
