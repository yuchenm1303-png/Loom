export const THREAD_READ_TIMEOUT_MS = 15_000;

// Bound the shared read itself so prefetch and retries cannot reuse a stuck
// promise. Settling this wrapper also prevents late replies from being applied.
export function withThreadReadTimeout<T>(request: Promise<T>): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(() => {
      reject(new Error("对话加载超时，请再次点击该对话重试。"));
    }, THREAD_READ_TIMEOUT_MS);
    request.then(
      (result) => { clearTimeout(timer); resolve(result); },
      (error: unknown) => { clearTimeout(timer); reject(error); },
    );
  });
}
