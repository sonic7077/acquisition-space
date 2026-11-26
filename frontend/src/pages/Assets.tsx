import { useState, useRef } from "react";
import { Upload } from "lucide-react";
import { uploadAsset, type Row } from "../api";
import { Empty, Card, Field, Notice, getError } from "../components/ui";
import { type Ctx, useRows, filterIp } from "../context";
import Teleprompter from "./Teleprompter";

type UploadItem = { name: string; percent: number; status: string; error?: string };

export default function Assets(c: Ctx) {
  const drafts = filterIp(useRows("/drafts", c.version), c.ip).filter(
      (d) => d.approved_version === d.version,
    ),
    assets = filterIp(useRows("/assets", c.version), c.ip),
    [target, setTarget] = useState(""),
    [tele, setTele] = useState<Row | null>(null),
    [uploading, setUploading] = useState(false),
    [uploads, setUploads] = useState<UploadItem[]>([]);
  const input = useRef<HTMLInputElement>(null);
  const selectedDraft = drafts.find((draft) => draft.id === target);
  async function upload(files: FileList | null) {
    if (!selectedDraft || !files?.length || uploading) return;
    const batch = Array.from(files);
    const draftId = selectedDraft.id;
    setUploading(true);
    setUploads(batch.map(({ name }) => ({ name, percent: 0, status: "等待上传" })));
    function update(index: number, patch: Partial<UploadItem>) {
      setUploads((items) => items.map((item, i) => i === index ? { ...item, ...patch } : item));
    }
    try {
      for (const [index, file] of batch.entries()) {
        const form = new FormData();
        form.append("draft_id", draftId);
        form.append("file", file);
        update(index, { status: "上传中" });
        await c.run(async () => {
          try {
            const asset = await uploadAsset(
              form,
              (percent) => update(index, { percent }),
              () => update(index, { percent: 100, status: "服务器校验中" }),
            );
            update(index, { percent: 100, status: "已关联" });
            return asset;
          } catch (error) {
            update(index, { status: "上传失败", error: getError(error) });
            throw error;
          }
        });
      }
    } finally {
      if (input.current) input.current.value = "";
      setUploading(false);
    }
  }
  return (
    <>
      <div className="two-columns">
        <Card
          title={`待拍摄 / 已批准 · ${drafts.length}`}
          subtitle="提词和资产均使用当前批准版本"
        >
          {drafts.length ? (
            drafts.map((d) => (
              <div className="shooting-row" key={d.id}>
                <div>
                  <strong>{d.title}</strong>
                  <small>
                    {d.id.slice(0, 8)} · v{d.version}
                  </small>
                </div>
                <div className="button-row">
                  <button onClick={() => setTele(d)}>提词器</button>
                  <button
                    className={target === d.id ? "primary" : ""}
                    disabled={uploading}
                    onClick={() => setTarget(d.id)}
                  >
                    选择上传
                  </button>
                </div>
              </div>
            ))
          ) : (
            <Empty
              title="暂无已批准稿件"
              body="当前 IP 范围内的稿件需由团队长或管理员批准后才能上传。"
            >
              <button onClick={() => c.go("drafts")}>前往审稿</button>
            </Empty>
          )}
        </Card>
        <Card
          title="视频与封面投递箱"
          subtitle="选择已批准稿件，上传的文件将关联到该 IP 和批准版本"
        >
          <Field label="明确关联稿件">
            <select value={selectedDraft?.id ?? ""} disabled={!drafts.length || uploading} onChange={(e) => setTarget(e.target.value)}>
              <option value="">{drafts.length ? "请选择已批准稿件" : "暂无可选稿件"}</option>
              {drafts.map((d) => (
                <option key={d.id} value={d.id}>
                  {c.ips.find((i) => i.id === d.ip_id)?.name} / {d.title} / v
                  {d.version}
                </option>
              ))}
            </select>
          </Field>
          {!selectedDraft && <p className="muted" id="upload-requirement">
            {drafts.length ? "选择稿件后即可上传视频。" : "稿件批准后，即可在这里选择并上传。"}
          </p>}
          <button className="dropzone" disabled={!selectedDraft || uploading} aria-describedby={selectedDraft ? undefined : "upload-requirement"} onClick={() => input.current?.click()}>
            <Upload size={32} />
            <strong>选择视频与封面</strong>
            <span>视频 MP4 / MOV · 封面 PNG / JPG / JPEG · 单文件 ≤ 100 MB</span>
          </button>
          <input
            hidden
            multiple
            ref={input}
            disabled={!selectedDraft || uploading}
            type="file"
            accept=".mp4,.mov,.png,.jpg,.jpeg"
            onChange={(e) => upload(e.target.files)}
          />
          {uploads.length > 0 && <ul className="asset-uploads" aria-label="本批文件上传进度">
            {uploads.map((item, index) => (
              <li key={index}>
                <div><strong>{item.name}</strong><span>{item.percent}%</span></div>
                <progress value={item.percent} max={100} aria-label={item.name + " 上传进度"} />
                <small role="status">{item.status}</small>
                {item.error && <p className="error-text">{item.error}</p>}
              </li>
            ))}
          </ul>}
          <Notice>
            视频可以单独上传，封面可稍后补传。上传后可在「已关联媒体资产」下载核对，再前往发布排期。
          </Notice>
        </Card>
      </div>
      <Card title="已关联媒体资产">
        {assets.length ? (
          <table>
            <thead>
              <tr>
                <th>文件</th>
                <th>类型</th>
                <th>稿件版本</th>
                <th>大小</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {assets.map((a) => (
                <tr key={a.id}>
                  <td>{a.filename}</td>
                  <td>{a.type === "video" ? "视频" : "封面"}{a.source_type === "ai_generated" && <small>AI生成 · 已人工核对</small>}</td>
                  <td>
                    {a.draft_id.slice(0, 8)} · v{a.draft_version}
                  </td>
                  <td>{(a.bytes / 1024 / 1024).toFixed(2)}MB</td>
                  <td>
                    <a href={`/api/assets/${a.id}/download`}>下载核对</a>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <Empty title="还没有上传资产" body="上传前先确认目标稿件。" />
        )}
      </Card>
      {tele && <Teleprompter draft={tele} onClose={() => setTele(null)} />}
    </>
  );
}
