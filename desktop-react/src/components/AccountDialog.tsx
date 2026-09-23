import { CheckCircle2, LogOut, ShieldCheck, UserRound, X } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { useI18n } from "../i18n";
import { useMotionPresence } from "../motion/useMotionPresence";
import type { LoomAccountSnapshot } from "../types/account";
import "./account-auth.css";

interface AccountDialogProps {
  open: boolean;
  account: LoomAccountSnapshot;
  ready: boolean;
  busy: boolean;
  error: string;
  onClose(): void;
  onLogin(email: string, password: string): Promise<boolean>;
  onRegister(email: string, password: string): Promise<boolean>;
  onLogout(): Promise<void>;
}

type AuthMode = "login" | "register";

export function AccountDialog({
  open,
  account,
  ready,
  busy,
  error,
  onClose,
  onLogin,
  onRegister,
  onLogout,
}: AccountDialogProps) {
  const { language } = useI18n();
  const zh = language === "zh-CN";
  const presence = useMotionPresence(open, 190);
  const [mode, setMode] = useState<AuthMode>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [localError, setLocalError] = useState("");

  useEffect(() => {
    if (!open) {
      setPassword("");
      setConfirm("");
      setLocalError("");
    }
  }, [open]);

  if (!presence.mounted) return null;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setLocalError("");
    if (mode === "register" && password !== confirm) {
      setLocalError(zh ? "两次输入的密码不一致。" : "Passwords do not match.");
      return;
    }
    const ok = mode === "login"
      ? await onLogin(email.trim(), password)
      : await onRegister(email.trim(), password);
    if (ok) {
      setPassword("");
      setConfirm("");
    }
  };

  const displayName = account.user?.display_name?.trim() || account.user?.email || "";

  return (
    <div
      className="loom-account-backdrop"
      data-motion-phase={presence.phase}
      role="presentation"
      onMouseDown={presence.phase === "exiting" ? undefined : onClose}
    >
      <section
        className="loom-account-dialog"
        role="dialog"
        aria-modal="true"
        aria-label={zh ? "Loom 账号" : "Loom account"}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <button
          type="button"
          className="loom-account-close"
          onClick={onClose}
          aria-label={zh ? "关闭" : "Close"}
        >
          <X size={17} />
        </button>

        <header className="loom-account-header">
          <span className="loom-account-mark"><UserRound size={21} /></span>
          <div>
            <h2>{account.authenticated ? (zh ? "Loom 账号" : "Loom account") : (zh ? "登录 Loom" : "Sign in to Loom")}</h2>
            <p>
              {account.authenticated
                ? (zh ? "你的身份会用于后续的模型权限、额度和云端服务。" : "Your identity will be used for model access, quotas, and cloud services.")
                : (zh ? "登录后即可使用与你账号绑定的云端能力。" : "Sign in to use cloud features attached to your account.")}
            </p>
          </div>
        </header>

        {!ready ? (
          <div className="loom-account-state">{zh ? "正在检查登录状态…" : "Checking sign-in status…"}</div>
        ) : !account.configured ? (
          <div className="loom-account-unconfigured">
            <ShieldCheck size={20} />
            <strong>{zh ? "账号服务尚未配置" : "Account service is not configured"}</strong>
            <p>
              {zh
                ? "当前版本不会连接任何第三方站点。部署 Loom Account Service 后，只需为桌面端设置 LOOM_ACCOUNT_API_BASE_URL。"
                : "This build will not connect to any third-party service. Deploy Loom Account Service and set LOOM_ACCOUNT_API_BASE_URL for the desktop app."}
            </p>
          </div>
        ) : account.authenticated && account.user ? (
          <div className="loom-account-signed-in">
            <div className="loom-account-profile">
              <span className="loom-account-avatar">{displayName.slice(0, 1).toUpperCase()}</span>
              <div>
                <strong>{displayName}</strong>
                <span>{account.user.email}</span>
              </div>
              <CheckCircle2 size={18} />
            </div>
            <div className="loom-account-security-note">
              <ShieldCheck size={16} />
              <span>{zh ? "登录凭据已由系统安全存储加密保存。" : "Sign-in credentials are encrypted with the operating system secure storage."}</span>
            </div>
            <button
              type="button"
              className="loom-account-secondary-button"
              disabled={busy}
              onClick={() => void onLogout()}
            >
              <LogOut size={15} />
              {busy ? (zh ? "正在退出…" : "Signing out…") : (zh ? "退出登录" : "Sign out")}
            </button>
          </div>
        ) : (
          <>
            <div className="loom-account-tabs">
              <button type="button" className={mode === "login" ? "active" : ""} onClick={() => setMode("login")}>
                {zh ? "登录" : "Sign in"}
              </button>
              <button type="button" className={mode === "register" ? "active" : ""} onClick={() => setMode("register")}>
                {zh ? "注册" : "Create account"}
              </button>
            </div>

            <form className="loom-account-form" onSubmit={(event) => void submit(event)}>
              <label>
                <span>{zh ? "邮箱" : "Email"}</span>
                <input
                  type="email"
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  autoComplete="email"
                  required
                  placeholder="name@example.com"
                />
              </label>
              <label>
                <span>{zh ? "密码" : "Password"}</span>
                <input
                  type="password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  autoComplete={mode === "login" ? "current-password" : "new-password"}
                  required
                  minLength={8}
                  placeholder="••••••••"
                />
              </label>
              {mode === "register" ? (
                <label>
                  <span>{zh ? "确认密码" : "Confirm password"}</span>
                  <input
                    type="password"
                    value={confirm}
                    onChange={(event) => setConfirm(event.target.value)}
                    autoComplete="new-password"
                    required
                    minLength={8}
                    placeholder="••••••••"
                  />
                </label>
              ) : null}

              {localError || error ? <p className="loom-account-error">{localError || error}</p> : null}

              <button className="loom-account-primary-button" type="submit" disabled={busy}>
                {busy
                  ? (zh ? "处理中…" : "Working…")
                  : mode === "login"
                    ? (zh ? "登录 Loom" : "Sign in to Loom")
                    : (zh ? "创建 Loom 账号" : "Create Loom account")}
              </button>
            </form>

            <p className="loom-account-footnote">
              <ShieldCheck size={13} />
              {zh
                ? "密码不会保存到本机；只有加密后的会话令牌会进入系统安全存储。"
                : "Your password is never stored locally; only encrypted session tokens enter OS secure storage."}
            </p>
          </>
        )}
      </section>
    </div>
  );
}
