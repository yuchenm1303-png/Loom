// Temporary verification harness for ProfileInsightsPage (delete before commit).
// App is imported only for its stylesheet chain; it is never rendered.
import "../src/App";
import "../src/styles.css";
import "../src/shell-fix.css";
import "../src/components/model-panel-overrides.css";
import "../src/components/inline-thinking.css";
import "../src/components/composer-stability.css";
import "../src/components/composer-attachment-polish.css";
import "../src/typography-scale.css";
import "../src/components/settings-simple.css";
import "../src/components/settings-icon-alignment.css";
import "../src/components/computer-use-hud.css";
import "../src/components/inspector-tabs.css";
import "../src/components/semantic-colors.css";
import "../src/components/thread-header-mark-refinement.css";
import "../src/components/settings-models-polish.css";
import "../src/components/settings-capabilities-polish.css";
import "../src/components/review-motion.css";
import "../src/components/sidebar-clarity-fix.css";
import "../src/components/renderer-crispness.css";
import "../src/components/model-core-redesign.css";
import "../src/theme.css";
import "../src/components/sidebar-primary-actions-polish.css";
import "../src/components/permission-popover-polish.css";
import "../src/components/model-picker.css";
import "../src/components/composer-control-pills.css";
import "../src/components/project-details-panel-theme.css";
import "../src/global-motion.css";
import "../src/components/generation-motion.css";
import "../src/components/starter-cards-polish.css";
import "../src/components/starter-card-interaction-glow.css";
import "../src/components/starter-card-borderless-refinement.css";
import { createRoot } from "react-dom/client";
import { I18nProvider } from "../src/i18n";
import { ProfileInsightsPage } from "../src/components/ProfileInsightsPage";
import fixture from "./profile-fixture.local.json";

const query = new URLSearchParams(location.search);
const theme = query.get("theme") === "dark" ? "dark" : "light";
document.documentElement.dataset.loomTheme = theme;
document.documentElement.style.colorScheme = theme;
document.documentElement.dataset.loomReducedMotion = query.get("reduced") === "1" ? "true" : "false";
localStorage.setItem("loom.settings.language", query.get("lang") === "en" ? "en" : "zh-CN");

const state = query.get("state") || "data";
function emptied() {
  const copy = structuredClone(fixture) as any;
  copy.days = copy.days.map((day: any) => ({ ...day, inputTokens: 0, outputTokens: 0, totalTokens: 0, modelCalls: 0, turns: 0, toolCalls: 0, sessions: 0 }));
  for (const key of Object.keys(copy.totals)) copy.totals[key] = 0;
  copy.range.totalTokens = 0;
  Object.assign(copy, { models: [], tools: [], reasoning: [], peakDay: null, activeHour: null, longestTurnSeconds: 0, firstActivityDate: null, lastActivityDate: null, streaks: { ...copy.streaks, current: 0, longest: 0 } });
  if (Array.isArray(copy.hours)) copy.hours = copy.hours.map(() => 0);
  return copy;
}
(window as any).loom = {
  connect: async () => {},
  call: async () => {
    if (state === "loading") return new Promise(() => {});
    if (state === "error") throw new Error("Loom app server is not running");
    return state === "empty" ? emptied() : fixture;
  },
};

const authenticated = query.get("auth") === "1";
const account = {
  configured: true,
  reachable: true,
  authenticated,
  user: authenticated ? { id: 1, email: "yuchen@example.com", display_name: query.get("name") || "Yuchen", status: "active" } : null,
  serviceUrl: "",
};

createRoot(document.getElementById("root")!).render(
  <I18nProvider>
    <div className="profile-insights-host" data-motion-phase="entered">
      <ProfileInsightsPage account={account as any} onClose={() => {}} />
    </div>
  </I18nProvider>,
);
