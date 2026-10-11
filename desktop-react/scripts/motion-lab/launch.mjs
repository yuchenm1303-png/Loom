// Shared launcher for the motion lab. LOOM_BENCH_HEADED=1 opens a real (mostly off-screen) Chrome window instead of
// headless, which is what gives GPU compositing and the display's refresh rate: headless runs every animation on the
// main thread and is not representative of what the user sees.
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
export const headed = process.env.LOOM_BENCH_HEADED === "1";
export function launch() {
  return chromium.launch({
    executablePath: process.env.LOOM_CHROMIUM_PATH,
    headless: !headed,
    args: headed ? ["--window-position=2450,0", "--disable-features=CalculateNativeWinOcclusion", "--disable-backgrounding-occluded-windows", "--disable-renderer-backgrounding", "--disable-background-timer-throttling"] : [],
  });
}
