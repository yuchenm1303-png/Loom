import {
  AlertCircle,
  Camera,
  Check,
  ChevronRight,
  Eye,
  EyeOff,
  LogOut,
  Pencil,
  Pulse,
  ShieldCheck,
  Upload,
  UserRound,
  X,
} from "./icons";
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
} from "react";
import { useI18n } from "../i18n";
import { useMotionPresence } from "../motion/useMotionPresence";
import { useReducedMotion } from "../motion/useReducedMotion";
import { DUR, EASE, RETAIN } from "../motion/tokens";
import { AvatarCropDialog } from "./AvatarCropDialog";
import { Crossfade } from "./Crossfade";
import type { LoomAccountError, LoomAccountSnapshot, LoomAuthCapabilities } from "../types/account";
import {
  ACCOUNT_PASSWORD_MIN_LENGTH,
  accountErrorText,
  accountServiceLabel,
  isValidAccountEmail,
} from "./accountMessages";
import "./account-auth.css";

interface AccountDialogProps {
  open: boolean;
  account: LoomAccountSnapshot;
  ready: boolean;
  busy: boolean;
  error: LoomAccountError | null;
  onClose(): void;
  onClearError(): void;
  onRetry(): void | Promise<void>;
  onLogin(email: string, password: string): Promise<boolean>;
  onRegister(email: string, password: string): Promise<boolean>;
  capabilities?: LoomAuthCapabilities;
  onUpdateProfile(displayName: string, avatarDataUrl: string): Promise<boolean>;
  initialProfileEdit?: boolean;
  closeAfterProfileEdit?: boolean;
  onOpenProfile?(): void;
  onLogout(): Promise<void>;
}

type AuthMode = "login" | "register";
/** What the card shows under the identity. Switching views cross-fades the words and morphs the card's height. */
type View = "checking" | "error" | "unconfigured" | "form" | "account" | "editor";

const NAME_MAX = 48;
const FOCUSABLE =
  'button:not([disabled]), input:not([disabled]), [href], select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * The body of the card changes height on the flow step when it starts to show something else, instead of jumping:
 * the old words fade out above the new ones (Crossfade) while the edge glides. The painted height is kept by a
 * ResizeObserver, so a switch that interrupts another one continues from where the edge is.
 */
function useHeightMorph(identity: string, reduce: boolean) {
  const nodeRef = useRef<HTMLDivElement | null>(null);
  const heightRef = useRef<number | null>(null);
  const observerRef = useRef<ResizeObserver | null>(null);
  const shownRef = useRef(identity);
  const ref = useCallback((node: HTMLDivElement | null) => {
    observerRef.current?.disconnect();
    observerRef.current = null;
    nodeRef.current = node;
    heightRef.current = node ? node.getBoundingClientRect().height : null;
    if (!node) return;
    const observer = new ResizeObserver(() => { heightRef.current = node.getBoundingClientRect().height; });
    observer.observe(node);
    observerRef.current = observer;
  }, []);
  useLayoutEffect(() => {
    if (shownRef.current === identity) return;
    shownRef.current = identity;
    const node = nodeRef.current;
    const from = heightRef.current;
    if (!node || from === null || reduce) return;
    for (const running of node.getAnimations()) running.cancel();
    const to = node.getBoundingClientRect().height;
    if (Math.abs(to - from) < 1) return;
    node.dataset.morphing = "";
    const animation = node.animate([{ height: `${from}px` }, { height: `${to}px` }], { duration: DUR.flow, easing: EASE.move });
    const settle = () => { if (!node.getAnimations().length) delete node.dataset.morphing; };
    animation.addEventListener("finish", settle);
    animation.addEventListener("cancel", settle);
  }, [identity, reduce]);
  return ref;
}

/** A few lines that make room for themselves (layout, on the base step) and fade in once the room is there. */
function Fold({ open, children, className = "" }: { open: boolean; children: ReactNode; className?: string }) {
  return (
    <div className={`acct-fold${open ? " is-open" : ""}${className ? ` ${className}` : ""}`} aria-hidden={!open} inert={!open}>
      <div className="acct-fold-inner">{children}</div>
    </div>
  );
}

/** The last non-empty value, so a message can still be read while its fold closes. */
function useLastShown(value: string): string {
  const last = useRef(value);
  if (value) last.current = value;
  return value || last.current;
}

function GoogleMark() {
  return (
    <svg className="acct-brand" viewBox="0 0 48 48" aria-hidden="true">
      <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z" />
      <path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z" />
      <path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z" />
      <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z" />
    </svg>
  );
}

function GithubMark() {
  return (
    <svg className="acct-brand" viewBox="0 0 16 16" aria-hidden="true">
      <path fill="currentColor" d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0 0 16 8c0-4.42-3.58-8-8-8z" />
    </svg>
  );
}

export function AccountDialog({
  open,
  account,
  ready,
  busy,
  error,
  onClose,
  onClearError,
  onRetry,
  onLogin,
  onRegister,
  capabilities,
  onUpdateProfile,
  initialProfileEdit = false,
  closeAfterProfileEdit = false,
  onOpenProfile,
  onLogout,
}: AccountDialogProps) {
  const { language } = useI18n();
  const zh = language === "zh-CN";
  const reduce = useReducedMotion();
  const presence = useMotionPresence(open, RETAIN.base);
  const [mode, setMode] = useState<AuthMode>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [revealPassword, setRevealPassword] = useState(false);
  const [emailTouched, setEmailTouched] = useState(false);
  const [confirmTouched, setConfirmTouched] = useState(false);
  const [localError, setLocalError] = useState("");
  const [oauthOpening, setOauthOpening] = useState<"google" | "github" | null>(null);
  const [oauthWaiting, setOauthWaiting] = useState<"google" | "github" | null>(null);
  const [editingProfile, setEditingProfile] = useState(false);
  const [profileName, setProfileName] = useState("");
  const [profileAvatar, setProfileAvatar] = useState("");
  const [profileError, setProfileError] = useState("");
  const [brokenAvatar, setBrokenAvatar] = useState("");
  const [avatarCropFile, setAvatarCropFile] = useState<File | null>(null);
  // The crop dialog leaves like every panel does, still showing the picture it was cropping.
  const cropPresence = useMotionPresence(Boolean(avatarCropFile), RETAIN.base);
  const lastCropFileRef = useRef<File | null>(avatarCropFile);
  if (avatarCropFile) lastCropFileRef.current = avatarCropFile;

  const dialogRef = useRef<HTMLElement | null>(null);
  const closeRef = useRef<HTMLButtonElement | null>(null);
  const emailRef = useRef<HTMLInputElement | null>(null);
  const passwordRef = useRef<HTMLInputElement | null>(null);
  const confirmRef = useRef<HTMLInputElement | null>(null);
  const nameRef = useRef<HTMLInputElement | null>(null);
  const avatarInputRef = useRef<HTMLInputElement | null>(null);
  const initialProfileEditConsumedRef = useRef(false);

  const signedIn = Boolean(account.authenticated && account.user);
  // The profile page opens this dialog straight into the editor: it is the editor from its first frame.
  const opensInEditor = open && initialProfileEdit && !initialProfileEditConsumedRef.current;
  const liveView: View = !ready
    ? "checking"
    : !account.configured
      ? (error ? "error" : "unconfigured")
      : signedIn
        ? (editingProfile || opensInEditor ? "editor" : "account")
        : "form";
  // While it leaves, the card keeps showing what it showed: closing never flashes another view.
  const shownViewRef = useRef<View>(liveView);
  if (open) shownViewRef.current = liveView;
  const view = shownViewRef.current;
  const stageRef = useHeightMorph(view, reduce);

  useEffect(() => {
    if (open) void onRetry();
  }, [open, onRetry]);

  // Remember who opened the dialog and hand focus back on close, so keyboard users are not dumped at the top of the
  // document. The right tool dock hides while the card is up and comes back with new buttons: when the opener is
  // gone, its successor is found by the tool's id.
  useEffect(() => {
    if (!open) return;
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const toolId = opener?.closest<HTMLElement>("[data-tool-id]")?.dataset.toolId;
    return () => {
      const target = opener && document.contains(opener)
        ? opener
        : toolId ? document.querySelector<HTMLElement>(`[data-tool-id="${CSS.escape(toolId)}"]`) : null;
      target?.focus();
    };
  }, [open]);

  // The transient fields are cleared once the card is gone (not when it starts to leave, which would show the
  // cleared form during the exit).
  useEffect(() => {
    if (presence.mounted) return;
    setPassword("");
    setConfirm("");
    setLocalError("");
    setRevealPassword(false);
    setEmailTouched(false);
    setConfirmTouched(false);
    setOauthWaiting(null);
    setEditingProfile(false);
    setProfileError("");
    setAvatarCropFile(null);
  }, [presence.mounted]);

  // Focus follows the view: the first field of a form, otherwise the card itself so the keyboard stays inside it.
  useEffect(() => {
    if (!open) return;
    const timer = window.setTimeout(() => {
      const root = dialogRef.current;
      if (!root) return;
      if (view === "form") (emailRef.current ?? closeRef.current)?.focus();
      else if (view === "editor") (nameRef.current ?? closeRef.current)?.focus();
      else if (!root.contains(document.activeElement)) (view === "account" ? root : closeRef.current ?? root).focus();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [open, view]);

  useEffect(() => {
    if (editingProfile) return;
    setProfileName(account.user?.display_name?.trim() || "");
    setProfileAvatar(account.user?.avatar_data_url || "");
    setProfileError("");
  }, [account.user?.id, account.user?.display_name, account.user?.avatar_data_url, editingProfile]);

  useEffect(() => {
    if (!open) {
      initialProfileEditConsumedRef.current = false;
      return;
    }
    if (
      !initialProfileEdit
      || initialProfileEditConsumedRef.current
      || !ready
      || !account.authenticated
      || !account.user
    ) return;
    initialProfileEditConsumedRef.current = true;
    onClearError();
    setProfileName(account.user.display_name?.trim() || "");
    setProfileAvatar(account.user.avatar_data_url || "");
    setProfileError("");
    setEditingProfile(true);
  }, [
    account.authenticated,
    account.user,
    initialProfileEdit,
    onClearError,
    open,
    ready,
  ]);

  useEffect(() => {
    if (editingProfile && error) setProfileError(accountErrorText(error, zh));
  }, [editingProfile, error, zh]);

  // A finished quick sign-in, or anything else that changes who is signed in, ends the wait for the browser.
  useEffect(() => {
    setOauthWaiting(null);
  }, [signedIn]);

  const beginProfileEdit = () => {
    onClearError();
    setProfileName(account.user?.display_name?.trim() || "");
    setProfileAvatar(account.user?.avatar_data_url || "");
    setProfileError("");
    setEditingProfile(true);
  };

  const cancelProfileEdit = () => {
    setProfileError("");
    if (closeAfterProfileEdit) onClose();
    else setEditingProfile(false);
  };

  const saveProfile = async () => {
    const name = profileName.trim();
    if (name.length > NAME_MAX) {
      setProfileError(zh ? `昵称最多 ${NAME_MAX} 个字符。` : `Nickname must be ${NAME_MAX} characters or fewer.`);
      return;
    }
    const ok = await onUpdateProfile(name, profileAvatar);
    if (ok) {
      setProfileError("");
      if (closeAfterProfileEdit) onClose();
      else setEditingProfile(false);
    }
  };

  const chooseAvatar = (file: File | undefined) => {
    if (!file) return;
    setProfileError("");
    setAvatarCropFile(file);
    if (avatarInputRef.current) avatarInputRef.current.value = "";
  };

  const switchMode = useCallback(
    (next: AuthMode) => {
      setMode(next);
      // A failed sign-in message is meaningless under the other mode.
      setLocalError("");
      setConfirm("");
      setConfirmTouched(false);
      setRevealPassword(false);
      setOauthWaiting(null);
      onClearError();
    },
    [onClearError],
  );

  const onKeyDown = (event: ReactKeyboardEvent<HTMLElement>) => {
    if (event.key === "Escape") {
      event.stopPropagation();
      if (!busy) onClose();
      return;
    }
    if (event.key !== "Tab") return;

    const root = dialogRef.current;
    if (!root) return;
    // Folded fields and the words that are fading out are inert; the picture's own button and the file input are
    // reached through the "Change photo" link instead.
    const focusable = Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE))
      .filter((element) => element.tabIndex >= 0 && !element.closest("[inert]"));
    if (!focusable.length) return;

    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    const active = document.activeElement;

    if (event.shiftKey) {
      if (active === first || active === root || !root.contains(active)) {
        event.preventDefault();
        last.focus();
      }
      return;
    }
    if (active === last || !root.contains(active)) {
      event.preventDefault();
      first.focus();
    }
  };

  const message = localError || (view === "form" ? accountErrorText(error, zh) : "");
  const shownMessage = useLastShown(message);
  const shownProfileError = useLastShown(profileError);

  if (!presence.mounted) return null;

  const emailValue = email.trim();
  const emailInvalid = emailTouched && email.length > 0 && !isValidAccountEmail(emailValue);
  const register = mode === "register";
  const confirmChecked = register && confirm.length > 0 && (confirmTouched || confirm.length >= password.length);
  const confirmMismatch = confirmChecked && confirm !== password;
  const confirmMatches = register && confirm.length > 0 && confirm === password;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setLocalError("");
    setOauthWaiting(null);

    if (!isValidAccountEmail(emailValue)) {
      setEmailTouched(true);
      setLocalError(zh ? "请输入有效的邮箱地址。" : "Enter a valid email address.");
      emailRef.current?.focus();
      return;
    }
    if (register) {
      if (password.length < ACCOUNT_PASSWORD_MIN_LENGTH) {
        setLocalError(
          zh
            ? `密码至少需要 ${ACCOUNT_PASSWORD_MIN_LENGTH} 位。`
            : `Use at least ${ACCOUNT_PASSWORD_MIN_LENGTH} characters.`,
        );
        passwordRef.current?.focus();
        return;
      }
      if (password !== confirm) {
        setConfirmTouched(true);
        setLocalError(zh ? "两次输入的密码不一致。" : "Passwords do not match.");
        confirmRef.current?.focus();
        return;
      }
    }

    const ok = register
      ? await onRegister(emailValue, password)
      : await onLogin(emailValue, password);
    if (ok) {
      setPassword("");
      setConfirm("");
      setRevealPassword(false);
    }
  };

  const startOAuth = async (provider: "google" | "github") => {
    onClearError();
    setLocalError("");
    setOauthWaiting(null);
    setOauthOpening(provider);
    try {
      const result = await window.loom.accountOAuthStart(provider);
      if (!result.ok) setLocalError(result.error.message || (zh ? "无法打开快捷登录。" : "Could not start quick sign-in."));
      else setOauthWaiting(provider);
    } catch {
      setLocalError(zh ? "无法打开浏览器授权，请重试。" : "Could not open browser sign-in. Try again.");
    } finally {
      setOauthOpening(null);
    }
  };

  const user = account.user;
  const displayName = user?.display_name?.trim() || user?.email || "";
  const offline = account.configured && !account.reachable;
  const serviceLabel = accountServiceLabel(account.serviceUrl);
  const serviceNote = serviceLabel ? `${zh ? "账号服务" : "Account service"}: ${serviceLabel}` : "";
  const quickSignIn = Boolean(capabilities?.google || capabilities?.github);

  // ---- the identity ------------------------------------------------------------------------------------------
  const editing = view === "editor";
  const picture = editing ? profileAvatar : (view === "account" ? user?.avatar_data_url || "" : "");
  const drawable = picture && picture !== brokenAvatar ? picture : "";
  const initial = ((editing ? profileName.trim() : "") || displayName).slice(0, 1).toUpperCase();
  const person = editing || view === "account";
  const markKey = !person ? "empty" : drawable ? `picture:${drawable.length}:${drawable.slice(-24)}` : `initial:${initial}`;
  const markFace = !person ? (
    <span className="acct-mark-face is-empty"><UserRound size={24} strokeWidth={1.6} /></span>
  ) : drawable ? (
    <span className="acct-mark-face is-picture">
      <img src={drawable} alt="" draggable={false} onError={() => setBrokenAvatar(drawable)} />
    </span>
  ) : (
    <span className="acct-mark-face is-initial">{initial || <UserRound size={24} strokeWidth={1.6} />}</span>
  );
  const status = view === "account" ? (offline ? "offline" : "online") : null;

  const title = (() => {
    switch (view) {
      case "checking": return zh ? "Loom 账号" : "Loom account";
      case "error": return zh ? "无法读取登录状态" : "Couldn't load your account";
      case "unconfigured": return zh ? "账号服务尚未配置" : "Account service isn't set up";
      case "form": return register ? (zh ? "创建 Loom 账号" : "Create your Loom account") : (zh ? "登录 Loom" : "Sign in to Loom");
      case "account": return displayName;
      case "editor": return zh ? "编辑个人资料" : "Edit profile";
    }
  })();
  const lead = (() => {
    switch (view) {
      case "checking": return zh ? "正在确认登录状态…" : "Checking your sign-in…";
      case "error": return accountErrorText(error, zh) || (zh ? "账号请求失败，请稍后重试。" : "The account request failed.");
      case "unconfigured": return zh ? "当前版本不会连接任何第三方服务。" : "This build doesn't connect to any third-party service.";
      case "form": return register
        ? (zh ? "注册后即可使用 Loom 的云端能力。" : "One account for Loom's cloud features.")
        : (zh ? "登录后即可使用与你账号绑定的云端能力。" : "Sign in to use Loom's cloud features.");
      case "account": return user?.email || "";
      case "editor": return zh ? "头像和昵称会同步到 Loom 各处。" : "Your photo and nickname appear across Loom.";
    }
  })();

  // ---- the views ---------------------------------------------------------------------------------------------
  const offlineNotice = (
    <Fold open={offline} className="acct-notice-fold">
      <p className="acct-notice" role="status">
        <span className="acct-pulse is-warning" aria-hidden="true" />
        <span>{zh ? "暂时连不上账号服务，正在自动重连。" : "Can't reach the account service. Reconnecting…"}</span>
        <button type="button" className="acct-link" data-press="none" onClick={() => void onRetry()} disabled={busy}>
          {zh ? "重试" : "Retry"}
        </button>
      </p>
    </Fold>
  );

  const spinner = <span className="acct-spinner" aria-hidden="true" />;

  const body = (() => {
    switch (view) {
      case "checking":
        return null;
      case "error":
        return (
          <div className="acct-view">
            <button type="button" className="acct-button is-secondary" onClick={() => void onRetry()} disabled={busy}>
              {zh ? "重试" : "Try again"}
            </button>
          </div>
        );
      case "unconfigured":
        return (
          <div className="acct-view">
            <p className="acct-explain">
              {zh ? "部署 Loom Account Service 后，为桌面端设置 " : "Deploy Loom Account Service, then set "}
              <code>LOOM_ACCOUNT_API_BASE_URL</code>
              {zh ? "。" : " for the desktop app."}
            </p>
            <button type="button" className="acct-button is-secondary" onClick={() => void onRetry()} disabled={busy}>
              {zh ? "重新检查" : "Check again"}
            </button>
          </div>
        );
      case "form":
        return (
          <div className="acct-view">
            {offlineNotice}
            {quickSignIn ? (
              <>
                <div className="acct-oauth">
                  {capabilities?.google ? (
                    <button type="button" className="acct-button is-secondary acct-oauth-button" disabled={busy || oauthOpening !== null} onClick={() => void startOAuth("google")}>
                      {oauthOpening === "google" ? spinner : <GoogleMark />}
                      <span>{oauthOpening === "google" ? (zh ? "正在打开…" : "Opening…") : "Google"}</span>
                    </button>
                  ) : null}
                  {capabilities?.github ? (
                    <button type="button" className="acct-button is-secondary acct-oauth-button" disabled={busy || oauthOpening !== null} onClick={() => void startOAuth("github")}>
                      {oauthOpening === "github" ? spinner : <GithubMark />}
                      <span>{oauthOpening === "github" ? (zh ? "正在打开…" : "Opening…") : "GitHub"}</span>
                    </button>
                  ) : null}
                </div>
                <Fold open={oauthWaiting !== null}>
                  <p className="acct-waiting" role="status">
                    <span className="acct-pulse" aria-hidden="true" />
                    {zh ? "请在浏览器中完成登录…" : "Finish signing in in your browser…"}
                  </p>
                </Fold>
                <div className="acct-divider" aria-hidden="true"><span>{zh ? "或" : "or"}</span></div>
              </>
            ) : null}

            <form className="acct-form" onSubmit={(event) => void submit(event)} noValidate>
              <div className="acct-field">
                <div className="acct-label-row">
                  <label htmlFor="loom-account-email">{zh ? "邮箱" : "Email"}</label>
                  <span className="acct-label-note tone-bad" id="loom-account-email-hint" aria-live="polite">
                    {emailInvalid ? (zh ? "邮箱格式不完整" : "Looks incomplete") : ""}
                  </span>
                </div>
                <input
                  id="loom-account-email"
                  ref={emailRef}
                  className="acct-input"
                  type="email"
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  onBlur={() => setEmailTouched(true)}
                  autoComplete="email"
                  disabled={busy}
                  spellCheck={false}
                  aria-invalid={emailInvalid}
                  aria-describedby={emailInvalid ? "loom-account-email-hint" : undefined}
                  placeholder="name@example.com"
                />
              </div>

              <div className="acct-field">
                <div className="acct-label-row">
                  <label htmlFor="loom-account-password">{zh ? "密码" : "Password"}</label>
                  <span className="acct-label-note" id="loom-account-password-hint">
                    <Crossfade identity={register ? "rule" : "none"}>
                      {register ? (zh ? `至少 ${ACCOUNT_PASSWORD_MIN_LENGTH} 位` : `At least ${ACCOUNT_PASSWORD_MIN_LENGTH} characters`) : ""}
                    </Crossfade>
                  </span>
                </div>
                <span className="acct-input-wrap">
                  <input
                    id="loom-account-password"
                    ref={passwordRef}
                    className="acct-input has-reveal"
                    type={revealPassword ? "text" : "password"}
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                    autoComplete={register ? "new-password" : "current-password"}
                    disabled={busy}
                    minLength={register ? ACCOUNT_PASSWORD_MIN_LENGTH : undefined}
                    aria-describedby={register ? "loom-account-password-hint" : undefined}
                    placeholder={register ? (zh ? "设置密码" : "Choose a password") : (zh ? "输入密码" : "Your password")}
                  />
                  <button
                    type="button"
                    className="acct-reveal"
                    onClick={() => setRevealPassword((value) => !value)}
                    disabled={busy}
                    aria-pressed={revealPassword}
                    aria-label={
                      revealPassword
                        ? (zh ? "隐藏密码" : "Hide password")
                        : (zh ? "显示密码" : "Show password")
                    }
                  >
                    {revealPassword ? <EyeOff size={16} /> : <Eye size={16} />}
                  </button>
                </span>
              </div>

              <Fold open={register}>
                <div className="acct-field">
                  <div className="acct-label-row">
                    <label htmlFor="loom-account-confirm">{zh ? "确认密码" : "Confirm password"}</label>
                    <span
                      className={`acct-label-note ${confirmMismatch ? "tone-bad" : confirmMatches ? "tone-ok" : ""}`}
                      id="loom-account-confirm-hint"
                      aria-live="polite"
                    >
                      {confirmMismatch
                        ? (zh ? "两次输入不一致" : "Doesn't match")
                        : confirmMatches
                          ? <><Check size={13} aria-hidden="true" />{zh ? "一致" : "Matches"}</>
                          : ""}
                    </span>
                  </div>
                  <input
                    id="loom-account-confirm"
                    ref={confirmRef}
                    className="acct-input"
                    type={revealPassword ? "text" : "password"}
                    value={confirm}
                    onChange={(event) => setConfirm(event.target.value)}
                    onBlur={() => setConfirmTouched(true)}
                    autoComplete="new-password"
                    disabled={busy}
                    minLength={ACCOUNT_PASSWORD_MIN_LENGTH}
                    aria-invalid={confirmMismatch}
                    aria-describedby={confirmMismatch || confirmMatches ? "loom-account-confirm-hint" : undefined}
                    placeholder={zh ? "再输入一次" : "Type it again"}
                  />
                </div>
              </Fold>

              <Fold open={Boolean(message)}>
                {message ? (
                  <p className="acct-error" role="alert"><AlertCircle size={14} aria-hidden="true" />{message}</p>
                ) : (
                  <p className="acct-error" aria-hidden="true"><AlertCircle size={14} aria-hidden="true" />{shownMessage}</p>
                )}
              </Fold>

              <button className="acct-button is-primary acct-submit" type="submit" disabled={busy}>
                <Crossfade identity={busy ? "busy" : mode}>
                  {busy ? (
                    <span className="acct-button-label">{spinner}{register ? (zh ? "正在创建…" : "Creating account…") : (zh ? "正在登录…" : "Signing in…")}</span>
                  ) : (
                    <span className="acct-button-label">{register ? (zh ? "创建账号" : "Create account") : (zh ? "登录" : "Sign in")}</span>
                  )}
                </Crossfade>
              </button>
            </form>

            <p className="acct-switch">
              <Crossfade identity={mode}>
                <span>
                  {register ? (zh ? "已有账号？" : "Already have an account?") : (zh ? "还没有账号？" : "New to Loom?")}
                  <button type="button" className="acct-link" data-press="none" onClick={() => switchMode(register ? "login" : "register")} disabled={busy}>
                    {register ? (zh ? "登录" : "Sign in") : (zh ? "创建账号" : "Create account")}
                  </button>
                </span>
              </Crossfade>
            </p>

            <p
              className="acct-footnote"
              title={[
                zh
                  ? "密码不会保存到本机；只有加密后的会话令牌会进入系统安全存储。"
                  : "Your password is never stored locally; only an encrypted session token enters the system's secure storage.",
                serviceNote,
              ].filter(Boolean).join("\n")}
            >
              <ShieldCheck size={13} aria-hidden="true" />
              {zh ? "密码不会保存在这台设备上" : "Your password never stays on this device"}
            </p>
          </div>
        );
      case "account":
        return (
          <div className="acct-view">
            {offlineNotice}
            <div className="acct-list">
              <button type="button" className="acct-row" onClick={beginProfileEdit} disabled={busy}>
                <Pencil size={16} aria-hidden="true" />
                <span>{zh ? "编辑个人资料" : "Edit profile"}</span>
                <ChevronRight className="acct-row-chevron" size={16} aria-hidden="true" />
              </button>
              {onOpenProfile ? (
                <button type="button" className="acct-row" onClick={() => { onClose(); onOpenProfile(); }} disabled={busy}>
                  <Pulse size={16} aria-hidden="true" />
                  <span>{zh ? "个人主页与使用洞察" : "Profile & insights"}</span>
                  <ChevronRight className="acct-row-chevron" size={16} aria-hidden="true" />
                </button>
              ) : null}
            </div>
            <div className="acct-foot">
              <button type="button" className="acct-signout" disabled={busy} onClick={() => void onLogout()}>
                <Crossfade identity={busy ? "busy" : "idle"}>
                  {busy
                    ? <span className="acct-button-label">{spinner}{zh ? "正在退出…" : "Signing out…"}</span>
                    : <span className="acct-button-label"><LogOut size={15} aria-hidden="true" />{zh ? "退出登录" : "Sign out"}</span>}
                </Crossfade>
              </button>
              <span
                className="acct-secure"
                title={[
                  zh ? "会话凭据保存在系统安全存储中，密码不会保存在本机。" : "Your session is kept in the system's secure storage; your password is never stored.",
                  serviceNote,
                ].filter(Boolean).join("\n")}
              >
                <ShieldCheck size={13} aria-hidden="true" />
                {zh ? "凭据已安全保存在本机" : "Stored securely on this device"}
              </span>
            </div>
          </div>
        );
      case "editor":
        return (
          <div className="acct-view">
            <div className="acct-label-row acct-photo-row">
              <span className="acct-label">{zh ? "头像" : "Photo"}</span>
              <span className="acct-photo-actions">
                {profileAvatar ? (
                  <button type="button" className="acct-link is-quiet" data-press="none" onClick={() => setProfileAvatar("")} disabled={busy}>
                    {zh ? "移除" : "Remove"}
                  </button>
                ) : null}
                <button
                  type="button"
                  className="acct-link"
                  data-press="none"
                  onClick={() => avatarInputRef.current?.click()}
                  disabled={busy}
                  title={zh ? "PNG、JPEG 或 WebP，上传后可裁剪" : "PNG, JPEG or WebP. You can crop it next."}
                >
                  <Upload size={14} aria-hidden="true" />
                  {profileAvatar ? (zh ? "更换" : "Change") : (zh ? "上传" : "Upload")}
                </button>
              </span>
            </div>
            <div className="acct-field acct-name-field">
              <div className="acct-label-row">
                <label htmlFor="loom-account-nickname">{zh ? "昵称" : "Nickname"}</label>
                <span className="acct-label-note is-count">{profileName.trim().length} / {NAME_MAX}</span>
              </div>
              <input
                id="loom-account-nickname"
                ref={nameRef}
                className="acct-input"
                value={profileName}
                onChange={(event) => setProfileName(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !busy) { event.preventDefault(); void saveProfile(); }
                }}
                maxLength={NAME_MAX}
                placeholder={user?.email.split("@")[0]}
                disabled={busy}
              />
            </div>
            <Fold open={Boolean(profileError)}>
              {profileError ? (
                <p className="acct-error" role="alert"><AlertCircle size={14} aria-hidden="true" />{profileError}</p>
              ) : (
                <p className="acct-error" aria-hidden="true"><AlertCircle size={14} aria-hidden="true" />{shownProfileError}</p>
              )}
            </Fold>
            <div className="acct-actions">
              <button type="button" className="acct-button is-secondary" onClick={cancelProfileEdit} disabled={busy}>
                {zh ? "取消" : "Cancel"}
              </button>
              <button type="button" className="acct-button is-primary" onClick={() => void saveProfile()} disabled={busy}>
                <Crossfade identity={busy ? "busy" : "idle"}>
                  {busy
                    ? <span className="acct-button-label">{spinner}{zh ? "正在保存…" : "Saving…"}</span>
                    : <span className="acct-button-label">{zh ? "保存" : "Save"}</span>}
                </Crossfade>
              </button>
            </div>
          </div>
        );
    }
  })();

  return (
    <div
      className="loom-account-backdrop pl-scrim pl-reveal"
      data-motion-phase={presence.phase}
      inert={!open}
      role="presentation"
      onMouseDown={
        presence.phase === "exiting"
          ? undefined
          : (event) => {
              // Only a press that both starts and ends on the backdrop dismisses
              // the dialog; a drag that began inside it must not.
              if (event.target === event.currentTarget && !busy) onClose();
            }
      }
    >
      <section
        ref={dialogRef}
        className="loom-account-dialog"
        data-view={view}
        role="dialog"
        aria-modal="true"
        aria-labelledby="loom-account-title"
        aria-describedby="loom-account-lead"
        aria-busy={busy}
        tabIndex={-1}
        onKeyDown={onKeyDown}
      >
        <button
          ref={closeRef}
          type="button"
          className="loom-account-close pl-icon-button"
          onClick={onClose}
          disabled={busy}
          aria-label={zh ? "关闭" : "Close"}
        >
          <X size={16} />
        </button>

        <header className="acct-head">
          <span className={`acct-mark${view === "checking" ? " is-checking" : ""}${editing ? " is-editable" : ""}`}>
            <Crossfade identity={markKey} className="acct-mark-faces">{markFace}</Crossfade>
            {editing ? (
              <button
                type="button"
                className="acct-mark-edit"
                tabIndex={-1}
                onClick={() => avatarInputRef.current?.click()}
                disabled={busy}
                aria-label={zh ? "更换头像" : "Change photo"}
              >
                <Camera size={18} aria-hidden="true" />
              </button>
            ) : null}
            {status ? (
              <span
                key={status}
                className={`acct-mark-dot is-${status}`}
                role="img"
                aria-label={status === "online" ? (zh ? "已登录" : "Signed in") : (zh ? "已登录，暂时离线" : "Signed in, offline")}
                title={status === "online" ? (zh ? "已登录" : "Signed in") : (zh ? "已登录，暂时离线" : "Signed in, offline")}
              />
            ) : editing ? (
              <span className="acct-mark-badge" aria-hidden="true"><Camera size={12} /></span>
            ) : null}
          </span>
          <h2 id="loom-account-title" className="acct-title">
            <Crossfade identity={title}>{title}</Crossfade>
          </h2>
          <p id="loom-account-lead" className="acct-lead">
            <Crossfade identity={lead}>{lead}</Crossfade>
          </p>
        </header>

        <div ref={stageRef} className="acct-stage">
          <Crossfade block identity={view} className="acct-views">{body}</Crossfade>
        </div>

        <input
          ref={avatarInputRef}
          className="acct-file-input"
          tabIndex={-1}
          type="file"
          accept="image/png,image/jpeg,image/webp"
          onChange={(event) => chooseAvatar(event.target.files?.[0])}
          disabled={busy || !editing}
        />
      </section>
      <AvatarCropDialog
        file={avatarCropFile ?? (cropPresence.mounted ? lastCropFileRef.current : null)}
        open={Boolean(avatarCropFile)}
        phase={cropPresence.phase}
        zh={zh}
        onCancel={() => setAvatarCropFile(null)}
        onApply={(dataUrl) => {
          setProfileAvatar(dataUrl);
          setProfileError("");
          setAvatarCropFile(null);
        }}
      />
    </div>
  );
}
