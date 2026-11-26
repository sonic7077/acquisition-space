import { useState, type FormEvent } from "react";
import { Plus } from "lucide-react";
import { post, patch, type Row } from "../api";
import {
  Badge,
  Empty,
  Card,
  Modal,
  Field,
  Notice,
  fmt,
  STATE,
} from "../components/ui";
import { type Ctx, useRows, filterIp } from "../context";

const wechatPlatforms = new Set(["视频号", "公众号"]);

export default function Schedules(c: Ctx) {
  const rows = filterIp(useRows("/schedules", c.version), c.ip),
    drafts = filterIp(useRows("/drafts", c.version), c.ip).filter(
      (d) => d.approved_version === d.version,
    ),
    [modal, setModal] = useState(false),
    [detail, setDetail] = useState<Row | null>(null);
  const days = Array.from({ length: 7 }, (_, i) => {
    const d = new Date();
    d.setDate(d.getDate() + i);
    return d;
  });
  async function create(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(e.currentTarget));
    await c.run(async () => {
      await post("/schedules", {
        ...data,
        planned_at: new Date(data.planned_at as string).toISOString(),
      });
      setModal(false);
    }, "发布计划已创建");
  }
  return (
    <>
      <Card
        title="接下来7天"
        subtitle="按北京时间查看计划；排期和实际发表分开记录"
        action={
          <button className="primary" onClick={() => setModal(true)}>
            <Plus size={16} />
            新增排期
          </button>
        }
      >
        <div className="calendar">
          {days.map((day, i) => (
            <div key={i} className={"day " + (i === 0 ? "today" : "")}>
              <div>
                <strong>
                  {i === 0
                    ? "今天"
                    : day.toLocaleDateString("zh-CN", { weekday: "short" })}
                </strong>
                <span>
                  {day.getMonth() + 1}/{day.getDate()}
                </span>
              </div>
              {rows
                .filter(
                  (r) =>
                    new Date(r.planned_at).toLocaleDateString("zh-CN", {
                      timeZone: "Asia/Shanghai",
                    }) ===
                    day.toLocaleDateString("zh-CN", {
                      timeZone: "Asia/Shanghai",
                    }),
                )
                .map((r) => (
                  <button
                    className="calendar-event"
                    key={r.id}
                    onClick={() => setDetail(r)}
                  >
                    <strong>{r.title}</strong>
                    <small>
                      {r.platform} · {r.account}
                    </small>
                    <Badge value={r.status} />
                  </button>
                ))}
              <small className="muted">
                {!rows.some(
                  (r) =>
                    new Date(r.planned_at).toDateString() ===
                    day.toDateString(),
                )
                  ? "暂无排期"
                  : ""}
              </small>
            </div>
          ))}
        </div>
      </Card>
      <Card title={`发布记录 · ${rows.length}`}>
        {rows.length ? (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>内容</th>
                  <th>账号 / 平台</th>
                  <th>计划时间</th>
                  <th>状态</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.id}>
                    <td>
                      <strong>{r.title}</strong>
                      <small>批准版本 v{r.version}</small>
                    </td>
                    <td>
                      {r.account} / {r.platform}
                    </td>
                    <td>{fmt(r.planned_at)}</td>
                    <td>
                      <Badge value={r.status} />
                    </td>
                    <td>
                      <div className="button-row">
                        <button
                          className="text-button"
                          onClick={() => setDetail(r)}
                        >
                          {wechatPlatforms.has(r.platform) ? "详情 / 微信生态" : "详情"}
                        </button>
                        {!["cancelled", "needs_review"].includes(r.status) && (
                          <button
                            className="text-button"
                            onClick={() => c.go("ops-tasks", r.id)}
                          >
                            去运营派发
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty
            title="暂无发布计划"
            body="已批准稿件具备视频资产后，即可安排平台和账号。"
          />
        )}
      </Card>
      {modal && (
        <Modal title="新增发布排期" onClose={() => setModal(false)}>
          <form onSubmit={create}>
            <Field label="当前批准稿件">
              <select name="draft_id" required>
                <option value="">请选择稿件</option>
                {drafts.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.title} / v{d.version}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="发布平台">
              <select name="platform">
                {["抖音", "视频号", "小红书", "哔哩哔哩", "公众号"].map((p) => (
                  <option key={p}>{p}</option>
                ))}
              </select>
            </Field>
            <Field label="明确发布账号">
              <input name="account" required />
            </Field>
            <Field label="计划时间（北京时间）">
              <input name="planned_at" type="datetime-local" required />
            </Field>
            <Notice>当前为人工执行流程。创建排期不代表平台已发表。</Notice>
            <button className="primary">创建计划</button>
          </form>
        </Modal>
      )}
      {detail && (
        <Modal
          title={detail.title + " · 发布详情"}
          onClose={() => setDetail(null)}
        >
          <p>
            {detail.account} / {detail.platform} · v{detail.version}
          </p>
          <Badge value={detail.status} />
          <form
            onSubmit={(e) => {
              e.preventDefault();
              const data = Object.fromEntries(new FormData(e.currentTarget));
              c.run(async () => {
                const r = await patch("/schedules/" + detail.id, data);
                setDetail(r);
              }, "发布状态已记录");
            }}
          >
            <Field label="实际执行状态">
              <select name="status" defaultValue={detail.status}>
                {["planned", "staged", "published", "unknown", "cancelled"].map(
                  (s) => (
                    <option key={s} value={s}>
                      {STATE[s]}
                    </option>
                  ),
                )}
              </select>
            </Field>
            <Field label="发表链接或人工核验凭证">
              <input
                name="evidence"
                defaultValue={detail.evidence || ""}
                placeholder="已发表时必填"
              />
            </Field>
            <button className="primary">保存执行结果</button>
          </form>
          {wechatPlatforms.has(detail.platform) && (
            <section aria-label="微信生态触点">
              <h3 className="subheading">微信生态 · 一题六触点</h3>
              <div className="touchpoints">
                {Object.entries(detail.touchpoints || {}).map(
                  ([channel, state]) => (
                    <div key={channel}>
                      <strong>{channel}</strong>
                      <select
                        aria-label={channel + "状态"}
                        value={state as string}
                        onChange={(e) => {
                          const status = e.target.value;
                          const evidence = ["published", "skipped"].includes(status)
                            ? window.prompt(
                                status === "published"
                                  ? "请输入发表凭证"
                                  : "请输入跳过原因",
                              ) || ""
                            : "";
                          c.run(async () => {
                            const r = await patch(
                              `/schedules/${detail.id}/touchpoints`,
                              { channel, status, evidence },
                            );
                            setDetail(r);
                          }, "触点状态已保存");
                        }}
                      >
                        {[
                          "unprepared",
                          "producing",
                          "review",
                          "dependency",
                          "manual",
                          "published",
                          "skipped",
                        ].map((s) => (
                          <option value={s} key={s}>
                            {STATE[s]}
                          </option>
                        ))}
                      </select>
                    </div>
                  ),
                )}
              </div>
            </section>
          )}
          <Notice>
            仅记录实际发表结果。生成文案、上传视频或暂存草稿，都不等同于发表。
          </Notice>
        </Modal>
      )}
    </>
  );
}
