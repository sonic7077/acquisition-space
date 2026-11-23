import { useEffect, useState } from "react";
import {
  FileCheck,
  Clapperboard,
  MessageSquare,
  BookOpen,
  BrainCircuit,
  ArrowRight,
  Sparkles,
} from "lucide-react";
import { api, saveText, type Row } from "../api";
import { Badge, Empty, Card, Notice, fmt, getError } from "../components/ui";
import { type Ctx, useRows, filterIp } from "../context";
export default function Dashboard(c: Ctx) {
  const [data, setData] = useState<Row>({});
  useEffect(() => {
    api("/dashboard?ip_id=" + encodeURIComponent(c.ip))
      .then(setData)
      .catch((e) => c.notify(getError(e)));
  }, [c.version, c.ip]);
  const jobs = filterIp(useRows("/jobs", c.version), c.ip);
  return (
    <>
      <div className="hero">
        <div>
          <span className="hero-tag">
            <Sparkles size={14} /> 内容增长，从这里开始
          </span>
          <h2>
            沉淀一次知识，
            <br />
            持续创造有价值的内容。
          </h2>
          <p>先建立IP与知识库，再让写手理解你的产品、客户和表达。</p>
          <button className="primary" onClick={() => c.go("knowledge")}>
            建立知识资产 <ArrowRight size={16} />
          </button>
        </div>
        <div className="hero-graphic">
          <div className="orb orb-a" />
          <div className="orb orb-b" />
          <div className="graphic-card">
            <BookOpen size={25} />
            <span>知识沉淀</span>
            <small>你的真实经验与专业</small>
          </div>
          <div className="graphic-card second">
            <BrainCircuit size={25} />
            <span>专属表达</span>
            <small>让写手越来越懂你</small>
          </div>
          <div className="graphic-line" />
        </div>
      </div>
      <div className="stats-grid">
        {[
          ["待审核稿件", data.pending || 0, FileCheck, "drafts"],
          ["待拍摄稿件", data.shooting || 0, Clapperboard, "assets"],
          ["知识资产", data.knowledge || 0, BookOpen, "knowledge"],
          ["客户线索", data.leads || 0, MessageSquare, "leads"],
        ].map(([label, n, Icon, p]: any) => (
          <button key={p} className="stat card" onClick={() => c.go(p)}>
            <div>
              <span>{label}</span>
              <strong>
                {n}
                <small>条</small>
              </strong>
            </div>
            <span className="stat-icon">
              <Icon size={22} />
            </span>
          </button>
        ))}
      </div>
      <div className="two-columns">
        <Card title="内容生产路径" subtitle="从选题到发表，让每一步都有据可循">
          {[
            [
              "01",
              "发现值得做的选题",
              "关键词、对标素材与客户原话",
              "materials",
            ],
            ["02", "用自己的知识写内容", "按IP和文体装配写手上下文", "writers"],
            [
              "03",
              "审核、拍摄与发表",
              "版本批准，明确资产与发表凭证",
              "drafts",
            ],
          ].map(([n, t, s, p]) => (
            <button className="path-step" onClick={() => c.go(p)} key={n}>
              <span>{n}</span>
              <div>
                <strong>{t}</strong>
                <p>{s}</p>
              </div>
              <ArrowRight size={17} />
            </button>
          ))}
        </Card>
        <Card title="工作空间状态" subtitle="区分本地运行与外部真实接入">
          <div className="status-row">
            <span>本地数据库</span>
            <Badge value="已连接" />
          </div>
          <div className="status-row">
            <span>写作执行器最近心跳</span>
            <small>{fmt(data.worker)}</small>
          </div>
          <div className="status-row">
            <span>外部集成</span>
            <Badge value="待管理员配置并验证" />
          </div>
          <Notice>未配置模型时，可以导出提示包并手工导入稿件。</Notice>
        </Card>
      </div>
      <Card title="写作任务" subtitle="持久保存任务进度；失败不会伪造生成结果">
        {jobs.length ? (
          <table>
            <thead>
              <tr>
                <th>任务</th>
                <th>状态</th>
                <th>更新时间</th>
                <th>结果</th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((j) => (
                <tr key={j.id}>
                  <td>{j.id.slice(0, 8)}</td>
                  <td>
                    <Badge value={j.status} />
                  </td>
                  <td>{fmt(j.updated_at)}</td>
                  <td>
                    {j.error ||
                      (j.draft_id && "稿件已进入审稿") ||
                      (j.analysis_id && "辅助分析已保存到IP与写手") ||
                      "等待执行器处理"}
                    {j.raw_output&&<details><summary>查看模型原始结果</summary><pre className="writer-history">{j.raw_output}</pre><button onClick={()=>saveText("模型原始结果.txt",j.raw_output)}>下载原始结果</button></details>}
                    {j.draft_id&&<button className="text-button" onClick={()=>c.go("drafts")}>去审稿</button>}
                    {j.analysis_id&&<button className="text-button" onClick={()=>c.go("writers")}>查看分析</button>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <Empty
            title="暂时没有写作任务"
            body="在IP与写手页面提交内容主题，或先导出写作上下文。"
          />
        )}
      </Card>
    </>
  );
}
