import { clipboard, type NativeImage } from "electron";

// Electron 44+ exposes ClipboardItem with the async clipboard API; Electron
// 43 (last Windows ia32 release) still exposes the legacy native image API.
// Do not statically import ClipboardItem: a missing named export crashes the
// entire 32-bit Host before the first Loom window opens.
export async function writeClipboardImage(image: NativeImage): Promise<void> {
  const electron = await import("electron");
  if (typeof electron.ClipboardItem === "function") {
    const png = new Blob([new Uint8Array(image.toPNG())], { type: "image/png" });
    await clipboard.write([new electron.ClipboardItem({ "image/png": png })]);
    return;
  }

  const legacy = clipboard as unknown as { writeImage(value: NativeImage): void };
  if (typeof legacy.writeImage !== "function") {
    throw new Error("This Electron version cannot write an image to the clipboard.");
  }
  legacy.writeImage(image);
}
