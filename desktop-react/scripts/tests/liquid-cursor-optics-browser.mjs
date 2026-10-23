import assert from "node:assert/strict";

const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
try {
  const page = await browser.newPage();
  await page.goto(`${process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173"}/scripts/fixtures/cursor.html`);
  const results = await page.evaluate(async () => {
    const { liquidVertexShader, liquidFragmentShader, createReferenceLensRenderer } = await import("/src/components/portalLiquidOptics.ts");
    const canvas = document.createElement("canvas");
    const gl = canvas.getContext("webgl", { alpha: true, antialias: false });
    if (!gl) throw Error("WebGL unavailable");
    gl.getExtension("OES_standard_derivatives");
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
    attribute("a_texCoord", [0,0, 1,0, 0,1, 0,1, 1,0, 1,1]);
    const texture = gl.createTexture(); gl.bindTexture(gl.TEXTURE_2D, texture);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    const draw = createReferenceLensRenderer(gl, program);
    const uniform = name => {
      const value = gl.getUniform(program, gl.getUniformLocation(program, name));
      return ArrayBuffer.isView(value) ? Array.from(value) : value;
    };
    const results = [];
    for (const [width, height] of [[60,34], [80,54], [180,40], [150,150]]) {
      for (const [screenDpr, captureDpr, roi] of [[1,1,260], [2,1.75,260], [1.5,1,420]]) {
        const size = Math.round(roi * captureDpr);
        canvas.width = canvas.height = size;
        gl.viewport(0, 0, size, size);
        const source = document.createElement("canvas");
        source.width = source.height = size;
        const ctx = source.getContext("2d");
        // An asymmetric source detects a flipped texture or wrong ROI origin.
        ctx.fillStyle = "#e02020"; ctx.fillRect(0, 0, size, size / 2);
        ctx.fillStyle = "#2030e0"; ctx.fillRect(0, size / 2, size, size / 2);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, source);
        const input = { roiWidth: roi, roiHeight: roi, centerX: roi * .6, centerY: roi * .25,
          width, height, screenDpr, time: 0 };
        draw(input);
        const pixel = new Uint8Array(4);
        gl.readPixels(Math.floor(size * .6), Math.floor(size * .75), 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, pixel);
        results.push({ input, pixel: Array.from(pixel), positions: uniform("u_positions[0]"),
          resolution: uniform("u_resolution"), sizes: uniform("u_lensSizes[0]"),
          merge: uniform("u_mergeMode"), reflections: uniform("u_enableReflections"), error: gl.getError() });
      }
    }
    return results;
  });
  for (const result of results) {
    const { input, positions, resolution, sizes, pixel } = result;
    assert.equal(result.error, 0);
    assert.ok(Math.abs(positions[0] - .6) < .0001 && Math.abs(positions[1] - .75) < .0001);
    assert.deepEqual(resolution, [Math.round(input.roiWidth * input.screenDpr), Math.round(input.roiHeight * input.screenDpr)]);
    assert.deepEqual(sizes, [input.width * input.screenDpr, input.height * input.screenDpr]);
    assert.equal(result.merge, true);
    assert.equal(result.reflections, false);
    assert.ok(pixel[0] > 180 && pixel[2] < 100 && pixel[3] > 250, "top-half red source must remain correctly oriented at the lens centre");
  }
  console.log(`PASS ${results.length} cases: original shader compiles; source uniform/ROI/DPR contract and asymmetric texture orientation match`);
} finally { await browser.close(); }
