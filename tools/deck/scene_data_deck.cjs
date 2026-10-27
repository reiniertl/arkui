// Four slides for someone else's deck: no title slide, no section dividers,
// no footer and no slide numbers, so each one drops into another deck
// without carrying our chrome in with it.
const pptxgen = require("pptxgenjs");
const { EN, ZH } = require("./scene_data_strings.cjs");

const INK = "0E1117", MUTE = "5E6C82", LINE = "D7DEE8", SOFT = "F4F7FA";
const TEAL = "2FA8A0", AMBER = "D98A24", DIM = "9AA7B8";
const W = 13.3, H = 7.5, M = 0.7, CW = W - 2 * M;

function build(T, out) {
  const pres = new pptxgen();
  pres.layout = "LAYOUT_WIDE";
  pres.author = "Reinier Torres Labrada";
  pres.company = "Scene classification signals";
  pres.title = T.s1title;
  pres.theme = { headFontFace: T.head, bodyFontFace: T.font };

  let n = 0;
  const on = (s) => `${s}-${++n}`;
  const S = () => pres.addSlide();

  function title(s, text, sub) {
    s.addText(text, { x: M, y: 0.44, w: CW, h: 0.6, fontSize: 28, bold: true,
      color: INK, fontFace: T.head, isTextBox: true, margin: 0, objectName: on("t") });
    if (sub) {
      s.addText(sub, { x: M, y: 1.08, w: CW - 0.4, h: 0.5, fontSize: 13.5,
        color: MUTE, fontFace: T.font, isTextBox: true, margin: 0,
        valign: "top", objectName: on("s") });
    }
  }
  function card(s, x, y, w, h, fill, line) {
    s.addShape(pres.ShapeType.roundRect, { x, y, w, h, rectRadius: 0.08,
      fill: { color: fill || SOFT }, line: { color: line || LINE, width: 1 },
      objectName: on("c") });
  }
  function note(s, y, text, color) {
    s.addShape(pres.ShapeType.rect, { x: M, y, w: 0.045, h: 0.62,
      fill: { color: color || TEAL }, line: { width: 0 }, objectName: on("bar") });
    s.addText(text, { x: M + 0.22, y, w: CW - 0.3, h: 0.62, fontSize: 12,
      color: MUTE, fontFace: T.font, isTextBox: true, margin: 0, valign: "middle",
      objectName: on("note") });
  }
  function arrow(s, x, y) {
    s.addText("→", { x, y, w: 0.4, h: 0.4, fontSize: 20, color: DIM,
      align: "center", valign: "middle", isTextBox: true, margin: 0,
      objectName: on("ar") });
  }

  // ---- 1. where the data comes from ------------------------------------
  {
    const s = S();
    title(s, T.s1title, T.s1sub);
    const steps = [[T.p1h, T.p1b], [T.p2h, T.p2b], [T.p3h, T.p3b], [T.p4h, T.p4b]];
    const bw = 2.72, gap = (CW - 4 * bw) / 3, by = 2.0, bh = 1.65;
    steps.forEach((st, i) => {
      const x = M + i * (bw + gap);
      card(s, x, by, bw, bh, i === 3 ? "EAF6F5" : SOFT, i === 3 ? TEAL : LINE);
      s.addText(st[0], { x: x + 0.22, y: by + 0.18, w: bw - 0.44, h: 0.32,
        fontSize: 14, bold: true, color: i === 3 ? "19635E" : INK,
        fontFace: T.head, isTextBox: true, margin: 0, objectName: on("ph") });
      s.addText(st[1], { x: x + 0.22, y: by + 0.56, w: bw - 0.44, h: 0.9,
        fontSize: 11.5, color: MUTE, fontFace: T.font, isTextBox: true,
        margin: 0, valign: "top", lineSpacingMultiple: 1.1, objectName: on("pb") });
      if (i < 3) arrow(s, x + bw + gap / 2 - 0.2, by + bh / 2 - 0.2);
    });
    const kp = [[T.k1, T.k1l], [T.k2, T.k2l], [T.k3, T.k3l], [T.k4, T.k4l]];
    const ky = 4.15, kw = CW / 4;
    card(s, M, ky, CW, 1.0);
    kp.forEach((k, i) => {
      const x = M + i * kw;
      if (i) s.addShape(pres.ShapeType.line, { x, y: ky + 0.16, w: 0, h: 0.68,
        line: { color: LINE, width: 1 }, objectName: on("sep") });
      s.addText(k[0], { x: x + 0.3, y: ky + 0.12, w: kw - 0.5, h: 0.42,
        fontSize: 22, bold: true, color: TEAL, fontFace: T.head,
        isTextBox: true, margin: 0, objectName: on("kn") });
      s.addText(k[1], { x: x + 0.3, y: ky + 0.56, w: kw - 0.5, h: 0.3,
        fontSize: 11, color: MUTE, fontFace: T.font, isTextBox: true,
        margin: 0, objectName: on("kl") });
    });
    note(s, 5.5, T.s1note);
  }

  // ---- 2. what a scene is ----------------------------------------------
  {
    const s = S();
    title(s, T.s2title);
    card(s, M, 1.26, CW, 1.28, "EAF6F5", TEAL);
    s.addText(T.s2def, { x: M + 0.3, y: 1.42, w: CW - 0.6, h: 0.56, fontSize: 14,
      bold: true, color: "16403D", fontFace: T.font, isTextBox: true, margin: 0,
      valign: "top", objectName: on("def") });
    s.addText(T.s2defb, { x: M + 0.3, y: 2.02, w: CW - 0.6, h: 0.4, fontSize: 11.5,
      color: "3E6F6B", fontFace: T.font, isTextBox: true, margin: 0,
      objectName: on("defb") });
    const gs = [[T.g1h, T.g1n, T.g1b, TEAL], [T.g2h, T.g2n, T.g2b, INK],
                [T.g3h, T.g3n, T.g3b, INK], [T.g4h, T.g4n, T.g4b, INK],
                [T.g5h, T.g5n, T.g5b, MUTE]];
    const gw = (CW - 4 * 0.2) / 5, gy = 2.95, gh = 2.4;
    gs.forEach((g, i) => {
      const x = M + i * (gw + 0.2);
      card(s, x, gy, gw, gh, i === 0 ? "EAF6F5" : SOFT, i === 0 ? TEAL : LINE);
      s.addText(g[1], { x: x + 0.18, y: gy + 0.16, w: gw - 0.36, h: 0.42,
        fontSize: 20, bold: true, color: g[3], fontFace: T.head, isTextBox: true,
        margin: 0, objectName: on("gn") });
      s.addText(g[0], { x: x + 0.18, y: gy + 0.6, w: gw - 0.36, h: 0.34,
        fontSize: 12.5, bold: true, color: INK, fontFace: T.head, isTextBox: true,
        margin: 0, objectName: on("gh") });
      s.addText(g[2], { x: x + 0.18, y: gy + 0.98, w: gw - 0.36, h: 1.3,
        fontSize: 10.5, color: MUTE, fontFace: T.font, isTextBox: true, margin: 0,
        valign: "top", lineSpacingMultiple: 1.08, objectName: on("gb") });
    });
    note(s, 5.6, T.s2note);
  }

  // ---- 3. samples to clustering input ----------------------------------
  {
    const s = S();
    title(s, T.s3title);
    const fs = [[T.f1n, T.f1h, T.f1b], [T.f2n, T.f2h, T.f2b], [T.f3n, T.f3h, T.f3b]];
    const fw = 3.5, fgap = (CW - 3 * fw) / 2, fy = 1.35, fh = 1.6;
    fs.forEach((f, i) => {
      const x = M + i * (fw + fgap), last = i === 2;
      card(s, x, fy, fw, fh, last ? "EAF6F5" : SOFT, last ? TEAL : LINE);
      s.addText(f[0], { x: x + 0.26, y: fy + 0.2, w: fw - 0.5, h: 0.52,
        fontSize: 27, bold: true, color: last ? TEAL : INK, fontFace: T.head,
        isTextBox: true, margin: 0, objectName: on("fn") });
      s.addText(f[1], { x: x + 0.26, y: fy + 0.74, w: fw - 0.5, h: 0.3,
        fontSize: 12.5, bold: true, color: INK, fontFace: T.head, isTextBox: true,
        margin: 0, objectName: on("fh") });
      s.addText(f[2], { x: x + 0.26, y: fy + 1.04, w: fw - 0.5, h: 0.42,
        fontSize: 10.5, color: MUTE, fontFace: T.font, isTextBox: true, margin: 0,
        valign: "top", objectName: on("fb") });
      if (i < 2) arrow(s, x + fw + fgap / 2 - 0.2, fy + fh / 2 - 0.2);
    });
    card(s, M, 3.35, CW, 1.95);
    s.addText(T.s3rule, { x: M + 0.3, y: 3.5, w: CW - 0.6, h: 0.36, fontSize: 14,
      bold: true, color: INK, fontFace: T.head, isTextBox: true, margin: 0,
      objectName: on("rh") });
    [[T.s3r1, 3.94], [T.s3r2, 4.52]].forEach(([tx, y], i) => {
      s.addShape(pres.ShapeType.ellipse, { x: M + 0.32, y: y + 0.09, w: 0.18, h: 0.18,
        fill: { color: i ? AMBER : TEAL }, line: { width: 0 }, objectName: on("bd") });
      s.addText(tx, { x: M + 0.66, y, w: CW - 1.1, h: 0.5, fontSize: 12.5,
        color: "2A3340", fontFace: T.font, isTextBox: true, margin: 0,
        valign: "top", objectName: on("rb") });
    });
    note(s, 5.6, T.s3note);
  }

  // ---- 4. results -------------------------------------------------------
  {
    const s = S();
    title(s, T.s4title, T.s4sub);
    const rows = [[T.r1a, T.r1b, T.r1c, T.r1d, 0], [T.r2a, T.r2b, T.r2c, T.r2d, 0],
                  [T.r3a, T.r3b, T.r3c, T.r3d, 1], [T.r4a, T.r4b, T.r4c, T.r4d, 0]];
    const tx = M, tw = 6.5, ty = 1.95, rh = 0.46;
    const colx = [0.26, 3.1, 4.4, 5.6], colw = [2.8, 1.2, 1.1, 0.8];
    [T.th1, T.th2, T.th3, T.th4].forEach((h2, i) => {
      s.addText(h2, { x: tx + colx[i], y: ty, w: colw[i], h: 0.34, fontSize: 9.5,
        bold: true, color: MUTE, fontFace: T.head, charSpacing: 0.6,
        align: i ? "right" : "left", isTextBox: true, margin: 0, objectName: on("th") });
    });
    s.addShape(pres.ShapeType.line, { x: tx, y: ty + 0.36, w: tw, h: 0,
      line: { color: LINE, width: 1 }, objectName: on("hr") });
    rows.forEach((r, i) => {
      const y = ty + 0.44 + i * rh;
      if (r[4]) card(s, tx - 0.04, y - 0.04, tw + 0.08, rh - 0.02, "EAF6F5", TEAL);
      r.slice(0, 4).forEach((v, j) => {
        s.addText(v, { x: tx + colx[j], y, w: colw[j], h: rh - 0.06,
          fontSize: 12.5, bold: !!r[4], color: r[4] ? "19635E" : INK,
          fontFace: T.font, align: j ? "right" : "left", valign: "middle",
          isTextBox: true, margin: 0, objectName: on("td") });
      });
      if (!r[4]) s.addShape(pres.ShapeType.line, { x: tx, y: y + rh - 0.05, w: tw, h: 0,
        line: { color: "EDF1F5", width: 1 }, objectName: on("rr") });
    });
    const cs = [[T.c1h, T.c1b, TEAL], [T.c2h, T.c2b, AMBER], [T.c3h, T.c3b, INK]];
    const cx = M + 7.0, cw = CW - 7.0, chh = 1.33;
    cs.forEach((c, i) => {
      const y = 1.95 + i * (chh + 0.19);
      card(s, cx, y, cw, chh);
      s.addShape(pres.ShapeType.rect, { x: cx, y, w: 0.045, h: chh,
        fill: { color: c[2] }, line: { width: 0 }, objectName: on("cb") });
      s.addText(c[0], { x: cx + 0.26, y: y + 0.14, w: cw - 0.5, h: 0.32,
        fontSize: 13, bold: true, color: INK, fontFace: T.head, isTextBox: true,
        margin: 0, objectName: on("ch") });
      s.addText(c[1], { x: cx + 0.26, y: y + 0.48, w: cw - 0.5, h: 0.76,
        fontSize: 11, color: MUTE, fontFace: T.font, isTextBox: true, margin: 0,
        valign: "top", lineSpacingMultiple: 1.1, objectName: on("cbo") });
    });
  }

  return pres.writeFile({ fileName: out }).then(() => console.log("  " + out));
}

module.exports = { build };

// Still runnable on its own, which is how it was used before build.js
// existed; build.js imports it rather than replacing it.
if (require.main === module) {
  build(EN, "scene_data_en.pptx")
    .then(() => build(ZH, "scene_data_zh.pptx"))
    .catch((e) => { console.error(e); process.exit(1); });
}
