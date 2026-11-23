import { useEffect, useState, useRef } from "react";
import { ArrowRight, Upload, Download } from "lucide-react";
import { api, saveText, type Row } from "../api";
import { Empty, Card, Modal, Notice, getError } from "../components/ui";
import { type Ctx, useRows, filterIp, IpSelect } from "../context";
export default function Reports(c: Ctx) {
  const [report, setReport] = useState<Row | null>(null),
    [target, setTarget] = useState(c.ip),
    [platform, setPlatform] = useState("抖音"),
    [result, setResult] = useState<Row | null>(null),
    [previews, setPreviews] = useState<Row[]>([]);
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => {
    api("/reports?ip_id=" + encodeURIComponent(c.ip))
      .then(setReport)
      .catch((e) => c.notify(getError(e)));
  }, [c.version, c.ip]);
  const metrics = filterIp(useRows("/records/metrics", c.version), c.ip);
  async function upload(files: FileList | null) {
    if (!target) {
      c.notify("请选择导入所属IP");
      return;
    }
    if (!files) return;
    const pending: Row[] = [];
    for (const file of Array.from(files)) {
      const form = new FormData();
      form.append("ip_id", target);
      form.append("platform", platform);
      form.append("file", file);
      form.append("preview", "true");
      const r = await c.run(
        () => api("/metrics/import", { method: "POST", body: form }),
        undefined,
      );
      if (r) pending.push({ file, ip_id: target, platform, report: r });
    }
    setPreviews(pending);
    if (ref.current) ref.current.value = "";
  }
  async function confirmImport() {
    const results: Row[] = [];
    for (const pending of previews) {
      const form = new FormData();
      form.append("ip_id", pending.ip_id);
      form.append("platform", pending.platform);
      form.append("file", pending.file);
      const result = await c.run(
        () => api("/metrics/import", { method: "POST", body: form }),
        "报表已确认导入",
      );
      if (result) results.push(result);
    }
    if (results.length === previews.length) {
      setPreviews([]);
      setResult({
        rows: results.reduce((n, r) => n + r.rows, 0),
        added: results.reduce((n, r) => n + r.added, 0),
        errors: results.flatMap((r) => r.errors),
      });
    }
  }
  return (
    <>
      <Card
        title="上传平台数据"
        subtitle="按IP和平台导入CSV / Excel，重复快照会更新而不会重复累计"
      >
        <div className="button-row">
          <IpSelect ips={c.ips} value={target} onChange={setTarget} />
          <select
            aria-label="数据平台"
            value={platform}
            onChange={(e) => setPlatform(e.target.value)}
          >
            {["抖音", "视频号", "小红书"].map((p) => (
              <option key={p}>{p}</option>
            ))}
          </select>
          <button className="primary" onClick={() => ref.current?.click()}>
            <Upload size={16} />
            选择报表
          </button>
          <button
            onClick={() =>
              saveText(
                "指标导入模板.csv",
                "date,work_id,views,leads,likes\n2026-11-16,作品ID,100,2,10\n",
              )
            }
          >
            下载列模板
          </button>
        </div>
        <input
          ref={ref}
          hidden
          multiple
          type="file"
          accept=".csv,.xlsx"
          onChange={(e) => upload(e.target.files)}
        />
        <p className="muted">
          必需列：date、work_id；可选：views、leads、likes。支持对应中文列名，指标为空则保留缺失。
        </p>
        {result && (
          <Notice>
            已处理{result.rows}行，新增{result.added}条；异常
            {result.errors.length}条。
            {result.errors
              .map((e: Row) => `第${e.line}行：${e.reason}`)
              .join("；")}
          </Notice>
        )}
      </Card>
      {previews.length > 0 && (
        <Modal title="确认指标导入" onClose={() => setPreviews([])}>
          <p className="muted">
            检查目标IP、平台和列解析，确认后写入数据库；异常行会跳过。
          </p>
          {previews.map((pending, i) => (
            <div key={i}>
              <h3>
                {pending.file.name} /{" "}
                {c.ips.find((ip) => ip.id === pending.ip_id)?.name} /{" "}
                {pending.platform}
              </h3>
              <Notice>
                有效{pending.report.valid_rows}行；异常
                {pending.report.errors.length}行。
                {pending.report.errors
                  .map((r: Row) => `第${r.line}行：${r.reason}`)
                  .join("；")}
              </Notice>
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>日期</th>
                      <th>作品ID</th>
                      <th>播放</th>
                      <th>线索</th>
                    </tr>
                  </thead>
                  <tbody>
                    {pending.report.items
                      .slice(0, 20)
                      .map((r: Row, n: number) => (
                        <tr key={n}>
                          <td>{r.date}</td>
                          <td>{r.work_id}</td>
                          <td>{r.views ?? "缺失"}</td>
                          <td>{r.leads ?? "缺失"}</td>
                        </tr>
                      ))}
                  </tbody>
                </table>
              </div>
            </div>
          ))}
          <div className="modal-actions">
            <button onClick={() => setPreviews([])}>取消</button>
            <button className="primary" onClick={confirmImport}>
              确认写入指标
            </button>
          </div>
        </Modal>
      )}
      {metrics.length && report ? (
        <>
          <div className="stats-grid">
            <div className="card stat">
              <div>
                <span>当前范围 · 平台作品</span>
                <strong>{report.works}</strong>
              </div>
            </div>
            <div className="card stat">
              <div>
                <span>末次累计播放（可用数据）</span>
                <strong>{report.views}</strong>
              </div>
            </div>
            <div className="card stat">
              <div>
                <span>平台记录线索（未核身份）</span>
                <strong>{report.leads}</strong>
              </div>
            </div>
            <div className="card stat">
              <div>
                <span>缺失播放指标的作品</span>
                <strong>{report.missing_views}</strong>
              </div>
            </div>
          </div>
          <Card title="经营复盘" subtitle="摘要和原始快照均遵循顶部IP筛选。">
            {report.sections.map((s: Row) => (
              <div className="report-section" key={s.title}>
                <h3>{s.title}</h3>
                <p>{s.body}</p>
              </div>
            ))}
            <button
              onClick={() =>
                saveText(
                  "经营复盘.md",
                  report.sections
                    .map((s: Row) => `## ${s.title}\n\n${s.body}`)
                    .join("\n\n"),
                )
              }
            >
              <Download size={16} />
              导出复盘
            </button>
          </Card>
          <Card title="导入快照">
            <table>
              <thead>
                <tr>
                  <th>日期</th>
                  <th>平台 / 作品</th>
                  <th>累计播放</th>
                  <th>线索数</th>
                </tr>
              </thead>
              <tbody>
                {metrics.map((r) => (
                  <tr key={r.id}>
                    <td>{r.date}</td>
                    <td>
                      {r.platform} / {r.work_id}
                    </td>
                    <td>{r.views ?? "缺失"}</td>
                    <td>{r.leads ?? "缺失"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        </>
      ) : (
        <Card>
          <Empty
            title="还没有平台数据"
            body="选择IP、平台，再导入第一份报表；有数据后展示经营复盘。"
          />
        </Card>
      )}
      <Card title="写手复盘" subtitle="独立于播放指标，基于稿件版本与人工改动">
        <p className="muted">
          在IP与写手中登记规律和证据，经人工采纳后进入写作上下文。当前尚未自动生成改稿率与学习周报。
        </p>
        <button onClick={() => c.go("writers")}>
          查看学习规则 <ArrowRight size={15} />
        </button>
      </Card>
    </>
  );
}
