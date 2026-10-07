import assert from "node:assert/strict";
import { test } from "node:test";
import { serialize } from "node:v8";
import { HostFrameDecoder } from "../../dist-electron/hostTransport.js";

function encode(value) {
  const body = serialize(value);
  const header = Buffer.alloc(4);
  header.writeUInt32BE(body.length);
  return Buffer.concat([header, body]);
}

test("Host framing preserves split headers, UTF-8, binary data and multiple frames per chunk", () => {
  const frames = [{ type: "event", value: "中文 🚀" }, { type: "result", value: new Uint8Array([0, 255, 42]) }];
  const stream = Buffer.concat(frames.map(encode));
  for (const size of [1, 3, 4, 7, 31, stream.length]) {
    const received = [];
    const decoder = new HostFrameDecoder(frame => received.push(frame));
    for (let offset = 0; offset < stream.length; offset += size) decoder.push(stream.subarray(offset, offset + size));
    assert.deepEqual(received, frames);
  }
});

test("large fragmented history is copied once, without quadratic prefix assembly", t => {
  const value = { type: "result", value: "history\n".repeat(1024 * 1024) };
  const encoded = encode(value);
  const concat = Buffer.concat;
  let copiedBytes = 0;
  t.mock.method(Buffer, "concat", (parts, length) => {
    copiedBytes += length ?? parts.reduce((size, part) => size + part.length, 0);
    return concat(parts, length);
  });
  const received = [];
  const decoder = new HostFrameDecoder(frame => received.push(frame));
  for (let offset = 0; offset < encoded.length; offset += 16 * 1024) decoder.push(encoded.subarray(offset, offset + 16 * 1024));
  assert.deepEqual(received, [value]);
  assert.ok(copiedBytes <= encoded.length, `copied ${copiedBytes} bytes for a ${encoded.length}-byte history response`);
});

test("invalid frames are rejected and disconnected readers release incomplete fragments", () => {
  for (const length of [0, 64 * 1024 * 1024 + 1]) {
    const header = Buffer.alloc(4); header.writeUInt32BE(length);
    assert.throws(() => new HostFrameDecoder(() => {}).push(header), /frame length/);
  }
  assert.throws(() => new HostFrameDecoder(() => {}).push(encode({ value: "missing type" })), /Invalid Host message/);
  const decoder = new HostFrameDecoder(frame => assert.equal(frame.type, "ready"));
  decoder.push(encode({ type: "result", value: "discarded partial data" }).subarray(0, 10));
  decoder.reset();
  decoder.push(encode({ type: "ready" }));
});
