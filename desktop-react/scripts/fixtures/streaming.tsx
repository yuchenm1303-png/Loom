import React, { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { flushSync } from "react-dom";
import { Transcript } from "../../src/components/Transcript";
import { MarkdownMessage } from "../../src/components/MarkdownMessage";
import "../../src/styles.css";

const root = createRoot(document.getElementById("root")!);
const fixtures = window as unknown as {
  renderStream(text: string, running: boolean, status?: string, thread?: string): void;
  renderPlain(text: string, streaming: boolean): void;
  resetStream(): void;
};
fixtures.resetStream = () => flushSync(() => root.render(null));
fixtures.renderStream = (text, running, status = running ? "streaming" : "completed", thread = "thread-1") => {
  flushSync(() => root.render(<StrictMode><Transcript key={thread} currentTurnId="turn-1" running={running}
    items={[{ id: "answer-1", threadId: thread, turnId: "turn-1", type: "assistant_message", text, status,
      phase: running ? "commentary" : "final_answer" }]} onApproval={() => {}} /></StrictMode>));
};
fixtures.renderPlain = (content, streaming) => flushSync(() => root.render(
  <StrictMode><MarkdownMessage content={content} streaming={streaming} /></StrictMode>,
));
