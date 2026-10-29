import { useEffect, useRef, useState } from "react";
import { hudDocument } from "../../electron/hudDocument";

// Supply only the desktop preload's update transport. All HUD markup, styles,
// geometry and animations come from the exact document used by hudWindow.ts.
const documentSource = hudDocument().replace("<script>", `<script>
window.loomHud = { onUpdate: function(render) {
  addEventListener('message', function(event) {
    if (event.source === parent && event.data && event.data.type === 'loom:demo-hud') render(event.data.payload);
  });
}};
</script><script>`);

export function HomeComputerHud({ payload }: { payload: Record<string, unknown> }) {
  const frame = useRef<HTMLIFrameElement>(null);
  const [loaded, setLoaded] = useState(false);
  useEffect(() => {
    if (loaded) frame.current?.contentWindow?.postMessage({ type: "loom:demo-hud", payload }, "*");
  }, [loaded, payload]);
  return <iframe ref={frame} className="computer-demo-native-hud" title="Loom Computer Use HUD" srcDoc={documentSource} sandbox="allow-scripts" tabIndex={-1} aria-hidden="true" onLoad={() => setLoaded(true)} />;
}
