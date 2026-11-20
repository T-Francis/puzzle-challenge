// README screenshots (docs/images/), run by scripts/screenshots.sh in the Playwright image:
//   atelier_en.png  the workshop on a phone, English, dark theme, empty journal (first proposal)
//   demo.jpg        the public demo, English, 40 % into the reconstruction
//   catalog.png     the piece catalog
// The repository is mounted read only at /repo, the output directory at /out, the workshop answers on WORKSHOP.
import { chromium } from "playwright";

const WORKSHOP = process.env.WORKSHOP || "http://localhost:8081";
const browser = await chromium.launch();

async function shot(url, ctxOpts, out, { wait = 2500, ready, init } = {}) {
  const ctx = await browser.newContext(ctxOpts);
  if (init) await ctx.addInitScript(init);
  const page = await ctx.newPage();
  page.on("pageerror", (e) => console.log("page error", url, e.message));
  await page.goto(url, { waitUntil: "load" });
  if (ready) await page.waitForSelector(ready, { timeout: 120000 });
  await page.waitForTimeout(wait);
  const jpeg = out.endsWith(".jpg");
  await page.screenshot({ path: out, type: jpeg ? "jpeg" : "png", quality: jpeg ? 88 : undefined });
  await ctx.close();
  console.log("ok", out);
}

await shot(`${WORKSHOP}/atelier/`, { viewport: { width: 412, height: 915 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true },
  "/out/atelier_en.png", { ready: "#main:not(.hidden)", init: () => { localStorage.setItem("atelier.lang", "en"); localStorage.setItem("atelier.theme", "dark"); } });
await shot("file:///repo/demo/index.html?lang=en&t=0.4", { viewport: { width: 1280, height: 800 } }, "/out/demo.jpg", { wait: 4000 });
await shot("file:///repo/catalog/catalog.html", { viewport: { width: 1300, height: 900 } }, "/out/catalog.png", { wait: 2000 });
await browser.close();
