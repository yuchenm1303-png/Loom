import { clipboard, ClipboardItem, type NativeImage } from "electron";

// Electron 44 removed clipboard.writeImage; images go through ClipboardItem,
// which puts a bitmap on the system clipboard like navigator.clipboard does.
export async function writeClipboardImage(image: NativeImage): Promise<void> {
  const png = new Blob([new Uint8Array(image.toPNG())], { type: "image/png" });
  await clipboard.write([new ClipboardItem({ "image/png": png })]);
}
