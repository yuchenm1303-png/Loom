import type { LongGoal } from "../components/projectOverview";
import { BranchGroup } from "./BuildBranches";
import { Disclosure } from "./Disclosure";

/** The 记录 page of a conversation running a long goal: whether the work was independently checked, what the goal
 *  wrote down along the way (its notes), the claims it stands behind with the command behind each, and what every
 *  review found. A goal is not a project: the project's receipt, notes and milestones were three empty cards here. */

const VERDICT: Record<string, string> = {
  passed: "通过", needs_changes: "需要修改", blocked: "无法核验", early_review: "中途评审",
};

const at = (seconds: number) =>
  seconds ? new Date(seconds * 1000).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false }) : "";

/** The first line of a note, short enough for a row title. */
const headline = (text: string) => {
  const line = text.split(/\n/).find((part) => part.trim()) || text;
  return line.length > 42 ? `${line.slice(0, 42)}…` : line;
};

function delivery(goal: LongGoal): { title: string; detail: string } {
  if (goal.status === "complete" && goal.checked) return { title: "已通过独立核验", detail: goal.summary };
  if (goal.status === "complete") return { title: "已结束，未经独立核验", detail: goal.summary };
  if (goal.phase === "checking") return { title: "已声明完成，正在独立核验", detail: "" };
  if (goal.status === "active") return { title: "还在进行，尚未核验", detail: "声明完成并通过独立核验后才算交付。" };
  return { title: "已停下，尚未核验", detail: goal.reason };
}

export function GoalRecords({ goal }: { goal: LongGoal }) {
  const records = goal.records ?? { notes: [], claims: [], reviews: [] };
  const notes = [...records.notes].reverse();
  const reviews = [...records.reviews].reverse();
  const shown = delivery(goal);
  return (
    <>
      <BranchGroup label="交付">
        <div className="m-card">
          <p>{shown.title}</p>
          {shown.detail ? <p className="m-note m-prewrap">{shown.detail}</p> : null}
        </div>
      </BranchGroup>
      <BranchGroup label="目标笔记" aside={<span className="m-group-count">{records.notes.length} 条</span>}>
        <div className="m-card m-goal-records">
          {notes.length ? notes.map((note, index) => (
            <Disclosure key={`${note.at}-${index}`} title={headline(note.text)} aside={<span className="m-group-count">{at(note.at)}</span>}>
              <p className="m-prewrap">{note.text}</p>
            </Disclosure>
          )) : <p>还没有笔记。每个长目标回合结束时会写一条：做了什么、发现了什么、接下来做什么。</p>}
        </div>
      </BranchGroup>
      <BranchGroup label="主张账本" aside={<span className="m-group-count">{records.claims.length} 条</span>}>
        <div className="m-card m-goal-records">
          {records.claims.length ? records.claims.map((claim, index) => (
            <Disclosure key={index} title={claim.claim}>
              <p className="m-note">复现命令</p>
              <p className="m-path">{claim.command}</p>
              <p className="m-note">结果位置</p>
              <p className="m-path">{claim.output}</p>
            </Disclosure>
          )) : <p>还没有登记主张。研究类目标的每个主要数字都要登记产生它的命令，核验会重跑。</p>}
        </div>
      </BranchGroup>
      <BranchGroup label="评审" aside={<span className="m-group-count">{records.reviews.length} 次</span>}>
        <div className="m-card m-goal-records">
          {reviews.length ? reviews.map((review, index) => (
            <Disclosure key={`${review.at}-${index}`}
              title={review.verdict === "early_review" ? "中途评审" : `第 ${review.round} 轮核验 · ${VERDICT[review.verdict] || review.verdict}`}
              aside={<span className="m-group-count">{at(review.at)}</span>}>
              {review.summary ? <p className="m-prewrap">{review.summary}</p> : null}
              {review.major.length ? <>
                <p className="m-note">{review.verdict === "early_review" ? "拒稿理由" : "必须修改"}</p>
                <ul className="m-build-list">{review.major.map((item, at) => <li key={at} className="m-prewrap">{item}</li>)}</ul>
              </> : null}
              {review.minor.length ? <>
                <p className="m-note">次要问题</p>
                <ul className="m-build-list">{review.minor.map((item, at) => <li key={at} className="m-prewrap">{item}</li>)}</ul>
              </> : null}
              {review.requests.length ? <>
                <p className="m-note">提出的改进（已加入验收标准）</p>
                <ul className="m-build-list">{review.requests.map((item, at) => <li key={at} className="m-prewrap">{item}</li>)}</ul>
              </> : null}
            </Disclosure>
          )) : <p>还没有评审。声明完成后，一个没看过这段对话的核验者会重读、重跑并给出结论。</p>}
        </div>
      </BranchGroup>
    </>
  );
}
