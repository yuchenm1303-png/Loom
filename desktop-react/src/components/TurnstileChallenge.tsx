import { useEffect, useRef } from "react";

/** Cloudflare Turnstile explicit-render API. Only the public sitekey is used. */
type TurnstileAPI = {
  render: (node: HTMLElement, options: {
    sitekey: string;
    action: string;
    theme: "dark" | "light";
    size: "normal";
    callback: (token: string) => void;
    "expired-callback": () => void;
    "error-callback": () => void;
  }) => string;
  remove: (id: string) => void;
  reset: (id: string) => void;
};
type TurnstileWindow = Window & { turnstile?: TurnstileAPI };

const SCRIPT_ID = "loom-turnstile-script";
const SCRIPT_URL = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";
let scriptReady: Promise<TurnstileAPI> | null = null;

function loadTurnstile(): Promise<TurnstileAPI> {
  if ((window as TurnstileWindow).turnstile) return Promise.resolve((window as TurnstileWindow).turnstile!);
  if (scriptReady) return scriptReady;
  scriptReady = new Promise<TurnstileAPI>((resolve, reject) => {
    const existing = document.getElementById(SCRIPT_ID) as HTMLScriptElement | null;
    const script = existing || document.createElement("script");
    const onReady = () => {
      const api = (window as TurnstileWindow).turnstile;
      if (api) resolve(api);
      else reject(new Error("Cloudflare Turnstile did not initialize"));
    };
    const onFailure = () => reject(new Error("Cloudflare Turnstile could not load"));
    script.addEventListener("load", onReady, { once: true });
    script.addEventListener("error", onFailure, { once: true });
    if (!existing) {
      script.id = SCRIPT_ID;
      script.src = SCRIPT_URL;
      script.async = true;
      script.defer = true;
      document.head.appendChild(script);
    }
  }).catch((error: unknown) => {
    scriptReady = null;
    document.getElementById(SCRIPT_ID)?.remove();
    throw error;
  });
  return scriptReady;
}

export function TurnstileChallenge(props: {
  siteKey: string;
  theme: "dark" | "light";
  refreshKey: number;
  onToken: (token: string) => void;
  onError: () => void;
}) {
  const target = useRef<HTMLDivElement>(null);
  const callbacks = useRef({ onToken: props.onToken, onError: props.onError });
  callbacks.current = { onToken: props.onToken, onError: props.onError };

  useEffect(() => {
    const node = target.current;
    if (!node || !props.siteKey) return;
    let active = true;
    let widgetId: string | null = null;
    let api: TurnstileAPI | null = null;
    callbacks.current.onToken("");
    void loadTurnstile().then((ready) => {
      if (!active || !node.isConnected) return;
      api = ready;
      widgetId = ready.render(node, {
        sitekey: props.siteKey,
        action: "loom_auth",
        theme: props.theme,
        size: "normal",
        callback: (token) => { if (active) callbacks.current.onToken(token); },
        "expired-callback": () => { if (active) callbacks.current.onToken(""); },
        "error-callback": () => {
          if (active) {
            callbacks.current.onToken("");
            callbacks.current.onError();
          }
        },
      });
    }).catch(() => { if (active) callbacks.current.onError(); });
    return () => {
      active = false;
      if (api && widgetId !== null) api.remove(widgetId);
    };
  }, [props.siteKey, props.theme, props.refreshKey]);

  return <div className="loom-turnstile" aria-label="Cloudflare robot verification">
    <div ref={target} />
  </div>;
}
