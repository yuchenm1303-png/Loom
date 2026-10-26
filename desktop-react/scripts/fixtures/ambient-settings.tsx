/** Real component and production styles with disposable, in-memory transport.
 * npx vite --port 5197
 * /scripts/fixtures/ambient-settings.html?theme=light&lang=zh-CN&empty=1
 * Never connects to the user's Host or starts a model call.
 */
import { createRoot } from "react-dom/client";
import { AmbientSettingsPanel } from "../../src/components/AmbientSettingsPanel";
import { I18nProvider } from "../../src/i18n";
import { applyThemePreference } from "../../src/theme";
import "../../src/renderer-styles";
import "../../src/components/SettingsPage";
const params = new URLSearchParams(location.search);
const zh = params.get("lang") === "zh-CN";
localStorage.setItem("loom.settings.language", zh ? "zh-CN" : "en");
applyThemePreference(params.get("theme") === "light" ? "light" : "dark", { animate:false, syncNative:false });
const base = { threadId:"demo", prompt:zh ? "检查工作区笔记，只报告值得关注的变化。" : "Review the workspace notes. Only report meaningful changes.", enabled:true, maxRuns:100, maxTokens:100000, seconds:3600, path:"notes/weekly-review.md", runsUsed:3, tokensUsed:1280 };
const state = { online:true, paused:false,
  tasks:params.has("empty") ? [] : [
    { ...base, id:"weekly", name:zh ? "每周工作区回顾" : "Weekly workspace review", kind:"interval", nextAt:Date.now()/1000+3600 },
    { ...base, id:"notes", name:zh ? "笔记变化提醒" : "Notes, kept in view", kind:"file", enabled:false },
  ],
  runs:[{id:"run-review",task:"weekly",status:"completed",source:"interval",tokens:1280,result:zh ? "已整理本周的三个待办事项。" : "Three follow-ups collected from this week’s notes."}],
  notifications:[],
};
Object.assign(window, { loom:{ call:async (method, values) => {
  if (method === "thread/list") return {threads:[{id:"demo",title:zh ? "我的工作区" : "My workspace"}]};
  if (method === "ambient/pause") state.paused=values.paused;
  if (method === "ambient/put") {
    const old=state.tasks.find(task=>task.id===values.id);
    const task={...base,runsUsed:0,tokensUsed:0,...old,...values,id:values.id||crypto.randomUUID()};
    state.tasks=old ? state.tasks.map(item=>item.id===task.id?task:item) : [...state.tasks,task];
    return {task:structuredClone(task)};
  }
  if (method === "ambient/cancel") state.tasks.forEach(task=>{if(task.id===values.id) task.enabled=false;});
  if (method === "ambient/delete") state.tasks=state.tasks.filter(task=>task.id!==values.id);
  if (method === "ambient/history") return {runs:state.runs,nextCursor:null};
  return structuredClone(state);
} } });
const narrowUrl = new URL(location.href);
narrowUrl.searchParams.delete("narrow");
createRoot(document.getElementById("root")!).render(params.has("narrow") ? <iframe title="Narrow layout preview" src={narrowUrl.href} style={{display:"block",width:460,maxWidth:"100%",height:"100vh",margin:"0 auto",border:0}}/> : <I18nProvider><div className="settings-host" style={{minHeight:"100vh",overflow:"visible"}}><div className="settings-shell settings-refined" style={{display:"block",height:"auto"}}><main style={{maxWidth:1200,margin:"0 auto",padding:"32px 36px"}}><AmbientSettingsPanel threadId="demo"/></main></div></div></I18nProvider>);
