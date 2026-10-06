import { crc32, inflateSync } from "node:zlib";

// Browsers render damaged PNG data as blank or partial images without failing
// decode(). A single flipped bit in the cursor character once showed up as a
// "clipped" native cursor, so verify chunk CRCs and the image stream directly.
export function assertIntactPng(name, bytes) {
  if (bytes.subarray(0, 8).toString("hex") !== "89504e470d0a1a0a") throw new Error(`${name}: not a PNG file`);
  const imageData = [];
  let offset = 8;
  let type = "";
  while (type !== "IEND") {
    if (offset + 12 > bytes.length) throw new Error(`${name}: truncated PNG`);
    const end = offset + 8 + bytes.readUInt32BE(offset);
    if (end + 4 > bytes.length) throw new Error(`${name}: truncated PNG chunk`);
    const chunk = bytes.subarray(offset + 4, end);
    type = chunk.toString("latin1", 0, 4);
    if (crc32(chunk) !== bytes.readUInt32BE(end)) throw new Error(`${name}: ${type} chunk CRC mismatch`);
    if (type === "IDAT") imageData.push(chunk.subarray(4));
    offset = end + 4;
  }
  try {
    inflateSync(Buffer.concat(imageData));
  } catch (error) {
    throw new Error(`${name}: damaged image data (${error.message})`);
  }
}
