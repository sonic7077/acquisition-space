import { useEffect, useState } from "react";
import { api } from "../api";
import "./ReviewChecks.css";

type MatchState = "compared" | "missing_reference" | "missing_body" | "not_applicable" | "restricted";
type MatchedSentence = {
  text: string;
  matched: number;
  fragments: { text: string; length: number }[];
};

export interface ReviewCheck {
  id: string;
  name: string;
  passed: boolean;
  blocking: boolean;
  count?: number;
  limit?: number;
  phrases?: string[];
  state?: MatchState;
  ratio?: number | null;
  min_match?: number;
  threshold?: number;
  matched?: number;
  total?: number;
  matched_sentences?: MatchedSentence[];
}

const missing: Partial<Record<MatchState, string>> = {
  missing_reference: "等待提供对标逐字稿",
  missing_body: "填写正文后计算重合度",
  not_applicable: "无对标来源，重合度不适用",
  restricted: "没有对标素材权限",
};
const percent = new Intl.NumberFormat("zh-CN", { style: "percent", maximumFractionDigits: 3 });

function OverlapReport({ check, live }: { check: ReviewCheck; live?: boolean }) {
  const summary = check.state === "compared" && check.ratio != null && check.threshold != null
    ? `${percent.format(check.ratio)} ${check.passed ? "未超过" : "超过"} ${percent.format(check.threshold)}`
    : null;
  const sentences = check.matched_sentences || [];
  return (
    <section className="review-overlap" aria-label="和对标逐字稿的重合度">
      <div className="review-check-heading">
        <h3>和对标逐字稿的重合度</h3>
        {summary && (
          <strong className={check.passed ? "review-pass-text" : "review-fail-text"}>
            {summary}
          </strong>
        )}
      </div>
      <p className="muted">
        正文与对标逐字稿比较，去掉空白和标点后，连续 {check.min_match} 字及以上相同的正文位置只计一次。
        {live && "编辑正文或对标逐字稿时实时更新。"}
      </p>
      {summary ? <>
        <p className={check.passed ? "muted" : "review-fail-text"}>
          命中 {check.matched} / {check.total} 字。{!check.passed && "请改写命中片段，降至阈值以内后再批准。"}
        </p>
        {sentences.length ? <>
          <p className="muted">按每句命中字数由多到少排列。跨句命中按所在原句展示。</p>
          <ul className="review-snippets" aria-label="命中原句">
            {sentences.map((sentence, index) => (
              <li key={index}>
                <p>{sentence.text}</p>
                {sentence.fragments.map((fragment, fragmentIndex) => (
                  <div className="review-fragment" key={fragmentIndex}>
                    <span>重合片段（{fragment.length} 字）</span>
                    <mark>{fragment.text}</mark>
                  </div>
                ))}
              </li>
            ))}
          </ul>
        </> : <p className="muted">未发现达到连续长度要求的片段。</p>}
      </> : (
        <p className={check.blocking ? "review-fail-text" : "muted"}>{check.state ? missing[check.state] : "重合度暂不可用"}</p>
      )}
    </section>
  );
}

export function ReviewChecks({ checks, onReviewFacts, live }: {
  checks?: ReviewCheck[];
  onReviewFacts?: () => void;
  live?: boolean;
}) {
  if (!checks?.length) return null;
  const overlap = checks.find(check => check.id === "overlap");
  const rules = checks.filter(check => check.id !== "overlap");
  const blocked = rules.some(check => !check.passed && check.blocking);
  const hasAdvisory = rules.some(check => !check.passed && !check.blocking);
  return (
    <div className="checks review-checks">
      <section aria-label="规则体检">
        <div className="review-check-heading">
          <h3>规则体检</h3>
          <strong className={blocked ? "review-fail-text" : hasAdvisory ? "review-warning-text" : "review-pass-text"}>
            {blocked ? "有阻塞项" : hasAdvisory ? "有改稿提示" : "全部通过"}
          </strong>
        </div>
        {rules.map(check => {
          const status = check.passed ? "通过" : check.blocking ? "需修正" : "提示";
          return (
            <div key={check.id} className={"check-result " + (check.passed ? "pass" : check.blocking ? "fail" : "advisory")}>
              <span aria-hidden="true">{check.passed ? "✓" : "!"}</span>
              <div>
                {check.name}
                {check.count != null && <small>已填写 {check.count} 字，上限 {check.limit} 字</small>}
                {!!check.phrases?.length && <small>命中：{check.phrases.join("、")}</small>}
                {check.id === "facts" && !check.passed && <>
                  <small>待核实。核对正文中的数据、政策、产品条款或案例，确认事实状态并保存新版本后再批准。</small>
                  {onReviewFacts && <button type="button" className="review-fact-action" onClick={onReviewFacts}>去核验事实</button>}
                </>}
                {check.id === "resource_hook" && !check.passed && <small>如有可提供的真实资料，可在结尾说明资料用途。</small>}
              </div>
              <strong>{status}</strong>
            </div>
          );
        })}
      </section>
      {overlap && <OverlapReport check={overlap} live={live} />}
    </div>
  );
}

interface EditableFields {
  draft_id?: string;
  version?: number;
  ip_id?: string;
  skill: string;
  title: string;
  body: string;
  reference?: string;
  fact_status: string;
}

export function LiveReviewChecks({ data }: { data: EditableFields }) {
  const body = JSON.stringify(data);
  const [result, setResult] = useState<{ body: string; checks?: ReviewCheck[]; error?: string }>();
  const available = !!(data.draft_id || data.ip_id);
  useEffect(() => {
    if (!available) return;
    const controller = new AbortController();
    const timer = window.setTimeout(async () => {
      try {
        const report = await api<{ checks: ReviewCheck[] }>("/drafts/checks", { method: "POST", body, signal: controller.signal });
        if (!controller.signal.aborted) setResult({ body, checks: report.checks });
      } catch (error) {
        if (!controller.signal.aborted) setResult({ body, error: error instanceof Error ? error.message : "请稍后重试" });
      }
    }, 220);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [body, available]);

  if (!available) return <p className="muted">选择目标 IP 后开始规则体检。</p>;
  if (result?.body !== body) return <p className="muted">正在检查当前改动…</p>;
  if (result.error) return <p className="error-text">检查暂不可用。{result.error}</p>;
  return <ReviewChecks checks={result.checks} live />;
}
