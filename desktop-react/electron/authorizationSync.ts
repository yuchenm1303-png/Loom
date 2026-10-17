import { latestSync } from "./latestSync.js";

/** Independent credential lanes, scoped to the identity that requested them. */
export function authorizationSync(options: {
  epoch: () => string;
  ready: () => boolean;
  automation: (forceRefresh?: boolean) => Promise<string>;
  model: (forceRefresh?: boolean) => Promise<string>;
  deliver: (value: { credential?: string; modelCredential?: string }) => Promise<unknown>;
}): (forceRefresh?: boolean) => Promise<void> {
  let refreshRequested = false;
  let modelRefreshRequested = false;
  const lane = (read: () => Promise<string>, field: "credential" | "modelCredential") => latestSync(async () => {
    if (!options.ready()) return;
    const epoch = options.epoch();
    try {
      const credential = await read();
      if (epoch !== options.epoch() || !options.ready()) return;
      if (field === "modelCredential") {
        // Older Hosts interpret an omitted account credential as sign-out.
        // Keep the model lane's wire envelope compatible without making the
        // automation lane wait for model issuance. Recheck identity after read.
        const account = await options.automation(false);
        if (epoch !== options.epoch() || !options.ready()) return;
        await options.deliver({ credential: account, modelCredential: account ? credential : "" });
      } else {
        await options.deliver(credential ? { credential } : { credential: "", modelCredential: "" });
      }
    } catch {
      // Transient errors are not logout. Never publish an empty credential
      // just because a network refresh failed; explicit logout delivers "".
    }
  });
  const automation = lane(async () => {
    const force = refreshRequested;
    refreshRequested = false;
    try { return await options.automation(force); }
    catch (error) { refreshRequested ||= force; throw error; }
  }, "credential");
  const model = lane(async () => {
    const force = modelRefreshRequested;
    modelRefreshRequested = false;
    try { return await options.model(force); }
    catch (error) { modelRefreshRequested ||= force; throw error; }
    finally {
      // Issuing a model token can rotate the account access token on a 401.
      // Publish that rotation without making the first automation delivery wait.
      void automation();
    }
  }, "modelCredential");
  return async (forceRefresh = false) => {
    refreshRequested ||= forceRefresh;
    modelRefreshRequested ||= forceRefresh;
    await Promise.all([automation(), model()]);
  };
}
