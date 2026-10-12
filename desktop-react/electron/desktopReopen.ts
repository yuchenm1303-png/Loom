export async function reopenDesktop(launch: () => void, connected: () => boolean, delayMs = 1000, attempts = 30): Promise<boolean> {
  for (let attempt = 0; attempt < attempts; attempt++) {
    if (connected()) return true;
    launch();
    await new Promise(resolve => setTimeout(resolve, delayMs));
  }
  return connected();
}
