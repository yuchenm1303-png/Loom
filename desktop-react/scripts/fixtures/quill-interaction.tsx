import React, { StrictMode, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import "../../src/renderer-styles";
import { NewConversationRow, type NewConversationRowHandle } from "../../src/components/NewConversationRow";

function Lab() {
  const ref = useRef<NewConversationRowHandle>(null);
  const [count, setCount] = useState(0);
  const [mounted, setMounted] = useState(true);
  return <main style={{ padding: 60, color: "#a99ff8", background: "#19191f", minHeight: 300 }}>
    <button id="before">Focus start</button>
    {mounted && <NewConversationRow ref={ref} label="New conversation" title="New conversation" onNew={() => setCount((value) => value + 1)} />}
    <button id="shortcut" onClick={() => { ref.current?.write(); setCount((value) => value + 1); }}>Shortcut</button>
    <button id="unmount" onClick={() => setMounted(false)}>Unmount</button>
    <output>{count}</output>
  </main>;
}
createRoot(document.getElementById("root")!).render(<StrictMode><Lab /></StrictMode>);
