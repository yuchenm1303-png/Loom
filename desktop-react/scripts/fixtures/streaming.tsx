import React, { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { flushSync } from "react-dom";
import { Transcript } from "../../src/components/Transcript";
import { MarkdownMessage } from "../../src/components/MarkdownMessage";
import { TranscriptScrollController } from "../../src/components/TranscriptScrollController";
import type { TranscriptItem } from "../../src/types/loom";
import "../../src/styles.css";
import "../../src/components/run-progress.css";

const root = createRoot(document.getElementById("root")!);
const fixtures = window as unknown as {
  renderStream(text: string, running: boolean, status?: string, thread?: string): void;
  renderPlain(text: string, streaming: boolean): void;
  resetStream(): void;
  renderScrolled(text: string, running: boolean): void;
  renderItems(items: TranscriptItem[], running: boolean): void;
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
fixtures.renderItems = (items, running) => flushSync(() => root.render(
  <StrictMode><div className="conversation-stage" style={{ height: 800 }}><Transcript items={items} running={running} currentTurnId="turn-1" onApproval={() => {}} /></div></StrictMode>,
));
fixtures.renderScrolled = (text, running) => {
  const items: TranscriptItem[] = [{ id: "scroll-answer", threadId: "scroll-thread", turnId: "turn-1",
    type: "assistant_message", text, status: running ? "streaming" : "completed", phase: "final_answer" }];
  flushSync(() => root.render(<StrictMode>
    <div className="conversation-stage" style={{ height: 400 }}>
      <style>{".conversation-stage > .transcript-scroll { height: 400px; overflow-y: auto; }"}</style>
      <Transcript items={items} running={running} currentTurnId="turn-1" onApproval={() => {}} />
      <TranscriptScrollController items={items} running={running} threadId="scroll-thread" currentTurnId="turn-1" />
    </div>
  </StrictMode>));
};
