import fs from "node:fs/promises";
import path from "node:path";
import { createRequire } from "node:module";

const [anchor, source, configuration, output, widthText, heightText, background = "white"] = process.argv.slice(2);
const require = createRequire(path.resolve(anchor));
const puppeteer = require("puppeteer");
const width = Number(widthText);
const height = Number(heightText);
const browser = await puppeteer.launch(JSON.parse(await fs.readFile(configuration, "utf8")));
try {
  const page = await browser.newPage();
  await page.setViewport({ width: 2400, height: 2400, deviceScaleFactor: 1 });
  await page.setContent(
    '<html><head><style>body{margin:0;overflow:hidden}svg{position:absolute;left:0;top:0}</style></head><body>'
      + await fs.readFile(source, "utf8") + "</body></html>",
    { waitUntil: "load" },
  );
  await page.evaluate(async (dimensions) => {
    document.body.style.backgroundColor = dimensions.background;
    const svg = document.querySelector("svg");
    svg.style.width = `${dimensions.width}px`;
    svg.style.height = `${dimensions.height}px`;
    svg.style.maxWidth = "none";
    await document.fonts.ready;
  }, { width, height, background });
  await fs.mkdir(output, { recursive: true });
  const tiles = [];
  for (let top = 0; top < height; top += 2400) {
    for (let left = 0; left < width; left += 2400) {
      await page.evaluate(async (offset) => {
        document.querySelector("svg").style.transform =
          `translate(-${offset.left}px, -${offset.top}px)`;
        await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      }, { left, top });
      const filename = `tile-${left}-${top}.png`;
      await page.screenshot({
        path: path.join(output, filename),
        clip: {
          x: 0, y: 0,
          width: Math.min(2400, width - left),
          height: Math.min(2400, height - top),
        },
      });
      tiles.push({ filename, left, top });
    }
  }
  await fs.writeFile(path.join(output, "tiles.json"), JSON.stringify(tiles));
} finally {
  await browser.close();
}
