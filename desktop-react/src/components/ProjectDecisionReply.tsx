import { DecisionPromptCard, DecisionPromptRecoveryCard, parseDecisionMessage } from "./DecisionPromptCard";
import { MarkdownMessage } from "./MarkdownMessage";
import { StreamingPresentation, usePendingPresentations } from "./StreamingPresentation";
import { useRuntimeCopy } from "./runtimeCopy";

interface Props {
  content: string;
  messageKey: string;
  workspace?: string;
  streaming?: boolean;
  live?: boolean;
  disabled?: boolean;
  onSubmit?(text: string): Promise<void> | void;
}

export function ProjectDecisionReply(props: Props) {
  return <StreamingPresentation><Reply {...props} /></StreamingPresentation>;
}

function Reply({ content, messageKey, workspace, streaming, live, disabled, onSubmit }: Props) {
  const parsed = parseDecisionMessage(content, Boolean(streaming));
  const copy = useRuntimeCopy();
  const painting = usePendingPresentations().has(messageKey);
  const ready = !live && !streaming && !painting;
  return <>
    {parsed.text ? <MarkdownMessage content={parsed.text} compact glow streaming={streaming} messageKey={messageKey} workspace={workspace} /> : null}
    {ready ? parsed.decisions.map((spec, index) => <DecisionPromptCard
      key={spec.id || `${messageKey}:decision:${index}`} spec={spec} disabled={disabled} onSubmit={onSubmit}
    />) : null}
    {ready && parsed.incomplete ? <DecisionPromptRecoveryCard disabled={disabled}
      onRetry={onSubmit ? () => onSubmit(copy.decisionRetryPrompt) : undefined} /> : null}
  </>;
}
