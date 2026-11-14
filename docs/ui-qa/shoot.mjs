// usage: node shoot.mjs <outdir> [themes=light,dark] [sizes=1920x1080,1280x720] [views=...] [sources=hl,ds,sample]
import { chromium } from "playwright";
import fs from "node:fs";
const [out, themesArg = "light,dark", sizesArg = "1920x1080,1280x720", viewsArg, srcArg = "hl,ds,sample"] = process.argv.slice(2);
const views = (viewsArg || "mission,pipeline,requirements,design,contract,scenarios,files,studio").split(",");
const SRC = { hl: "http://127.0.0.1:8081/", ds: "http://127.0.0.1:8082/", sample: "http://127.0.0.1:8081/?sample=1&" };
fs.mkdirSync(out, { recursive: true });
const browser = await chromium.launch({ args: ["--use-gl=swiftshader", "--enable-unsafe-swiftshader"] });
const errors = [];
for (const src of srcArg.split(","))
  for (const theme of themesArg.split(","))
    for (const size of sizesArg.split(",")) {
      const [w, h] = size.split("x").map(Number);
      const ctx = await browser.newContext({ viewport: { width: w, height: h }, colorScheme: theme === "dark" ? "dark" : "light" });
      for (const view of views) {
        const page = await ctx.newPage();
        page.on("console", (m) => { if (["error", "warning"].includes(m.type()) && !/fonts\.g|ERR_/.test(m.text())) errors.push(`${src}/${view}: ${m.text().slice(0, 150)}`); });
        page.on("pageerror", (e) => errors.push(`${src}/${view}: PAGEERROR ${e.message}`));
        const base = SRC[src];
        const q = (base.includes("?") ? "" : "?") + `theme=${theme}`;
        await page.goto(`${base}${q}#/${view}`, { waitUntil: "load" });
        await page.waitForTimeout(view === "design" || view === "files" ? 3500 : 1800);
        await page.screenshot({ path: `${out}/${src}-${theme}-${size}-${view}.png`, fullPage: true });
        await page.close();
      }
      await ctx.close();
    }
await browser.close();
console.log(errors.length ? [...new Set(errors)].join("\n") : "no console errors");
