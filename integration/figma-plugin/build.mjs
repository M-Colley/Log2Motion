import { build } from "esbuild";
import { mkdir, readFile, writeFile } from "node:fs/promises";

await mkdir("dist", { recursive: true });
await build({ entryPoints: ["code.ts"], outfile: "dist/code.js", bundle: true, target: "es2020" });
const chart = await build({
  stdin: { contents: 'import Chart from "chart.js/auto"; globalThis.Chart = Chart;', resolveDir: process.cwd() },
  bundle: true, write: false, minify: true, target: "es2020", format: "iife",
});
const html = await readFile("ui.html", "utf8");
const marker = '<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>';
if (!html.includes(marker)) throw new Error("Chart.js script marker is missing from ui.html");
await writeFile("dist/ui.html", html.replace(marker, () => `<script>${chart.outputFiles[0].text.replaceAll("</script", "<\\/script")}</script>`));
