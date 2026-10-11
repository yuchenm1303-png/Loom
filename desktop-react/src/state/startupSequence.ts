/**
 * What the window waits for between launching and showing its conversations.
 *
 * The runtime connection comes first and cannot be skipped. Everything after it
 * used to be awaited one step at a time: models, then projects, then threads. The
 * model list involves provider catalogues and an account round trip, so on a poor
 * network it alone held the sidebar empty for seconds, and nothing in it is needed
 * to list conversations. The reads are independent, so they are issued together,
 * and the window is ready once the conversation list and projects are here.
 */

/** Where launch is: waiting for the local runtime, or loading what it holds. */
export type StartupStage = "runtime" | "library";

export interface StartupSteps<Initialized, Thread> {
  connect(): Promise<Initialized>;
  /** The runtime answered; the conversation library is next. */
  connected(initialized: Initialized): void;
  loadModels(): Promise<unknown>;
  loadProjects(): Promise<unknown>;
  loadThreads(): Promise<readonly Thread[]>;
  /** Conversations and projects are loaded; the window can be used. */
  ready(threads: readonly Thread[]): void;
  openFirst(thread: Thread): Promise<unknown>;
  /** Any step failing is fatal, wherever it fails: models included. */
  failed(cause: unknown): void;
  cancelled(): boolean;
}

export async function runStartup<Initialized, Thread>(steps: StartupSteps<Initialized, Thread>): Promise<void> {
  try {
    const initialized = await steps.connect();
    if (steps.cancelled()) return;
    steps.connected(initialized);

    // Not awaited: it settles in its own time. A failure is still a startup
    // failure, as it was when this waited in front of everything else. It is
    // started a turn later than the two reads below so those are queued first.
    void Promise.resolve()
      .then(() => steps.loadModels())
      .catch((cause: unknown) => {
        if (!steps.cancelled()) steps.failed(cause);
      });

    const [, threads] = await Promise.all([steps.loadProjects(), steps.loadThreads()]);
    if (steps.cancelled()) return;
    steps.ready(threads);
    if (threads.length) await steps.openFirst(threads[0]);
  } catch (cause) {
    if (!steps.cancelled()) steps.failed(cause);
  }
}
