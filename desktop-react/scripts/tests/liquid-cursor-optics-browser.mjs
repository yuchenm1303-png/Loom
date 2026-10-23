import assert from "node:assert/strict";

const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
try {
  const page = await browser.newPage();
  await page.goto(`${process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173"}/scripts/fixtures/cursor.html`);
  const results = await page.evaluate(async () => {
    const { liquidVertexShader, liquidFragmentShader, LIQUID_OPTICS } = await import("/src/components/portalLiquidOptics.ts");
    const canvas = document.createElement("canvas");
    const gl = canvas.getContext("webgl", { alpha: true, antialias: false });
    if (!gl) throw Error("WebGL unavailable");
    const compile = (type, source) => {
      const shader = gl.createShader(type);
      gl.shaderSource(shader, source); gl.compileShader(shader);
      if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) throw Error(gl.getShaderInfoLog(shader));
      return shader;
    };
    const program = gl.createProgram();
    gl.attachShader(program, compile(gl.VERTEX_SHADER, liquidVertexShader));
    gl.attachShader(program, compile(gl.FRAGMENT_SHADER, liquidFragmentShader));
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw Error(gl.getProgramInfoLog(program));
    gl.useProgram(program);
    const attribute = (name, data) => {
      const buffer = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
      gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(data), gl.STATIC_DRAW);
      const location = gl.getAttribLocation(program, name);
      gl.enableVertexAttribArray(location); gl.vertexAttribPointer(location, 2, gl.FLOAT, false, 0, 0);
    };
    attribute("a_position", [-1,-1, 1,-1, -1,1, -1,1, 1,-1, 1,1]);
    attribute("a_uv", [0,0, 1,0, 0,1, 0,1, 1,0, 1,1]);
    const scalar = (name, value) => gl.uniform1f(gl.getUniformLocation(program, name), value);
    const vector = (name, x, y) => gl.uniform2f(gl.getUniformLocation(program, name), x, y);
    const texture = gl.createTexture(); gl.bindTexture(gl.TEXTURE_2D, texture);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    const results = [];
    for (const [width, height] of [[60,34], [80,54], [180,40], [150,150]]) {
      for (const [dpr, roi, viewport, strength] of [[1,420,1280,.95], [1,900,3840,.95], [1.75,420,1280,.95], [1,420,1280,1.57]]) {
        const size = Math.round(roi * dpr);
        canvas.width = canvas.height = size;
        gl.viewport(0, 0, size, size);
        vector("u_resolution", size, size); vector("u_viewport", viewport, viewport * .7);
        vector("u_lensCenter", size / 2, size / 2); vector("u_lensSize", width * dpr, height * dpr);
        scalar("u_dpr", dpr); scalar("u_strength", strength); scalar("u_pinch", LIQUID_OPTICS.pinch);
        scalar("u_zoom", 1); scalar("u_wobble", 0); scalar("u_time", 0);
        const record = { width, height, dpr, roi, viewport, strength, backward: 0, displacement: 0, saturated: 0 };
        for (const pattern of ["x", "y", "stripes"]) {
          scalar("u_aberration", pattern === "stripes" ? LIQUID_OPTICS.aberration : 0);
          scalar("u_reflection", pattern === "stripes" ? LIQUID_OPTICS.reflection : 0);
          scalar("u_tint", pattern === "stripes" ? LIQUID_OPTICS.tint : 0);
          const data = new Uint8Array(size * size * 4);
          for (let y = 0; y < size; y++) for (let x = 0; x < size; x++) {
            const coord = ((pattern === "y" ? y : x) + .5 - size / 2) / dpr;
            const value = pattern === "stripes" ? ((Math.floor(coord / 6) & 1) ? 255 : 0) : Math.max(0, Math.min(255, Math.round(128 + coord)));
            const offset = (y * size + x) * 4;
            data[offset] = data[offset + 1] = data[offset + 2] = value; data[offset + 3] = 255;
          }
          gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, size, size, 0, gl.RGBA, gl.UNSIGNED_BYTE, data);
          gl.clearColor(0,0,0,0); gl.clear(gl.COLOR_BUFFER_BIT); gl.drawArrays(gl.TRIANGLES, 0, 6);
          const pixels = new Uint8Array(size * size * 4);
          gl.readPixels(0, 0, size, size, gl.RGBA, gl.UNSIGNED_BYTE, pixels);
          const at = (x, y) => (Math.floor(size - 1 - y) * size + Math.floor(x)) * 4;
          if (pattern === "stripes") {
            let count = 0, saturated = 0;
            for (let y = 0; y < size; y++) for (let x = 0; x < size; x++) {
              const i = at(x, y);
              if (pixels[i + 3] < 250) continue;
              count++;
              if (Math.max(pixels[i], pixels[i+1], pixels[i+2]) - Math.min(pixels[i], pixels[i+1], pixels[i+2]) > 80) saturated++;
            }
            record.saturated = saturated / count;
          } else {
            const extent = (pattern === "x" ? width : height) / 2 - 3;
            let peak = -1;
            for (let pos = -extent; pos <= extent; pos += 1 / dpr) {
              const x = size / 2 + (pattern === "x" ? pos * dpr : 0);
              const y = size / 2 + (pattern === "y" ? pos * dpr : 0);
              const value = pixels[at(x, y) + 1];
              if (peak >= 0) record.backward = Math.max(record.backward, peak - value);
              record.displacement = Math.max(record.displacement, Math.abs(value - (128 + pos)));
              peak = Math.max(peak, value);
            }
          }
        }
        results.push(record);
      }
    }
    return results;
  });
  console.log(JSON.stringify({ cases: results.length,
    maxReversal: Math.max(...results.map(result => result.backward)),
    maxSaturatedFraction: Math.max(...results.map(result => result.saturated)) }));
  for (const result of results) {
    const label = JSON.stringify(result);
    assert.ok(result.backward <= 2, `refraction must not fold/reverse a monotonic source: ${label}`);
    assert.ok(result.saturated < .015, `neutral stripes must not turn into rainbow bands: ${label}`);
    assert.ok(result.displacement >= 1, `keep visible optical bending: ${label}`);
  }
  for (let index = 0; index < results.length; index += 4) {
    assert.equal(results[index].saturated, results[index + 1].saturated,
      "changing viewport/capture size cannot change the fringe width");
    assert.equal(results[index].displacement, results[index + 1].displacement);
  }
  console.log("PASS: real shader pixels stay monotonic and almost achromatic across small/wide/round lenses, press, DPR, ROI and viewport sizes");
} finally { await browser.close(); }
