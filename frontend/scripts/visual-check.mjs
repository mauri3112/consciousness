import { chromium } from "playwright";

const url = process.env.CONSCIOUSNESS_STUDIO_URL ?? "http://localhost:5173";
const desktopScreenshotPath = process.env.CONSCIOUSNESS_SCREENSHOT ?? "../docs/assets/studio-render.png";
const mobileScreenshotPath = process.env.CONSCIOUSNESS_MOBILE_SCREENSHOT ?? "../docs/assets/studio-render-mobile.png";

const browser = await chromium.launch();

const errors = [];
const results = [];

for (const target of [
  { name: "desktop", path: desktopScreenshotPath, viewport: { width: 1512, height: 945 } },
  { name: "mobile", path: mobileScreenshotPath, viewport: { width: 390, height: 844 } }
]) {
  const page = await browser.newPage({
    viewport: target.viewport,
    deviceScaleFactor: 1
  });
  page.on("console", (message) => {
    if (message.type() === "error") {
      errors.push(`${target.name}: ${message.text()}`);
    }
  });
  page.on("pageerror", (error) => errors.push(`${target.name}: ${error.message}`));

  await page.goto(url, { waitUntil: "networkidle" });
  await page.screenshot({ path: target.path, fullPage: true });

  results.push({
    target: target.name,
    currentText: await page.locator(".state-node.current").textContent(),
    panelCount: await page.locator(".graph-panel, .inspector, .data-panel").count()
  });
  await page.close();
}

await browser.close();

if (results.some((result) => !result.currentText || result.panelCount < 5) || errors.length > 0) {
  console.error(
    JSON.stringify(
      {
        results,
        errors
      },
      null,
      2
    )
  );
  process.exit(1);
}

console.log(`Visual check passed for ${url}; screenshots: ${desktopScreenshotPath}, ${mobileScreenshotPath}`);
