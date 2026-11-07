import qrcode from "qrcode-generator";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useI18n } from "../i18n";
import "./mobile-pairing.css";

type Pairing = { pairId: string; code: string; deadline: number };
type Claim = { phoneName: string; confirmCode: string };
type Phone = { id: string; name: string; created_at: number; last_used_at: number };

const POLL_MS = 1500;

const COPY = {
  en: {
    title: "Phone", intro: "Send messages and approve actions from your phone. The work still runs on this computer.",
    add: "Add phone", adding: "Preparing…", addTitle: "Connect a phone", addHint: "Install Loom on your phone, then show a code here to pair it.", scanTitle: "Scan with your phone", steps: ["Open Loom on your phone", "Tap Scan QR code", "Point the camera at this code"], scan: "Scan this code with Loom on your phone.", expires: (s: number) => `Expires in ${s} s`,
    expired: "The code expired.", retry: "Show a new code", cancel: "Cancel",
    confirm: (name: string) => `Allow “${name}” to control this computer?`, compare: "Check that your phone shows the same number.",
    allow: "Allow", deny: "Deny", done: "Phone added.", phones: "Paired phones", none: "No phones yet.",
    revoke: "Remove", lastUsed: "Last used", desktopOnly: "Pair a phone from the Loom desktop app.",
  },
  zh: {
    title: "手机", intro: "在手机上发消息、批准操作，工作仍在这台电脑上运行。",
    add: "添加手机", adding: "正在准备…", addTitle: "连接一部手机", addHint: "先在手机上安装 Loom，再在这里显示配对二维码。", scanTitle: "用手机扫描", steps: ["在手机上打开 Loom", "点「扫描二维码」", "对准左边的二维码"], scan: "用手机上的 Loom 扫描这个二维码。", expires: (s: number) => `${s} 秒后失效`,
    expired: "二维码已失效。", retry: "换一个二维码", cancel: "取消",
    confirm: (name: string) => `允许“${name}”控制这台电脑吗？`, compare: "确认手机上显示的是同一个数字。",
    allow: "允许", deny: "拒绝", done: "手机已添加。", phones: "已配对的手机", none: "还没有配对手机。",
    revoke: "移除", lastUsed: "最近使用", desktopOnly: "请在 Loom 桌面端配对手机。",
  },
} as const;

function QrCode({ value, label }: { value: string; label: string }) {
  const svg = useMemo(() => {
    const code = qrcode(0, "M");
    code.addData(value);
    code.make();
    return code.createSvgTag({ cellSize: 5, margin: 0, scalable: true });
  }, [value]);
  return <div className="mp-qr" role="img" aria-label={label} dangerouslySetInnerHTML={{ __html: svg }} />;
}

export function MobilePairingPanel() {
  const { language } = useI18n();
  const copy = COPY[language === "zh-CN" ? "zh" : "en"];
  const bridge = window.loom;
  const available = typeof bridge?.mobilePairStart === "function";
  const [pairing, setPairing] = useState<Pairing | null>(null);
  const [claim, setClaim] = useState<Claim | null>(null);
  const [phones, setPhones] = useState<Phone[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [now, setNow] = useState(() => Date.now());
  const decided = useRef(false);

  const fail = (cause: unknown) => setError(cause instanceof Error ? cause.message : String(cause));
  const reset = () => { setPairing(null); setClaim(null); decided.current = false; };

  const loadPhones = useCallback(async () => {
    if (!available) return;
    try { setPhones((await bridge.mobileDevices?.())?.devices ?? []); } catch (cause) { fail(cause); }
  }, [available, bridge]);

  useEffect(() => { void loadPhones(); }, [loadPhones]);

  const start = async () => {
    setBusy(true); setError(""); setNotice(""); reset();
    try {
      const issued = await bridge.mobilePairStart!();
      setPairing({ pairId: issued.pairId, code: issued.code, deadline: Date.now() + issued.expiresIn * 1000 });
      setNow(Date.now());
    } catch (cause) { fail(cause); }
    finally { setBusy(false); }
  };

  const expired = Boolean(pairing && now >= pairing.deadline);

  useEffect(() => {
    if (!pairing || expired || claim) return;
    const timer = window.setInterval(() => {
      setNow(Date.now());
      void bridge.mobilePairStatus!(pairing.pairId).then((status) => {
        if (status.state === "claimed") setClaim({ phoneName: status.phoneName, confirmCode: status.confirmCode });
      }).catch(() => {});
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [bridge, claim, expired, pairing]);

  const decide = async (approve: boolean) => {
    if (!pairing || decided.current) return;
    decided.current = true;
    setBusy(true); setError("");
    try {
      await bridge.mobilePairDecide!(pairing.pairId, approve);
      if (approve) setNotice(copy.done);
      reset();
      // The phone turns its approval into a session on its next poll.
      window.setTimeout(() => void loadPhones(), POLL_MS * 3);
    } catch (cause) { decided.current = false; fail(cause); }
    finally { setBusy(false); }
  };

  const revoke = async (phone: Phone) => {
    setError("");
    try { await bridge.mobileDeviceRevoke!(phone.id); await loadPhones(); } catch (cause) { fail(cause); }
  };

  if (!available) return <p className="mp-note">{copy.desktopOnly}</p>;

  return (
    <div className="mp">
      <div className="settings-page-heading"><div><h1>{copy.title}</h1><p>{copy.intro}</p></div></div>

      {claim ? (
        <section className="mp-panel is-confirm" aria-live="polite">
          <strong className="mp-code">{claim.confirmCode.slice(0, 3)} {claim.confirmCode.slice(3)}</strong>
          <div className="mp-copy">
            <h2>{copy.confirm(claim.phoneName)}</h2>
            <p>{copy.compare}</p>
            <div className="mp-actions">
              <button type="button" className="mp-button is-primary" disabled={busy} onClick={() => void decide(true)}>{copy.allow}</button>
              <button type="button" className="mp-button" disabled={busy} onClick={() => void decide(false)}>{copy.deny}</button>
            </div>
          </div>
        </section>
      ) : pairing && !expired ? (
        <section className="mp-panel is-code">
          <QrCode value={pairing.code} label={copy.scan} />
          <div className="mp-copy">
            <h2>{copy.scanTitle}</h2>
            <ol>{copy.steps.map((step) => <li key={step}>{step}</li>)}</ol>
            <small>{copy.expires(Math.max(0, Math.ceil((pairing.deadline - now) / 1000)))}</small>
            <div className="mp-actions"><button type="button" className="mp-button" onClick={reset}>{copy.cancel}</button></div>
          </div>
        </section>
      ) : (
        <section className="mp-panel is-idle">
          <div className="mp-copy">
            <h2>{pairing ? copy.expired : copy.addTitle}</h2>
            <p>{copy.addHint}</p>
          </div>
          <button type="button" className="mp-button is-primary" disabled={busy} onClick={() => void start()}>{busy ? copy.adding : pairing ? copy.retry : copy.add}</button>
        </section>
      )}

      {notice ? <p className="mp-note" role="status">{notice}</p> : null}
      {error ? <p className="mp-note is-error" role="alert">{error}</p> : null}

      <section className="mp-list">
        <h2>{copy.phones}</h2>
        {phones.length ? (
          <div className="mp-rows">
            {phones.map((phone) => (
              <div className="mp-phone" key={phone.id}>
                <div><strong>{phone.name}</strong><small>{copy.lastUsed} · {new Date(phone.last_used_at * 1000).toLocaleString()}</small></div>
                <button type="button" className="mp-button" onClick={() => void revoke(phone)}>{copy.revoke}</button>
              </div>
            ))}
          </div>
        ) : <p className="mp-note">{copy.none}</p>}
      </section>
    </div>
  );
}
