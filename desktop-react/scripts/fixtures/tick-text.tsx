// The thinking row's text swap: the label blurs across to its new words and only the changed digit of the counter swaps.
// ?start=1 waits for window.tickFixture.go(); the frame tool steps the animations by hand.
import { createRoot } from "react-dom/client";
import { useState } from "react";
import { Crossfade } from "../../src/components/Crossfade";
import { TickText } from "../../src/components/TickText";
import "../../src/motion-tokens.css";
import "../../src/components/run-progress.css";

function Row() {
  const [seconds, setSeconds] = useState(190);
  const [done, setDone] = useState(false);
  Object.assign(window, { tickFixture: { go: () => { setSeconds(191); setDone(true); }, tick: () => setSeconds((value) => value + 1), reset: () => { setSeconds(190); setDone(false); } } });
  const clock = `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
  return (
    <div style={{ background: "#1a1a1a", color: "#bbb", padding: 28, font: "22px system-ui, sans-serif", display: "flex", gap: 10, alignItems: "baseline" }}>
      <span style={{ color: "#d97757" }}>•••</span>
      <TickText value={clock} />
      <span>·</span>
      <Crossfade soft identity={done ? "done" : "going"}>{done ? "thought for 1s" : "almost done thinking…"}</Crossfade>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(<Row />);
