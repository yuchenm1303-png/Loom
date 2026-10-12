export async function reopenDesktop(launch: () => void, visible: () => boolean, delayMs = 1000, attempts = 30): Promise<boolean> {
  for (let attempt = 0; attempt < attempts; attempt++) {
    if (visible()) return true;
    launch();
    await new Promise(resolve => setTimeout(resolve, delayMs));
  }
  return visible();
}
