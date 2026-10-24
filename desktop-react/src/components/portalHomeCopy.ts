// The words of the Loom home page, in both languages, kept apart from its markup so the page can be read like a text.
// A pair of asterisks marks the words that are set in italic (the markup turns them into <em>).

export type PortalHomeCopy = {
  brandLabel: string;
  title: [string, string];
  intro: string;
  more: string;
  less: string;
  moreParagraphs: string[];
  open: string;
  download: string;
  platform: string;
  notes: string;
  doing: { heading: string; items: { lead: string; text: string }[] };
  session: { heading: string; rows: { time: string; text: string }[]; note: string; label: string };
  start: { heading: string; steps: { lead: string; text: string }[] };
  madeBy: [string, string];
  updates: string;
  classic: string;
};

export const PORTAL_HOME_COPY: { en: PortalHomeCopy; zh: PortalHomeCopy } = {
  en: {
    brandLabel: "Loom home",
    title: ["A quieter way", "to get things done."],
    intro: "Loom is a personal AI agent that works on your own computer, with your files, your code and your browser. It shows every step as it happens, and it asks before it does anything that matters.",
    more: "More",
    less: "Less",
    moreParagraphs: [
      "Your conversations and files stay on your machine. The web workspace is a window onto the Loom Host running there, so one conversation follows you between the desktop app and the browser.",
      "Loom plans a task, writes and runs code, uses a browser or a desktop app, and stops to ask when the decision is yours.",
    ],
    open: "Open workspace",
    download: "Download for Windows",
    platform: "Windows 10 and 11",
    notes: "Release notes",
    doing: {
      heading: "What Loom does",
      items: [
        { lead: "Explore.", text: "Read the files, code and pages that matter, and turn scattered material into a clear picture." },
        { lead: "Create.", text: "Write code, edit files and carry a task through to something you can use." },
        { lead: "Stay in control.", text: "Everything runs on your own computer. You see each step, and you decide what happens next." },
      ],
    },
    session: {
      heading: "A task, start to finish",
      rows: [
        { time: "09:12", text: "Read the project and the failing test." },
        { time: "09:13", text: "Found the cause in *release.py* and fixed it." },
        { time: "09:14", text: "Ran the tests: 41 passed." },
        { time: "09:14", text: "Ready to push one commit. Waiting for your approval." },
      ],
      note: "Each step stays in view. Anything consequential waits for you.",
      label: "An example session, step by step",
    },
    start: {
      heading: "Getting started",
      steps: [
        { lead: "Install Loom for Windows.", text: "The installer includes the Host that does the work on your computer." },
        { lead: "Sign in.", text: "The web workspace finds that Host on this computer and connects on its own." },
        { lead: "Say what you want done.", text: "Follow each step as it appears, and approve what matters." },
      ],
    },
    madeBy: ["Loom is made by ", "."],
    updates: "Updates",
    classic: "Classic home",
  },
  zh: {
    brandLabel: "Loom 首页",
    title: ["从容开始，", "让事情向前。"],
    intro: "Loom 是运行在你自己电脑上的个人 AI 助手。它能读懂你的文件和代码，使用你的浏览器，每一步都让你看得见，并在做重要的事之前先问你。",
    more: "更多",
    less: "收起",
    moreParagraphs: [
      "对话和文件都留在你的电脑上。网页工作区只是这台电脑上 Loom Host 的一扇窗，所以同一段对话可以在桌面端和浏览器之间延续。",
      "Loom 会规划任务、编写并运行代码、使用浏览器或桌面应用，并在需要你决定时停下来询问。",
    ],
    open: "进入工作区",
    download: "下载 Windows 版",
    platform: "Windows 10 / 11",
    notes: "更新日志",
    doing: {
      heading: "Loom 能做什么",
      items: [
        { lead: "理解。", text: "读懂重要的文件、代码和网页，把零散的材料整理成清晰的全貌。" },
        { lead: "创造。", text: "编写代码、修改文件，把一件任务推进到可以直接使用的成果。" },
        { lead: "由你掌控。", text: "一切都在你自己的电脑上运行。每一步你都看得见，下一步由你决定。" },
      ],
    },
    session: {
      heading: "一项任务，从头到尾",
      rows: [
        { time: "09:12", text: "读取项目和未通过的测试。" },
        { time: "09:13", text: "在 *release.py* 中找到原因并修复。" },
        { time: "09:14", text: "运行测试：41 项通过。" },
        { time: "09:14", text: "准备推送一次提交，等待你的批准。" },
      ],
      note: "每一步都看得见，重要的操作会先等你同意。",
      label: "一次示例会话，逐步展开",
    },
    start: {
      heading: "开始使用",
      steps: [
        { lead: "安装 Windows 版。", text: "安装包里自带在你电脑上干活的 Host。" },
        { lead: "登录。", text: "网页工作区会自动找到这台电脑上的 Host 并连接。" },
        { lead: "说出想做的事。", text: "一步步看着它推进，重要的地方由你批准。" },
      ],
    },
    madeBy: ["Loom 由 ", " 打造。"],
    updates: "更新",
    classic: "经典主页",
  },
};
