// The thinking row's text swap: the label blurs across to its new words and only the changed digit of the counter swaps.
// ?start=1 waits for window.tickFixture.go(); the frame tool steps the animations by hand.
import { createRoot } from "react-dom/client";
import { useState } from "react";
import { Crossfade } from "../../src/components/Crossfade";
import { RollText } from "../../src/components/RollText";
import { TickText } from "../../src/components/TickText";
import "../../src/motion-tokens.css";
import "../../src/components/run-progress.css";
import "../../src/components/roll-text.css";
import "../../src/components/inline-thinking.css";
import "./sr-only.css";

function Row() {
  const [seconds, setSeconds] = useState(190);
  const [done, setDone] = useState(false);
  Object.assign(window, { tickFixture: { go: () => { setSeconds(191); setDone(true); }, tick: () => setSeconds((value) => value + 1), reset: () => { setSeconds(190); setDone(false); } } });
  const [phase, setPhase] = useState(0);
  const phases = ["Waiting for the model…", "Thinking…", "Reading files…"];
  const clock = `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
  const button = { font: "14px system-ui, sans-serif", padding: "8px 14px", borderRadius: 8, border: "1px solid #444", background: "#2a2a2a", color: "#ddd", cursor: "pointer" } as const;
  return (
    <div>
      <div style={{ background: "#1a1a1a", color: "#bbb", padding: 28, font: "22px system-ui, sans-serif", display: "flex", gap: 10, alignItems: "baseline" }}>
        <span style={{ color: "#d97757" }}>•••</span>
        <TickText value={clock} />
        <span>·</span>
        <Crossfade soft identity={done ? "done" : "going"}>{done ? "thought for 1s" : "almost done thinking…"}</Crossfade>
      </div>
      <div style={{ background: "#0e0e12", padding: 28 }}>
        <div className="inline-thinking"><span className="thinking-shimmer"><RollText identity={phases[phase]} text={phases[phase]} /></span></div>
      </div>
      <div style={{ background: "#141414", padding: 16, display: "flex", gap: 10, flexWrap: "wrap" }}>
        <button style={button} onClick={() => setSeconds((value) => value + 1)}>计时 +1 秒</button>
        <button style={button} onClick={() => setDone((value) => !value)}>切换文字</button>
        <button style={button} onClick={() => { setSeconds(191); setDone(true); }}>播放切换 (go)</button>
        <button style={button} onClick={() => setPhase((value) => (value + 1) % phases.length)}>思考行换词</button>
        <button style={button} onClick={() => { setSeconds(190); setDone(false); }}>复位</button>
      </div>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(<Row />);
