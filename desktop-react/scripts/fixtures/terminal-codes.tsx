import { createRoot } from "react-dom/client";
import { I18nProvider } from "../../src/i18n";
import { MarkdownMessage } from "../../src/components/MarkdownMessage";

export function mountTerminalCodes() {
  const container = document.createElement("div");
  container.id = "terminal-code-fixture";
  Object.assign(container.style, {
    position: "fixed",
    left: "20px",
    bottom: "20px",
    width: "min(600px, calc(100vw - 100px))",
    maxHeight: "80vh",
    overflow: "auto",
    zIndex: "2500",
    background: "var(--loom-workspace-canvas)",
    padding: "12px",
  });
  document.body.append(container);
  const root = createRoot(container);
  root.render(
    <I18nProvider>
      <MarkdownMessage
        workspace="C:/Workspace/Loom"
        content={
          '```python\nprint("INLINE_CODE_RESULT")\n```\n\n```javascript\nconsole.log("JS_CODE_RESULT")\n```\n\n```sql\nSELECT 1;\n```'
        }
      />
    </I18nProvider>,
  );
  return () => {
    root.unmount();
    container.remove();
  };
}
