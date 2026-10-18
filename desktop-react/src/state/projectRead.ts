/** Bound passive project reads without retrying potentially destructive Git actions. */
export async function withProjectReadTimeout<T>(request: Promise<T>, timeoutMs = 20_000): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([
      request,
      new Promise<never>((_, reject) => {
        timer = setTimeout(() => reject(new Error("项目读取超时，请重试；若持续失败，请检查本地主机连接。")), timeoutMs);
      }),
    ]);
  } finally {
    clearTimeout(timer);
  }
}
