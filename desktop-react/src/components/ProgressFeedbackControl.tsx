import type { ProgressFeedbackMode } from "../types/loom";
import { useI18n } from "../i18n";
import "./progress-feedback-control.css";

export function ProgressFeedbackControl({ mode, saving, onChange }: {
  mode?: ProgressFeedbackMode;
  saving: boolean;
  onChange(mode: ProgressFeedbackMode): void;
}) {
  const { language } = useI18n();
  const zh = language === "zh-CN";
  const choices: { value: ProgressFeedbackMode; title: string; description: string }[] = [
    { value: "quiet", title: zh ? "安静" : "Quiet", description: zh ? "持续工作，主要反馈重要结果和阻塞。" : "Work continuously; report major results and blockers." },
    { value: "balanced", title: zh ? "适中" : "Balanced", description: zh ? "在阶段完成、关键发现或方向变化时反馈。" : "Report milestones, useful discoveries and changes of approach." },
    { value: "detailed", title: zh ? "详细" : "Detailed", description: zh ? "增加中间发现和必要的操作说明，便于观察。" : "Include intermediate findings and useful explanations of actions." },
  ];
  return <div className="progress-feedback-control">
    <fieldset className="progress-feedback-options" disabled={saving || !mode} aria-label={zh ? "进度反馈" : "Progress feedback"}>
      {choices.map(choice => <label key={choice.value} className={`progress-feedback-option ${mode === choice.value ? "is-selected" : ""}`}>
        <span className="progress-feedback-option-heading">
          <input type="radio" name="progressFeedback" value={choice.value} checked={mode === choice.value}
            onChange={() => onChange(choice.value)} />
          <strong>{choice.title}</strong>
          {choice.value === "balanced" ? <small>{zh ? "默认" : "Default"}</small> : null}
        </span>
        <span className="progress-feedback-description">{choice.description}</span>
      </label>)}
    </fieldset>
    <p className="progress-feedback-status" role="status" aria-live="polite">
      {saving ? (zh ? "正在保存到 Host…" : "Saving to Host…") : !mode
        ? (zh ? "连接 Host 并读取设置后可调整。" : "Connect to Host and load settings to adjust this preference.")
        : (zh ? "对所有对话生效，从下一次模型请求开始采用。各档均会及时报告阻塞和需要你决定的事项；不改变最终答复的详略。"
          : "Applies to all conversations from the next model request. Every level reports blockers and required decisions promptly; final-answer detail is unchanged.")}
    </p>
  </div>;
}
