import { useEffect, useState, type FormEvent } from "react";
import { RefreshCw, MessageSquare, ThumbsUp, Pin, Sparkles } from "lucide-react";
import { api, post, type Row } from "../../api";
import { Card, Empty, Field, Modal, Notice } from "../../components/ui";
import { type Ctx } from "../../context";

export default function OpsInteractions(c: Ctx) {
  const [comments, setComments] = useState<Row[]>([]),
    [sessions, setSessions] = useState<Row[]>([]),
    [summary, setSummary] = useState<Row | null>(null),
    [allowWrite, setAllowWrite] = useState(false),
    [notice, setNotice] = useState(""),
    [replyTarget, setReplyTarget] = useState<Row | null>(null),
    [replyDraft, setReplyDraft] = useState(""),
    [msgTarget, setMsgTarget] = useState<Row | null>(null),
    [messages, setMessages] = useState<Row[]>([]),
    [msgDraft, setMsgDraft] = useState("");

  const load = () => {
    api<Row>("/ops/comments")
      .then((d) => {
        setComments(d.list || []);
        setAllowWrite(Boolean(d.allow_interaction_write));
        if (d.notice) setNotice(d.notice);
      })
      .catch(() => {});
    api<Row>("/ops/comments/summary").then(setSummary).catch(() => {});
    api<Row>("/ops/messages/sessions")
      .then((d) => {
        setSessions(d.list || []);
        if (d.allow_interaction_write !== undefined) setAllowWrite(Boolean(d.allow_interaction_write));
        if (d.notice && !notice) setNotice(d.notice);
      })
      .catch(() => {});
  };

  useEffect(() => {
    load();
  }, [c.version]);

  async function submitCommentReply(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!replyTarget) return;
    await c.run(async () => {
      await post(`/ops/comments/${replyTarget.commentId}/reply`, {
        account_id: replyTarget.account_id,
        content: replyDraft,
      });
      setReplyTarget(null);
      setReplyDraft("");
      load();
    }, "已发送评论回复");
  }

  async function submitSessionMessage(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!msgTarget) return;
    await c.run(async () => {
      await post(`/ops/messages/sessions/${msgTarget.sessionId}/send`, {
        account_id: msgTarget.account_id,
        text: msgDraft,
      });
      setMsgDraft("");
      const d = await api<Row>(`/ops/messages/sessions/${msgTarget.sessionId}/messages?account_id=${encodeURIComponent(msgTarget.account_id)}&refresh=true`);
      setMessages(d.list || []);
    }, "私信已发送");
  }

  return (
    <>
      <Notice>
        {notice ||
          (allowWrite
            ? "当前已开启受控互动外发：回复评论、点赞、置顶与发送私信均须本人点击确认并记入审计日志。"
            : "当前处于只读模式（仅浏览评论、私信记录与生成 AI 建议草稿）；外发写入需管理员在「系统设置 → 发布执行器」开启。")}
      </Notice>

      <Card
        title={`评论聚合 · ${comments.length}`}
        subtitle={
          summary && (summary.todayNew !== undefined || summary.unreplied !== undefined)
            ? `今日新增评论 ${summary.todayNew ?? 0} · 今日待回复 ${summary.unreplied ?? 0}`
            : undefined
        }
        action={
          <button onClick={load}>
            <RefreshCw size={15} />
            刷新互动
          </button>
        }
      >
        {comments.length ? (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>评论内容</th>
                  <th>评论者</th>
                  <th>回复状态</th>
                  <th>时间</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {comments.map((cm, idx) => (
                  <tr key={cm.commentId || idx}>
                    <td>{cm.content || "—"}</td>
                    <td>{cm.nickname || cm.userName || "—"}</td>
                    <td>{cm.replied ? "已回复" : "待处理"}</td>
                    <td>{cm.createTime || "—"}</td>
                    <td>
                      <div className="button-row">
                        <button
                          className="text-button"
                          disabled={!cm.account_id}
                          onClick={() =>
                            c.run(async () => {
                              const res = await post("/ops/interactions/draft-reply", {
                                account_id: cm.account_id,
                                comment_id: String(cm.commentId),
                              });
                              setReplyDraft(String(res.draft || res.reply || res.content || ""));
                              setReplyTarget(cm);
                            }, "已生成 AI 回复建议草稿")
                          }
                        >
                          <Sparkles size={14} />
                          AI 草稿
                        </button>
                        <button
                          className="text-button"
                          disabled={!allowWrite || !cm.account_id}
                          title={!allowWrite ? "只读模式下禁用外发，请先在系统设置启用" : undefined}
                          onClick={() => {
                            setReplyDraft("");
                            setReplyTarget(cm);
                          }}
                        >
                          回复
                        </button>
                        <button
                          className="text-button"
                          disabled={!allowWrite || !cm.account_id}
                          onClick={() =>
                            c.run(async () => {
                              await post(`/ops/comments/${cm.commentId}/like`, { account_id: cm.account_id });
                            }, "已点赞评论")
                          }
                        >
                          <ThumbsUp size={14} />
                          点赞
                        </button>
                        <button
                          className="text-button"
                          disabled={!allowWrite || !cm.account_id}
                          onClick={() =>
                            c.run(async () => {
                              await post(`/ops/comments/${cm.commentId}/top`, { account_id: cm.account_id });
                            }, "已置顶评论")
                          }
                        >
                          <Pin size={14} />
                          置顶
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty title="暂无作品评论" body="当前授权账号在最近周期内暂无已同步的评论记录。" />
        )}
      </Card>

      <Card title={`私信会话 · ${sessions.length}`}>
        {sessions.length ? (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>对方昵称</th>
                  <th>最新消息</th>
                  <th>未读数</th>
                  <th>时间</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {sessions.map((s, idx) => (
                  <tr key={s.sessionId || idx}>
                    <td>{s.peerNickname || s.nickname || "—"}</td>
                    <td>{s.lastMessage || "—"}</td>
                    <td>{s.unreadCount ?? 0}</td>
                    <td>{s.lastTime || "—"}</td>
                    <td>
                      <button
                        className="text-button"
                        disabled={!s.account_id}
                        onClick={() =>
                          c.run(async () => {
                            const d = await api<Row>(
                              `/ops/messages/sessions/${s.sessionId}/messages?account_id=${encodeURIComponent(s.account_id)}&refresh=true`,
                            );
                            setMessages(d.list || []);
                            setMsgDraft("");
                            setMsgTarget(s);
                          })
                        }
                      >
                        <MessageSquare size={14} />
                        查看消息
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty title="暂无私信会话" body="当前授权账号暂无私信会话记录。" />
        )}
      </Card>

      {replyTarget && (
        <Modal title="回复评论" onClose={() => setReplyTarget(null)}>
          <Notice>原评论：{replyTarget.content}</Notice>
          <form onSubmit={submitCommentReply}>
            <Field label="回复正文">
              <textarea
                rows={4}
                required
                value={replyDraft}
                onChange={(e) => setReplyDraft(e.target.value)}
                placeholder="输入回复正文，或先点击 AI 草稿生成建议"
              />
            </Field>
            <button className="primary" disabled={!allowWrite}>
              {allowWrite ? "确认发送到平台" : "只读模式下禁止发送"}
            </button>
          </form>
        </Modal>
      )}

      {msgTarget && (
        <Modal title={`私信对话 · ${msgTarget.peerNickname || msgTarget.nickname}`} onClose={() => setMsgTarget(null)}>
          <div className="schema-preview">
            {messages.length ? (
              messages.map((m, idx) => (
                <p key={m.id || idx}>
                  <strong>{m.senderName || m.direction || "消息"}：</strong>
                  {m.text || m.content || "—"}
                </p>
              ))
            ) : (
              <p className="muted">暂无消息记录</p>
            )}
          </div>
          <form onSubmit={submitSessionMessage}>
            <Field label="发送私信回复">
              <textarea
                rows={3}
                required
                value={msgDraft}
                onChange={(e) => setMsgDraft(e.target.value)}
                placeholder="输入私信内容"
              />
            </Field>
            <div className="button-row">
              <button
                type="button"
                onClick={() =>
                  c.run(async () => {
                    const res = await post("/ops/interactions/draft-reply", {
                      account_id: msgTarget.account_id,
                      session_id: String(msgTarget.sessionId),
                    });
                    setMsgDraft(String(res.draft || res.reply || res.content || ""));
                  }, "已生成 AI 私信回复建议")
                }
              >
                <Sparkles size={14} />
                生成 AI 建议
              </button>
              <button className="primary" disabled={!allowWrite}>
                {allowWrite ? "确认发送私信" : "只读模式下禁止发送"}
              </button>
            </div>
          </form>
        </Modal>
      )}
    </>
  );
}