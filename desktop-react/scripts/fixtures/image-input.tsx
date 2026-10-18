import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import { Composer } from "../../src/components/Composer";
import { Transcript } from "../../src/components/Transcript";
import { I18nProvider } from "../../src/i18n";
import type { ModelSnapshot, TranscriptItem } from "../../src/types/loom";
import "../../src/renderer-styles";

const fixture = { sends: [] as unknown[] };
Object.assign(window, { imageFixture: fixture, loom: { stageTempFile: async (name: string) => `/fixture/${name}` } });
const error = { id: "image-error", threadId: "image-test", turnId: "turn", type: "error", status: "completed",
  error: "AITransportError: Error code: 400: Beam supports text input only. This conversation contains an image." } as TranscriptItem;
function App() {
  const [support, setSupport] = useState<boolean | null>(null);
  const [running, setRunning] = useState(false);
  const profile = { selection: "builtin:opencode-go:beam-free", id: "beam", kind: "builtin", name: "Beam Free", adapter: "opencode-go",
    model: "beam-free", baseUrl: "https://account.smirel.com/model/v1/opencode-go", vision: support };
  const snapshot = { primary: profile, profiles: [profile], current: profile, activeModelId: "beam", recentModels: [] } as ModelSnapshot;
  return <I18nProvider><main style={{ padding: 24, maxWidth: 1000 }}>
    <button onClick={() => setSupport(null)}>Unknown</button><button onClick={() => setSupport(false)}>Text only</button>
    <button onClick={() => setSupport(true)}>Vision</button><button onClick={() => setRunning(value => !value)}>Toggle running</button>
    <Transcript items={[error]} onApproval={() => {}} />
    <Composer threadId="image-test" accountAuthenticated running={running} modelSnapshot={snapshot} imagesAllowed={support !== false} imageSupport={support}
      onSend={(text, attachments) => { fixture.sends.push({ text, attachments }); }} onInterrupt={() => {}} />
  </main></I18nProvider>;
}
createRoot(document.getElementById("root")!).render(<App />);
