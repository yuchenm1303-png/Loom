import React, { StrictMode, startTransition, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { flushSync } from "react-dom";
import { Transcript } from "../../src/components/Transcript";
import { MarkdownMessage } from "../../src/components/MarkdownMessage";
import { Composer } from "../../src/components/Composer";
import { I18nProvider } from "../../src/i18n";
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
  renderTypingStress(): void;
};
const noop = async () => {};
function TypingStress() {
  const [receiving, setReceiving] = useState(false);
  const [text, setText] = useState("## Streaming output\n\n" + "Testing **output** and `code`.\n\n".repeat(400));
  useEffect(() => {
    const timer = setInterval(() => startTransition(() => {
      setReceiving(true);
      setText(value => value + "More output. ");
    }), 28);
    return () => clearInterval(timer);
  }, []);
  return <><MarkdownMessage content={text} streaming={receiving} />
    <Composer threadId="typing-test" running accountAuthenticated onSend={noop} onInterrupt={noop} /></>;
}
fixtures.renderTypingStress = () => flushSync(() => root.render(<I18nProvider><TypingStress /></I18nProvider>));
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
