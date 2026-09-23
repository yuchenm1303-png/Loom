import {
  Clipboard,
  Copy,
  ExternalLink,
  Image as ImageIcon,
  Link2,
  MousePointer2,
  Reply,
  Scissors,
  Search,
  Redo2,
  Undo2,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import { useI18n } from "../i18n";
import { dispatchQuoteReply, type QuoteReplySource } from "./quoteReply";
import "./global-context-menu.css";

type EditableElement = HTMLInputElement | HTMLTextAreaElement | HTMLElement;

interface ContextState {
  x: number;
  y: number;
  target: HTMLElement;
  editable: EditableElement | null;
  password: boolean;
  selectionText: string;
  messageShell: HTMLElement | null;
  messageKind: "assistant" | "user" | null;
  messageText: string;
  quoteText: string;
  image: HTMLImageElement | null;
  imagePath: string;
  filePath: string;
  link: HTMLAnchorElement | null;
  codeText: string;
}

interface MenuAction {
  id: string;
  label: string;
  shortcut?: string;
  icon: ReactNode;
  group: number;
  disabled?: boolean;
  run(): void | Promise<unknown>;
}

function isInputLike(value: Element | null): value is HTMLInputElement | HTMLTextAreaElement {
  return value instanceof HTMLInputElement || value instanceof HTMLTextAreaElement;
}

function editableFromTarget(target: HTMLElement): EditableElement | null {
  const element = target.closest("textarea, input, [contenteditable='true']");
  return element instanceof HTMLElement ? element : null;
}

function inputSelection(element: EditableElement | null): string {
  if (!element) return "";
  if (isInputLike(element)) {
    const start = element.selectionStart ?? 0;
    const end = element.selectionEnd ?? start;
    return element.value.slice(Math.min(start, end), Math.max(start, end)).trim();
  }
  return window.getSelection()?.toString().trim() || "";
}

function documentSelection(): Selection | null {
  const selection = window.getSelection();
  return selection && !selection.isCollapsed ? selection : null;
}

function selectionInside(selection: Selection | null, container: HTMLElement | null): boolean {
  if (!selection || !container) return false;
  return Boolean(
    selection.anchorNode
    && selection.focusNode
    && container.contains(selection.anchorNode)
    && container.contains(selection.focusNode)
  );
}

function messageTextFromShell(shell: HTMLElement | null, kind: "assistant" | "user" | null): string {
  if (!shell || !kind) return "";
  const raw = shell.dataset.loomMessageText?.trim();
  if (raw) return raw;
  if (kind === "user") {
    return (
      shell.querySelector<HTMLElement>(".user-message-text")?.innerText
      || shell.querySelector<HTMLElement>(".user-message")?.innerText
      || ""
    ).trim();
  }

  const directAnswers = [...shell.querySelectorAll<HTMLElement>(".assistant-message > .markdown-body")];
  const answer = directAnswers.at(-1)?.innerText?.trim();
  if (answer) return answer;
  return shell.querySelector<HTMLElement>(".assistant-message .markdown-body")?.innerText?.trim() || "";
}

function imageFromTarget(target: HTMLElement): HTMLImageElement | null {
  if (target instanceof HTMLImageElement) return target;
  const imageSurface = target.closest(
    ".user-message-image-preview, .assistant-local-image-preview, .user-message-image-lightbox-canvas",
  );
  return imageSurface?.querySelector("img") ?? null;
}

function imagePathFromElement(image: HTMLImageElement | null): string {
  if (!image) return "";
  return image.dataset.loomImagePath || "";
}

async function writeText(value: string): Promise<void> {
  if (!value) return;
  await window.loom.writeClipboardText(value);
}

function replaceEditableSelection(element: EditableElement, replacement: string): void {
  element.focus();
  if (isInputLike(element)) {
    const start = element.selectionStart ?? element.value.length;
    const end = element.selectionEnd ?? start;
    element.setRangeText(replacement, start, end, "end");
    element.dispatchEvent(new Event("input", { bubbles: true }));
    return;
  }
  document.execCommand("insertText", false, replacement);
}

function selectAll(element: EditableElement | null): void {
  if (element) {
    element.focus();
    if (isInputLike(element)) {
      element.select();
      return;
    }
  }
  document.execCommand("selectAll");
}

function sourceForQuote(kind: "assistant" | "user" | null): QuoteReplySource {
  return kind || "selection";
}

function imageSource(image: HTMLImageElement | null): string {
  return String(image?.currentSrc || image?.src || "").trim();
}

async function materializeImageSource(source: string): Promise<string> {
  if (!source.startsWith("blob:")) return source;
  const response = await fetch(source);
  if (!response.ok) throw new Error("Could not read image");
  const blob = await response.blob();
  return await new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error || new Error("Could not read image"));
    reader.onload = () => resolve(String(reader.result || ""));
    reader.readAsDataURL(blob);
  });
}

function shortcut(command: string): string {
  const mac = /Mac|iPhone|iPad/.test(navigator.platform);
  return mac ? `⌘${command}` : `Ctrl+${command}`;
}

function redoShortcut(): string {
  return /Mac|iPhone|iPad/.test(navigator.platform) ? "⌘⇧Z" : "Ctrl+Y";
}

function trimPreview(value: string): string {
  const compact = value.replace(/\s+/g, " ").trim();
  return compact.length > 118 ? `${compact.slice(0, 118).trimEnd()}…` : compact;
}

export function GlobalContextMenu() {
  const { language } = useI18n();
  const zh = language === "zh-CN";
  const [state, setState] = useState<ContextState | null>(null);
  const [position, setPosition] = useState({ x: 0, y: 0 });
  const [positioned, setPositioned] = useState(false);
  const [closing, setClosing] = useState(false);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const closeTimerRef = useRef<number | null>(null);
  const readyFrameRef = useRef<number | null>(null);

  const closeMenu = useCallback((immediate = false) => {
    if (!state) return;
    if (closeTimerRef.current !== null) window.clearTimeout(closeTimerRef.current);
    if (readyFrameRef.current !== null) cancelAnimationFrame(readyFrameRef.current);
    readyFrameRef.current = null;
    const reduced = document.documentElement.dataset.loomReducedMotion === "true"
      || Boolean(window.matchMedia?.("(prefers-reduced-motion: reduce)").matches);
    if (immediate || reduced) {
      setState(null);
      setClosing(false);
      setPositioned(false);
      return;
    }
    setClosing(true);
    closeTimerRef.current = window.setTimeout(() => {
      closeTimerRef.current = null;
      setState(null);
      setClosing(false);
      setPositioned(false);
    }, 150);
  }, [state]);

  useEffect(() => {
    const onContextMenu = (event: MouseEvent) => {
      let target: HTMLElement | null = event.target instanceof HTMLElement ? event.target : null;
      if (!target && event.target instanceof Element) {
        let ancestor: Element | null = event.target;
        while (ancestor && !(ancestor instanceof HTMLElement)) ancestor = ancestor.parentElement;
        target = ancestor instanceof HTMLElement ? ancestor : null;
      }
      if (!target || target.closest(".loom-context-menu")) return;

      event.preventDefault();

      const editable = editableFromTarget(target);
      const password = editable instanceof HTMLInputElement && editable.type === "password";
      const selection = documentSelection();
      const windowSelection = selection?.toString().trim() || "";
      const selectionText = editable ? inputSelection(editable) : windowSelection;
      const messageShell = target.closest<HTMLElement>(".message-shell");
      const messageKind = messageShell?.classList.contains("assistant-message-shell")
        ? "assistant"
        : messageShell?.classList.contains("user-message-shell")
          ? "user"
          : null;
      const messageText = messageTextFromShell(messageShell, messageKind);
      const selectedInMessage = selectionInside(selection, messageShell);
      const quoteText = messageShell
        ? (selectedInMessage && windowSelection ? windowSelection : messageText)
        : selectionText;
      const image = imageFromTarget(target);
      const filePath = target.closest<HTMLElement>("[data-loom-file-path]")?.dataset.loomFilePath || "";
      const link = target.closest<HTMLAnchorElement>("a[href]");
      const codeText = target.closest("pre")?.innerText?.trim() || "";
      const targetRect = target.getBoundingClientRect();
      const anchorX = event.clientX || Math.min(targetRect.left + 24, targetRect.right);
      const anchorY = event.clientY || Math.min(targetRect.bottom, window.innerHeight - 8);

      if (closeTimerRef.current !== null) window.clearTimeout(closeTimerRef.current);
      if (readyFrameRef.current !== null) cancelAnimationFrame(readyFrameRef.current);
      closeTimerRef.current = null;
      readyFrameRef.current = null;
      setClosing(false);
      setPositioned(false);
      setState({
        x: anchorX,
        y: anchorY,
        target,
        editable,
        password,
        selectionText,
        messageShell,
        messageKind,
        messageText,
        quoteText,
        image,
        imagePath: imagePathFromElement(image),
        filePath,
        link,
        codeText,
      });
      setPosition({ x: anchorX, y: anchorY });
    };

    const close = () => closeMenu();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") close();
    };

    document.addEventListener("contextmenu", onContextMenu, true);
    window.addEventListener("blur", close);
    window.addEventListener("resize", close);
    window.addEventListener("scroll", close, true);
    window.addEventListener("keydown", onKeyDown, true);
    return () => {
      document.removeEventListener("contextmenu", onContextMenu, true);
      window.removeEventListener("blur", close);
      window.removeEventListener("resize", close);
      window.removeEventListener("scroll", close, true);
      window.removeEventListener("keydown", onKeyDown, true);
    };
  }, [closeMenu]);

  useEffect(() => {
    if (!state) return;
    const close = (event: PointerEvent) => {
      if (!menuRef.current?.contains(event.target as Node)) closeMenu();
    };
    document.addEventListener("pointerdown", close, true);
    return () => document.removeEventListener("pointerdown", close, true);
  }, [closeMenu, state]);

  useLayoutEffect(() => {
    if (!state || !menuRef.current) return;
    const rect = menuRef.current.getBoundingClientRect();
    const margin = 8;
    setPosition({
      x: Math.max(margin, Math.min(state.x, window.innerWidth - rect.width - margin)),
      y: Math.max(margin, Math.min(state.y, window.innerHeight - rect.height - margin)),
    });
    if (readyFrameRef.current !== null) cancelAnimationFrame(readyFrameRef.current);
    readyFrameRef.current = requestAnimationFrame(() => {
      readyFrameRef.current = null;
      setPositioned(true);
    });
  }, [state]);

  const actions = useMemo<MenuAction[]>(() => {
    if (!state) return [];
    const items: MenuAction[] = [];
    const activeComposer = document.querySelector<HTMLTextAreaElement>(".composer textarea:not(:disabled)");
    const canQuote = Boolean(
      activeComposer
      && state.quoteText
      && !state.password
      && !state.target.closest(".composer")
    );
    const selected = state.selectionText;
    const copyText = selected || state.codeText || state.messageText;
    const remoteImageSource = imageSource(state.image);

    if (state.editable) {
      items.push({
        id: "undo",
        label: zh ? "撤销" : "Undo",
        shortcut: shortcut("Z"),
        icon: <Undo2 size={15} strokeWidth={1.75} />,
        group: 0,
        run: () => {
          state.editable?.focus();
          document.execCommand("undo");
        },
      });
      items.push({
        id: "redo",
        label: zh ? "重做" : "Redo",
        shortcut: redoShortcut(),
        icon: <Redo2 size={15} strokeWidth={1.75} />,
        group: 0,
        run: () => {
          state.editable?.focus();
          document.execCommand("redo");
        },
      });
    }

    if (canQuote) {
      items.push({
        id: "quote",
        label: state.messageKind === "assistant"
          ? (zh ? "引用回答" : "Reply with quote")
          : state.messageKind === "user"
            ? (zh ? "引用消息" : "Quote message")
            : (zh ? "引用所选内容" : "Quote selection"),
        icon: <Reply size={15} strokeWidth={1.8} />,
        group: 0,
        run: () => {
          dispatchQuoteReply({
            text: state.quoteText,
            source: sourceForQuote(state.messageKind),
            messageId: state.messageShell?.dataset.messageId,
          });
          window.getSelection()?.removeAllRanges();
          requestAnimationFrame(() => activeComposer?.focus());
        },
      });
    }

    if (state.image && remoteImageSource) {
      items.push({
        id: "copy-image",
        label: zh ? "复制图片" : "Copy image",
        icon: <ImageIcon size={15} strokeWidth={1.75} />,
        group: 1,
        run: async () => {
          const source = state.imagePath || await materializeImageSource(remoteImageSource);
          await window.loom.copyImageSource(source);
        },
      });
    }

    if (copyText && !state.password) {
      items.push({
        id: "copy",
        label: state.codeText && !selected
          ? (zh ? "复制代码" : "Copy code")
          : state.messageText && !selected
            ? (zh ? "复制消息" : "Copy message")
            : (zh ? "复制" : "Copy"),
        shortcut: shortcut("C"),
        icon: <Copy size={15} strokeWidth={1.75} />,
        group: 1,
        run: () => writeText(copyText),
      });
    }

    if (state.editable && !state.password) {
      items.push({
        id: "cut",
        label: zh ? "剪切" : "Cut",
        shortcut: shortcut("X"),
        icon: <Scissors size={15} strokeWidth={1.75} />,
        group: 1,
        disabled: !selected,
        run: async () => {
          if (!selected) return;
          await writeText(selected);
          replaceEditableSelection(state.editable!, "");
        },
      });
    }

    if (state.editable) {
      items.push({
        id: "paste",
        label: zh ? "粘贴" : "Paste",
        shortcut: shortcut("V"),
        icon: <Clipboard size={15} strokeWidth={1.75} />,
        group: 1,
        run: async () => {
          const text = await window.loom.readClipboardText();
          if (text) replaceEditableSelection(state.editable!, text);
        },
      });
    }

    const href = state.link?.href || "";
    if (href && /^(https?:|mailto:)/i.test(href)) {
      items.push({
        id: "open-link",
        label: zh ? "在浏览器中打开链接" : "Open link in browser",
        icon: <ExternalLink size={15} strokeWidth={1.75} />,
        group: 2,
        run: () => window.loom.openExternal(href),
      });
      items.push({
        id: "copy-link",
        label: zh ? "复制链接" : "Copy link",
        icon: <Link2 size={15} strokeWidth={1.75} />,
        group: 2,
        run: () => writeText(href),
      });
    } else if (state.image && /^https?:/i.test(remoteImageSource)) {
      items.push({
        id: "open-image",
        label: zh ? "在浏览器中打开图片" : "Open image in browser",
        icon: <ExternalLink size={15} strokeWidth={1.75} />,
        group: 2,
        run: () => window.loom.openExternal(remoteImageSource),
      });
      items.push({
        id: "copy-image-link",
        label: zh ? "复制图片地址" : "Copy image address",
        icon: <Link2 size={15} strokeWidth={1.75} />,
        group: 2,
        run: () => writeText(remoteImageSource),
      });
    }

    if (state.imagePath) {
      items.push({
        id: "reveal-image",
        label: zh ? "在文件夹中查看原图" : "Show original image",
        icon: <ExternalLink size={15} strokeWidth={1.75} />,
        group: 2,
        run: () => window.loom.revealPath(state.imagePath),
      });
    } else if (state.filePath) {
      items.push({
        id: "reveal-file",
        label: zh ? "在文件夹中显示" : "Show in folder",
        icon: <ExternalLink size={15} strokeWidth={1.75} />,
        group: 2,
        run: () => window.loom.revealPath(state.filePath),
      });
      items.push({
        id: "copy-file-path",
        label: zh ? "复制文件路径" : "Copy file path",
        icon: <Link2 size={15} strokeWidth={1.75} />,
        group: 2,
        run: () => writeText(state.filePath),
      });
    }

    if (selected && !state.password) {
      items.push({
        id: "search",
        label: zh ? "搜索所选内容" : "Search selected text",
        icon: <Search size={15} strokeWidth={1.75} />,
        group: 3,
        run: () => window.loom.openExternal(`https://www.google.com/search?q=${encodeURIComponent(selected.slice(0, 1200))}`),
      });
    }

    items.push({
      id: "select-all",
      label: zh ? "全选" : "Select all",
      shortcut: shortcut("A"),
      icon: <MousePointer2 size={15} strokeWidth={1.75} />,
      group: 4,
      run: () => {
        requestAnimationFrame(() => selectAll(state.editable));
      },
    });

    return items;
  }, [state, zh]);

  useEffect(() => {
    if (!state || !positioned || closing) return;
    const frame = requestAnimationFrame(() => {
      menuRef.current?.querySelector<HTMLButtonElement>(".loom-context-action:not(:disabled)")?.focus({ preventScroll: true });
    });
    return () => cancelAnimationFrame(frame);
  }, [closing, positioned, state]);

  useEffect(() => () => {
    if (closeTimerRef.current !== null) window.clearTimeout(closeTimerRef.current);
    if (readyFrameRef.current !== null) cancelAnimationFrame(readyFrameRef.current);
  }, []);

  if (!state || !actions.length) return null;

  let lastGroup = -1;
  const previewText = state.quoteText || state.selectionText;
  const previewLabel = state.messageKind === "assistant"
    ? (zh ? "Loom 回复" : "Loom reply")
    : state.messageKind === "user"
      ? (zh ? "你的消息" : "Your message")
      : state.image
        ? (zh ? "图片" : "Image")
        : state.selectionText
          ? (zh ? "所选内容" : "Selection")
          : "";

  return createPortal(
    <div
      ref={menuRef}
      className={`loom-context-menu ${positioned ? "is-positioned" : "is-positioning"} ${closing ? "is-closing" : ""}`.trim()}
      role="menu"
      aria-label={zh ? "右键菜单" : "Context menu"}
      style={{ left: position.x, top: position.y }}
      onContextMenu={(event) => event.preventDefault()}
      onKeyDown={(event) => {
        if (event.key !== "ArrowDown" && event.key !== "ArrowUp" && event.key !== "Home" && event.key !== "End") return;
        event.preventDefault();
        const buttons = [...(menuRef.current?.querySelectorAll<HTMLButtonElement>(".loom-context-action:not(:disabled)") ?? [])];
        if (!buttons.length) return;
        const current = buttons.indexOf(document.activeElement as HTMLButtonElement);
        const next = event.key === "Home"
          ? 0
          : event.key === "End"
            ? buttons.length - 1
            : event.key === "ArrowDown"
              ? (current + 1 + buttons.length) % buttons.length
              : (current - 1 + buttons.length) % buttons.length;
        buttons[next]?.focus({ preventScroll: true });
      }}
    >
      {previewLabel && previewText ? (
        <div className="loom-context-preview" aria-hidden="true">
          <span>{previewLabel}</span>
          <p>{trimPreview(previewText)}</p>
        </div>
      ) : null}
      <div className="loom-context-actions">
        {actions.map((action) => {
          const separator = lastGroup >= 0 && lastGroup !== action.group;
          lastGroup = action.group;
          return (
            <div className={separator ? "loom-context-action-wrap has-separator" : "loom-context-action-wrap"} key={action.id}>
              <button
                type="button"
                role="menuitem"
                className={action.id === "quote" ? "loom-context-action is-primary" : "loom-context-action"}
                disabled={action.disabled}
                onClick={() => {
                  closeMenu();
                  void Promise.resolve(action.run()).catch(() => undefined);
                }}
              >
                <span className="loom-context-action-icon">{action.icon}</span>
                <span className="loom-context-action-label">{action.label}</span>
                {action.shortcut ? <kbd>{action.shortcut}</kbd> : null}
              </button>
            </div>
          );
        })}
      </div>
    </div>,
    document.body,
  );
}
