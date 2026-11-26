import { useEffect, useState } from "react";
import { RefreshCw, BarChart2, EyeOff } from "lucide-react";
import { api, post, type Row } from "../../api";
import { Card, Empty, Modal, Notice } from "../../components/ui";
import { type Ctx } from "../../context";

const cell = (v: any) => (v === null || v === undefined ? "—" : String(v));

export default function OpsPosts(c: Ctx) {
  const [posts, setPosts] = useState<Row[]>([]),
    [stats, setStats] = useState<Row[]>([]),
    [trend, setTrend] = useState<Row | null>(null),
    [dailyPost, setDailyPost] = useState<Row | null>(null),
    [dailyRows, setDailyRows] = useState<Row[]>([]),
    [visibleTarget, setVisibleTarget] = useState<Row | null>(null),
    [selectedAcc, setSelectedAcc] = useState("");

  const load = () => {
    const q = c.ip ? `?ip_id=${encodeURIComponent(c.ip)}` : "";
    api<Row[]>(`/ops/posts${q}`).then(setPosts).catch(() => {});
    api<Row[]>(`/ops/stats/accounts${q}`).then(setStats).catch(() => {});
    api<Row>(`/ops/posts/trend${selectedAcc ? `?account_id=${encodeURIComponent(selectedAcc)}` : ""}`)
      .then(setTrend)
      .catch(() => {});
  };

  useEffect(() => {
    load();
  }, [c.version, c.ip]);

  return (
    <>
      <Notice>
        作品与数据指标对小红书等平台的缺失哨兵值（-1）已自动清洗为空值（显示为 “—”），绝不补零混淆真实数据；线上删除作品入口本期保持「暂不开放」。
      </Notice>

      <Card
        title={`账号运营快照 · ${stats.length}`}
        subtitle={
          trend?.daily?.length
            ? `近周期趋势序列：${trend.daily.length} 条分日记录`
            : "可选择上方账号查看近周期作品趋势"
        }
        action={
          <div className="button-row">
            <select aria-label="选择账号" value={selectedAcc} onChange={(e) => setSelectedAcc(e.target.value)}>
              <option value="">请选择账号操作同步</option>
              {stats.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.platform_name} · {a.nickname}
                </option>
              ))}
            </select>
            <button
              disabled={!selectedAcc}
              onClick={() =>
                c.run(async () => {
                  await post(`/ops/accounts/${selectedAcc}/stats/refresh`, {});
                  load();
                }, "已刷新账号统计")
              }
            >
              <RefreshCw size={15} />
              刷新账号统计
            </button>
            <button
              className="primary"
              disabled={!selectedAcc}
              onClick={() =>
                c.run(async () => {
                  await post(`/ops/accounts/${selectedAcc}/posts/sync`, {});
                  load();
                }, "已同步平台作品列表")
              }
            >
              同步账号作品
            </button>
          </div>
        }
      >
        {stats.length ? (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>账号</th>
                  <th>平台</th>
                  <th>粉丝总数</th>
                  <th>累计赞</th>
                  <th>更新日期</th>
                </tr>
              </thead>
              <tbody>
                {stats.map((a) => (
                  <tr key={a.id}>
                    <td>{a.nickname}</td>
                    <td>{a.platform_name}</td>
                    <td>{cell(a.stats?.fans_total)}</td>
                    <td>{cell(a.stats?.likes)}</td>
                    <td>{cell(a.stats?.observed_date)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty title="暂无账号统计" body="请先在「账号与授权」同步账号后刷新指标。" />
        )}
      </Card>

      <Card title={`已同步作品 · ${posts.length}`}>
        {posts.length ? (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>作品标题</th>
                  <th>平台 / 账号</th>
                  <th>播放</th>
                  <th>点赞</th>
                  <th>评论</th>
                  <th>完播率(%)</th>
                  <th>发布时间</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {posts.map((p) => (
                  <tr key={p.id}>
                    <td>
                      {p.url ? (
                        <a href={p.url} target="_blank" rel="noreferrer">
                          {p.title || p.vendor_post_id}
                        </a>
                      ) : (
                        p.title || p.vendor_post_id
                      )}
                    </td>
                    <td>
                      {p.platform_name} · {p.account_nickname}
                    </td>
                    <td>{cell(p.views)}</td>
                    <td>{cell(p.likes)}</td>
                    <td>{cell(p.comments)}</td>
                    <td>{p.full_view_rate != null ? Number(p.full_view_rate).toFixed(1) : "—"}</td>
                    <td>{p.post_time || "—"}</td>
                    <td>
                      <div className="button-row">
                        <button
                          className="text-button"
                          onClick={() =>
                            c.run(async () => {
                              const d = await api<Row>(`/ops/posts/${p.id}/daily`);
                              setDailyRows(d.list || []);
                              setDailyPost(p);
                            })
                          }
                        >
                          <BarChart2 size={14} />
                          分日明细
                        </button>
                        <button className="text-button" onClick={() => setVisibleTarget(p)}>
                          <EyeOff size={14} />
                          改仅自己可见
                        </button>
                        <button
                          className="text-button"
                          disabled
                          title="暂不开放，当前版本不支持线上删稿"
                        >
                          删除作品（暂不开放）
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty title="暂无已同步作品" body="选择上方账号并点击「同步账号作品」即可拉取平台作品与指标。" />
        )}
      </Card>

      {dailyPost && (
        <Modal title={`分日数据 · ${dailyPost.title || dailyPost.vendor_post_id}`} onClose={() => setDailyPost(null)}>
          {dailyRows.length ? (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>日期</th>
                    <th>播放</th>
                    <th>点赞</th>
                    <th>评论</th>
                    <th>转发</th>
                    <th>涨粉</th>
                  </tr>
                </thead>
                <tbody>
                  {dailyRows.map((d, i) => (
                    <tr key={d.date || i}>
                      <td>{d.date || d.observedDate || "—"}</td>
                      <td>{cell(d.view ?? d.views)}</td>
                      <td>{cell(d.like ?? d.likes)}</td>
                      <td>{cell(d.comment ?? d.comments)}</td>
                      <td>{cell(d.forward ?? d.forwards)}</td>
                      <td>{cell(d.fansAdd)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <Empty title="暂无分日数据" body="小V猫尚未返回该作品的分日增量明细。" />
          )}
        </Modal>
      )}

      {visibleTarget && (
        <Modal title={`修改作品可见性 · ${visibleTarget.title || visibleTarget.vendor_post_id}`} onClose={() => setVisibleTarget(null)}>
          <Notice>将向小V猫下发单条可见性调整任务，把该作品设为「仅自己可见」。</Notice>
          <div className="button-row">
            <button
              className="primary"
              onClick={() =>
                c.run(async () => {
                  await post(`/ops/posts/${visibleTarget.id}/visibility`, { visible: 1, confirm: true });
                  setVisibleTarget(null);
                  load();
                }, "已提交修改作品可见性任务")
              }
            >
              确认设为仅自己可见
            </button>
          </div>
        </Modal>
      )}
    </>
  );
}