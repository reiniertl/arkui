// Build the four slides, in both languages, as .pptx and as .pdf.
//
//   npm run deck
//
// scene_data_deck.js wrote the .pptx and stopped there. The .pdf beside it
// in the repository had no producer at all - it was exported by hand once,
// from something that is not here - which is the same shape as the page
// generator that lived only in a scratch directory. An artefact nobody can
// rebuild is not an output.
//
// The .pdf step shells out to LibreOffice, which is the one input this
// script cannot declare in package.json. Where it is absent the .pptx is
// still written and the script says what is missing rather than failing
// silently: the deck is readable either way, and only the exported form is
// lost.
const { execFile } = require("child_process");
const fs = require("fs");
const path = require("path");
const { EN, ZH } = require("./scene_data_strings.cjs");
const { build } = require("./scene_data_deck.cjs");

const OUT = process.argv[2] || process.cwd();

function soffice() {
  for (const c of ["soffice", "libreoffice"]) {
    for (const dir of (process.env.PATH || "").split(path.delimiter)) {
      const p = path.join(dir, c);
      try { fs.accessSync(p, fs.constants.X_OK); return p; } catch { /* next */ }
    }
  }
  return null;
}

function toPdf(bin, pptx) {
  return new Promise((resolve) => {
    execFile(bin, ["--headless", "--convert-to", "pdf", "--outdir",
                   path.dirname(pptx), pptx], (err) => {
      if (err) { console.log("  pdf failed for " + path.basename(pptx)); resolve(false); }
      else { console.log("  " + path.basename(pptx, ".pptx") + ".pdf"); resolve(true); }
    });
  });
}

async function main() {
  const made = [];
  for (const [strings, name] of [[EN, "scene_data_en.pptx"], [ZH, "scene_data_zh.pptx"]]) {
    const out = path.join(OUT, name);
    await build(strings, out);
    made.push(out);
  }
  const bin = soffice();
  if (!bin) {
    console.log("  no soffice or libreoffice on PATH; the .pptx files are");
    console.log("  written and the .pdf export is skipped.");
    return;
  }
  for (const pptx of made) await toPdf(bin, pptx);
}

main().catch((e) => { console.error(e); process.exit(1); });
