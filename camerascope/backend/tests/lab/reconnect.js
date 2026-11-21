const { chromium } = require('playwright');
const { execSync } = require('child_process');
const S = process.argv[2];
const st = async (page) => page.evaluate(() => { const t = document.querySelector('.tile'); const v = t.querySelector('video');
  return { w: v?.videoWidth || 0, t: v ? +v.currentTime.toFixed(1) : -1, overlay: t.querySelector('.tile-overlay')?.innerText.replace(/\s+/g, ' ') || '', dot: t.querySelector('.dot').className }; });
(async () => {
  const b = await chromium.launch(); const ctx = await b.newContext({ viewport: { width: 1280, height: 800 }, ignoreHTTPSErrors: true });
  const page = await ctx.newPage();
  await page.goto('https://localhost:8443/login');
  await page.fill('input[type=email]', 'owner@example.com'); await page.fill('input[type=password]', 'Lab-Owner-Pass9');
  await page.click('button:has-text("Sign in")'); await page.waitForURL('**/dashboard');
  await page.click('.cam-item:has-text("Office entrance")');
  await page.waitForTimeout(8000);
  console.log('playing     :', JSON.stringify(await st(page)));
  execSync(`bash ${S}/lab/kill_site2.sh`);
  console.log('>> gateway agent for site2 killed');
  for (let i = 0; i < 4; i++) { await page.waitForTimeout(8000); console.log(`outage +${(i + 1) * 8}s :`, JSON.stringify(await st(page))); }
  const listText = await page.locator('.cam-item:has-text("Office entrance")').first().innerText();
  console.log('list entry  :', listText.replace(/\s+/g, ' '));
  execSync(`bash ${S}/lab/agent_site2.sh`);
  console.log('>> gateway agent restarted (stored credentials, no user action)');
  let recovered = false;
  for (let i = 0; i < 12; i++) {
    await page.waitForTimeout(5000);
    const a = await st(page); await page.waitForTimeout(1500); const c = await st(page);
    console.log(`recovery +${(i + 1) * 6.5}s:`, JSON.stringify(c));
    if (c.w > 0 && c.t > a.t && !c.overlay) { recovered = true; break; }
  }
  console.log('RESULT:', recovered ? 'RECOVERED AUTOMATICALLY' : 'DID NOT RECOVER');
  await b.close();
})();
