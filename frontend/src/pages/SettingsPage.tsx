import { useEffect, useState, type FormEvent } from "react";
import { ChevronRight } from "lucide-react";
import { api, post, type Row } from "../api";
import { Card, Field, Notice, getError } from "../components/ui";
import { type Ctx } from "../context";
const CONFIG: Record<
  string,
  { name: string; desc: string; fields: [string, string, string?][] }
> = {
  model: {
    name: "AI模型",
    desc: "兼容Chat Completions接口；密钥仅在服务端使用。",
    fields: [
      ["base_url", "服务地址（包含 /v1）"],
      ["model", "模型名称"],
      ["api_key", "API Key", "password"],
    ],
  },
  feishu: {
    name: "飞书",
    desc: "使用你指定的现有多维表格，不复制课程模板。",
    fields: [
      ["auth_mode", "授权方式", "auth_mode"],
      ["url", "多维表格 / 知识空间链接"],
      ["app_id", "应用 App ID"],
      ["app_secret", "App Secret", "password"],
      ["authors_table_id", "博主表ID（表名为博主时可留空）"],
      ["works_table_id", "作品表ID（表名为作品时可留空）"],
      ["comments_table_id", "评论表ID（表名为评论时可留空）"],
      ["field_mapping", "字段映射 JSON（可选）", "textarea"],
    ],
  },
  collection: {
    name: "TikHub采集",
    desc: "接入 api.tikhub.io 抖音、小红书、视频号关键词搜索与评论；预算按请求次数计，失败请求也计入。",
    fields: [
      ["base_url", "TikHub服务地址"],
      ["api_key", "API Key", "password"],
      ["daily_budget", "每日调用预算", "number"],
    ],
  },
  transcription: {
    name: "语音转写",
    desc: "选题库优先使用本机 Whisper 转写视频语音。也可配置兼容 /audio/transcriptions 的备用服务。",
    fields: [
      ["base_url", "服务地址"],
      ["model", "转写模型名称"],
      ["api_key", "API Key", "password"],
    ],
  },
  wechat: {
    name: "微信生态",
    desc: "登记公众号与企微连接；当前采用手工发表凭证。",
    fields: [
      ["app_id", "公众号 App ID"],
      ["app_secret", "App Secret", "password"],
      ["webhook", "企微 Webhook", "password"],
    ],
  },
  executor: {
    name: "发布执行器",
    desc: "登记独立执行端；当前尚未实现Windows RPA联调。",
    fields: [
      ["base_url", "执行器地址"],
      ["token", "设备令牌", "password"],
    ],
  },
  discovery: {
    name: "作品推荐规则",
    desc: "透明的运营参考阈值；修改不调用外部平台，也不等于爆款或成交认定。",
    fields: [
      ["denom_floor", "倍数分母下限", "number"],
      ["min_mult", "推荐最低倍数", "number"],
      ["min_likes_candidate", "倍数推荐最低点赞", "number"],
      ["likes_high", "高赞阈值（大于）", "number"],
      ["likes_mid", "普通点赞阈值（大于）", "number"],
      ["low_fan_min_likes", "低粉高赞最低点赞（大于）", "number"],
      ["low_fan_ratio", "低粉高赞比例（大于）", "number"],
      ["traffic_high", "高评赞比（0—1）", "number"],
      ["traffic_mid", "中评赞比（0—1）", "number"],
      ["min_comments", "评赞比最低评论数", "number"],
      ["rise_delta", "起量最低涨赞", "number"],
      ["rise_new_likes", "新作起量最低点赞", "number"],
      ["rise_mult", "新作起量最低倍数", "number"],
      ["rise_hours", "新作起量小时数", "number"],
      ["stale_days", "推荐保鲜天数", "number"],
    ],
  },
  rules: {
    name: "审稿规则",
    desc: "来源重合检查独立于人工改稿指标。",
    fields: [
      ["overlap_min", "最短连续匹配字符数", "number"],
      ["overlap_max", "最大重合占比（0—1）", "number"],
    ],
  },
};
export default function SettingsPage(c: Ctx) {
  const [settings, setSettings] = useState<Row | null>(null),
    [tab, setTab] = useState("model"),
    [busy, setBusy] = useState(false),
    [schema,setSchema] = useState<Row|null>(null),
    [syncPreview,setSyncPreview] = useState<Row|null>(null),
    [librarySchema,setLibrarySchema] = useState<Row|null>(null);
  useEffect(() => {
    api("/settings")
      .then(setSettings)
      .catch((e) => c.notify(getError(e)));
  }, [c.version]);
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setBusy(true);
    const form = e.currentTarget;
    const f = new FormData(form),
      data: Row = {};
    CONFIG[tab].fields.forEach(([key, , type]) => {
      const v = f.get(key) as string;
      if (type === "password") {
        if (v) data[key] = v;
      } else data[key] = type === "number" ? Number(v) : v;
    });
    if (["model","collection","feishu"].includes(tab)) data.enabled = f.get("enabled") === "on";
    const result = await c.run(async () => {
      const s = await api("/settings", {
        method: "PUT",
        body: JSON.stringify({ [tab]: data }),
      });
      setSettings(s);
      return s;
    }, "配置已保存；保存不代表外部连接验证成功");
    if (result) {form.reset();setSchema(null);setSyncPreview(null);setLibrarySchema(null);}
    setBusy(false);
  }
  return (
    <>
      <Notice>
        仅管理员可读取和修改本页。API
        Key和访问令牌加密保存，页面只显示是否已配置；留空保留原密钥。
      </Notice>
      <div className="settings-layout">
        <div className="settings-nav">
          {Object.entries(CONFIG).map(([k, v]) => (
            <button
              key={k}
              className={tab === k ? "active" : ""}
              onClick={() => setTab(k)}
            >
              {v.name}
              <ChevronRight size={14} />
            </button>
          ))}
        </div>
        <Card title={CONFIG[tab].name} subtitle={CONFIG[tab].desc}>
          {settings ? (
            <form
              key={tab + JSON.stringify(settings[tab])}
              onSubmit={submit}
              className="settings-form"
            >
              {CONFIG[tab].fields.map(([key, label, type]) => (
                <Field
                  key={key}
                  label={
                    label +
                    (type === "password"
                      ? settings[tab]?.[key + "_configured"]
                        ? " · 已配置"
                        : " · 未配置"
                      : "")
                  }
                >
                  {type === "auth_mode" ? <select name={key} defaultValue={settings[tab]?.auth_mode||'app'}><option value="app">应用 App ID / App Secret</option><option value="cli">本机已授权飞书 CLI（无需复制密钥）</option></select> : type === "textarea" ? <textarea name={key} rows={5} defaultValue={settings[tab]?.[key] || ''} placeholder={'{"works":{"likes":"点赞数","author_id":"博主关联"}}'} /> : <input
                    name={key}
                    type={type || "text"}
                    step={tab === "discovery" ? "any" : key === "overlap_max" ? "0.01" : undefined}
                    autoComplete={type === "password" ? "new-password" : "off"}
                    defaultValue={
                      type === "password" ? "" : (settings[tab]?.[key] ?? (key === "base_url" && tab === "collection" ? "https://api.tikhub.io" : key === "daily_budget" ? 100 : ""))
                    }
                    readOnly={(tab === "collection" && key === "base_url")}
                    placeholder={
                      type === "password"
                        ? "留空保留原值，输入新值替换"
                        : undefined
                    }
                  />}
                </Field>
              ))}
              {tab === "model" && (
                <label className="check-row">
                  <input
                    name="enabled"
                    type="checkbox"
                    defaultChecked={settings.model?.enabled}
                  />
                  启用模型写作（提交任务后将调用此服务）
                </label>
              )}
              {['collection','feishu'].includes(tab) && <label className="check-row"><input name="enabled" type="checkbox" defaultChecked={settings[tab]?.enabled}/>{tab==='collection'?'启用TikHub采集':'启用飞书同步（采集完成后自动同步三表）'}</label>}
              <div className="button-row">
                <button className="primary" disabled={busy}>
                  保存设置
                </button>
                {CONFIG[tab].fields.some(
                  (f) =>
                    f[2] === "password" &&
                    settings[tab]?.[f[0] + "_configured"],
                ) && (
                  <button
                    type="button"
                    className="danger"
                    onClick={() =>
                      c.run(async () => {
                        const clear: Row = {};
                        CONFIG[tab].fields
                          .filter((f) => f[2] === "password")
                          .forEach((f) => (clear[f[0]] = null));
                        setSettings(
                          await api("/settings", {
                            method: "PUT",
                            body: JSON.stringify({ [tab]: clear }),
                          }),
                        );
                      }, "该分组凭证已清除")
                    }
                  >
                    清除该组凭证
                  </button>
                )}
              </div>
              <p className="muted">
                密钥不会回传到浏览器或记录在审计日志中。备份时请保留本机主密钥文件。
              </p>
            </form>
          ) : (
            <p>正在加载管理员设置…</p>
          )}
          {tab === "discovery" && <p className="muted">当前配置版本 v{settings?.discovery?.version||1}。样本不足或缺失数据时不计算对应指标；本人/停用账号不进入对标推荐。所有成员可在素材和使用说明中查看规则，只有管理员可修改。</p>}
          {tab === 'collection' && <div className="schema-preview"><a href="https://docs.tikhub.io/doc-4579297" target="_blank" rel="noreferrer">查看 TikHub 官方说明 ↗</a><p className="muted">当前接入抖音、小红书与视频号。搜索任务自动读取范围内作品评论；不会因错误自动反复请求。每日调用预算是本系统的请求上限，实际计费以服务商账单为准。</p></div>}
          {tab === 'feishu' && <div className="schema-preview"><p>当前目标：<a href={settings?.feishu?.url} target="_blank" rel="noreferrer">打开飞书模板 ↗</a></p><div className="button-row"><button disabled={busy} onClick={async()=>{setBusy(true);await c.run(async()=>setSyncPreview(await post('/integrations/feishu/preview',{all_scopes:true,include_library:true})));setBusy(false)}}>预览全部现有数据</button><button disabled={busy||!schema?.ready||!syncPreview||!settings?.feishu?.enabled} onClick={async()=>{setBusy(true);await c.run(()=>post('/integrations/feishu/sync',{all_scopes:true}),'已提交全部范围三表同步，请在选题库任务中查看结果');setBusy(false)}}>同步全部博主、作品和评论</button></div>{syncPreview&&<Notice>三表数据：博主 {syncPreview.counts.authors}、作品 {syncPreview.counts.works}、平台评论 {syncPreview.counts.comments} 条。{syncPreview.library_counts&&<>材料：知识 {syncPreview.library_counts.knowledge}、专属写手 {syncPreview.library_counts.writers}、系统写手 {syncPreview.library_counts.builtin_writers}、稿件 {syncPreview.library_counts.drafts}、IP账号 {syncPreview.library_counts.ips} 条。</>}{syncPreview.notice}</Notice>}<div className="button-row"><button disabled={busy} onClick={async()=>{setBusy(true);await c.run(async()=>{setLibrarySchema(await post('/integrations/feishu/library/prepare',{}));setSchema(await api('/integrations/feishu/schema'))},'材料同步表已检查；保留模板字段和公式');setBusy(false)}}>准备材料同步表</button><button disabled={busy||!schema?.ready||!librarySchema?.ready||!syncPreview||!settings?.feishu?.enabled} onClick={async()=>{setBusy(true);await c.run(()=>post('/integrations/feishu/sync',{all_scopes:true,include_library:true}),'已提交全部数据与材料同步，请在选题库任务中查看结果');setBusy(false)}}>同步全部数据与材料</button></div><p className="muted">材料同步将IP账号、稿件对应到原有表，并为知识库和写手SKILL补齐专表。包含8个系统写手，保留末级目录、问答、规则与版本；不设置发布、采集或审核触发开关。</p>{librarySchema?.warnings?.map((w:string)=><p className="error-text" key={w}>{w}</p>)}<div className="button-row"><button disabled={busy} onClick={async()=>{setBusy(true);await c.run(async()=>setSchema(await api('/integrations/feishu/schema')));setBusy(false)}}>读取三表与字段</button><button disabled={busy} onClick={async()=>{setBusy(true);await c.run(async()=>setSchema(await post('/integrations/feishu/prepare',{})),'已检查同步字段');setBusy(false)}}>检查并准备同步字段</button></div><p className="muted">先保存授权方式与目标链接，再读取字段。更换目标链接会清除原表ID和字段映射，请重新读取后配置。本机 CLI 使用现有登录凭证；应用模式使用下方密钥。准备操作会为三表补齐缺失的文本列“获客空间ID”“内容结构”“获客空间·IP”；保留已有字段和公式。请为应用开通多维表格读写权限，并授权访问此Base。</p><p className="muted">映射分组：authors（博主）、works（作品）、comments（评论）。字段键可用 title、body、likes、followers、source、author_id、material_id 等；值填写飞书字段名或字段ID。关系字段仅在类型与目标表匹配时写入。</p>{schema&&<><BadgeSchema ready={schema.ready}/>{schema.warnings?.map((w:string)=><p className="error-text" key={w}>{w}</p>)}{Object.entries(schema.tables||{}).map(([k,t]:[string,any])=><div key={k}><strong>{t.name} · {t.table_id}</strong><p className="muted">{t.fields.length} 个字段 · 已映射 {Object.keys(t.mapping).length} 项</p><pre>{Object.entries(t.mapping).map(([local,field]:[string,any])=>`${local} → ${field.field_name}（类型${field.type}）`).join('\n')}</pre></div>)}</>}</div>}
        </Card>
      </div>
    </>
  );
}

function BadgeSchema({ready}:{ready:boolean}) {return <p className={ready?"badge good":"badge"}>{ready?"三表结构可同步":"请完成目标表与字段配置"}</p>}
