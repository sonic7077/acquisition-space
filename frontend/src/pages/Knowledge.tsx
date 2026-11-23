import { useState, useRef, useEffect } from "react";
import { Search, Upload, FolderOpen } from "lucide-react";
import { api, post, patch, type Row } from "../api";
import { Badge, Empty, Card, Modal, Field, Notice } from "../components/ui";
import { type Ctx, useRows, verifiedKnowledge } from "../context";
import KnowledgeDirectories, { CategoryOptions, type KnowledgeCategory, type CategoryInfo } from "./KnowledgeDirectories";
export default function Knowledge(c: Ctx) {
  const rows = useRows("/knowledge", c.version),
    [tree,setTree] = useState<KnowledgeCategory[]>([]),
    [categories,setCategories] = useState<CategoryInfo[]>([]),
    [selected,setSelected] = useState("all"),
    [category,setCategory] = useState(""),
    [q, setQ] = useState(""),
    [status, setStatus] = useState("all"),
    [batch, setBatch] = useState<Row | null>(null),
    [doc, setDoc] = useState<Row | null>(null),
    [busy, setBusy] = useState(false);
  const normal = useRef<HTMLInputElement>(null),
    directory = useRef<HTMLInputElement>(null);
  useEffect(()=>{api<Row>('/knowledge/categories').then(d=>{setTree(d.tree);setCategories(d.categories)}).catch(()=>c.notify('知识库目录加载失败，请刷新重试'));},[]);
  function selectDirectory(id:string) {
    setSelected(id);
    setCategory(categories.find(c=>c.id===id)?.leaf?id:'');
  }
  const selectedPath=categories.find(c=>c.id===selected)?.path;
  async function preview(files: FileList | null) {
    if (!files?.length) return;
    if (!categories.some(c=>c.id===category&&c.leaf)) {c.notify("请先选择对应的末级目录");return;}
    setBusy(true);
    const form = new FormData();
    form.append("category_id",category);
    Array.from(files).forEach((f) =>
      form.append("files", f, f.webkitRelativePath || f.name),
    );
    const result = await c.run(() =>
      api("/knowledge/preview", { method: "POST", body: form }),
    );
    if (result) setBatch(result);
    setBusy(false);
    if (normal.current) normal.current.value = "";
    if (directory.current) directory.current.value = "";
  }
  const shown = rows.filter(
    (r) =>
      (status === "all" || r.status === status) &&
      (selected === "all" || selected === "unclassified" && !r.category_id || selectedPath && (r.category_path === selectedPath || r.category_path?.startsWith(selectedPath+"/"))) &&
      (r.title + r.path + (r.category_path||"")).includes(q),
  );
  return (
    <>
      <div className="mini-stats">
        <span>
          <strong>{rows.length}</strong> 全部资料
        </span>
        <span>
          <strong>{verifiedKnowledge(rows).length}</strong>{" "}
          已核验可引用
        </span>
        <span>
          <strong>{rows.filter((r) => r.status === "pending").length}</strong>{" "}
          待核实
        </span>
        <span>
          <strong>{rows.filter((r) => r.status === "template").length}</strong>{" "}
          模板资料
        </span>
      </div>
      <div className="kb-layout"><KnowledgeDirectories tree={tree} categories={categories} rows={rows} selected={selected} onSelect={selectDirectory}/><div className="kb-content">
      <Card
        title="接入已有知识库"
        subtitle="选择共享知识库的末级目录，再上传文件、文件夹或ZIP。"
      >
        <div className="import-layout">
          <div className="upload-info">
            <span className="feature-icon">
              <FolderOpen size={26} />
            </span>
            <div>
              <h3>让写手读懂你的业务积累</h3>
              <p>
                支持 Markdown、TXT、PDF、Word、CSV、JSON。
                <br />
                单文件20MB，每批200个文件、解压后80MB。扫描PDF请先OCR。
              </p>
            </div>
          </div>
          <div className="upload-actions">
            <Field label="导入末级目录"><select aria-label="导入末级目录" required value={category} onChange={e=>{setCategory(e.target.value);setSelected(e.target.value||'all')}}><CategoryOptions categories={categories}/></select></Field>
            <div className="button-row">
              <button disabled={busy||!category} onClick={() => normal.current?.click()}>
                <Upload size={16} />
                上传文件 / ZIP
              </button>
              <button
                disabled={busy||!category}
                className="primary"
                onClick={() => directory.current?.click()}
              >
                <FolderOpen size={16} />
                上传知识库目录
              </button>
            </div>
          </div>
        </div>
        <input
          hidden
          multiple
          ref={normal}
          type="file"
          accept=".md,.txt,.pdf,.docx,.csv,.json,.zip"
          onChange={(e) => preview(e.target.files)}
        />
        <input
          hidden
          multiple
          ref={directory}
          type="file"
          {...({ webkitdirectory: "", directory: "" } as any)}
          onChange={(e) => preview(e.target.files)}
        />
        {busy && <p className="loading-line">正在提取文件并生成导入预览…</p>}
        <Notice>
          每批文件归入同一个末级目录。写手根据文体和任务从共享库检索相关的已核验、未过期资料。
        </Notice>
      </Card>
      <Card
        title="知识资产"
        subtitle={`${selectedPath|| (selected==='unclassified'?'待归类历史资料':'全部目录')} · 当前筛选 ${shown.length} 条`}
        action={
          <div className="search-box">
            <Search size={16} />
            <input
              aria-label="搜索知识"
              placeholder="搜索标题或目录"
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
          </div>
        }
      >
        <div className="tabs">
          {[
            ["all", "全部"],
            ["verified", "已核验"],
            ["pending", "待核验"],
            ["template", "模板"],
            ["archived", "归档"],
          ].map(([k, n]) => (
            <button
              className={status === k ? "active" : ""}
              onClick={() => setStatus(k)}
              key={k}
            >
              {n}
            </button>
          ))}
        </div>
        {shown.length ? (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>知识条目 / 原始路径</th>
                  <th>状态</th>
                  <th>字符数</th>
                  <th>有效期</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((r) => (
                  <tr key={r.id}>
                    <td>
                      <strong className="table-title">{r.title}</strong>
                      <small className="kb-category-path">归档：{r.category_path||"待归类"}</small>
                      <small>原始路径：{r.path}</small>
                    </td>
                    <td>
                      <Badge value={r.status} />
                    </td>
                    <td>{r.characters}</td>
                    <td>{r.valid_until || "长期（需定期复核）"}</td>
                    <td>
                      <button
                        className="text-button"
                        onClick={() =>
                          c.run(async () => {
                            const d = await api("/knowledge/" + r.id);
                            setDoc({
                              ...d,
                              _savedText: d.text,
                              _savedValid: d.valid_until || null,
                            });
                          })
                        }
                      >
                        查看内容
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty
            title="这里还没有知识资产"
            body="上传你的现有知识库，预览后确认；或切换筛选条件。"
          />
        )}
      </Card>
      </div></div>
      {batch && (
        <Modal title="确认知识库导入" onClose={() => setBatch(null)}>
          <p className="muted">
            导入位置：{batch.category_path}<br/>
            共 {batch.items.length}{" "}
            个文件。同一末级目录中相同路径和内容的文件只保留一份。
          </p>
          {batch.ignored?.length > 0 && (
            <Notice>
              已跳过{batch.ignored.length}个非知识文件或隐藏元数据：
              {batch.ignored.map((f: Row) => f.path).join("、")}
            </Notice>
          )}
          <div className="preview-list">
            {batch.items.map((r: Row, i: number) => (
              <div key={i}>
                <strong>{r.path}</strong>
                <Badge value={r.duplicate ? "已存在，将跳过" : r.status} />
                <p>{r.preview}</p>
              </div>
            ))}
          </div>
          <div className="modal-actions">
            <button onClick={() => setBatch(null)}>取消</button>
            <button
              className="primary"
              onClick={() =>
                c.run(async () => {
                  await post("/knowledge/confirm", { batch_id: batch.id });
                  setBatch(null);
                }, "知识库导入完成")
              }
            >
              确认导入
            </button>
          </div>
        </Modal>
      )}
      {doc && (
        <Modal title={doc.title} onClose={() => setDoc(null)}>
          <p className="muted">归档目录：{doc.category_path||'待归类'}<br/>原始路径：{doc.path}</p>
          {c.me.role !== 'member' && <div className="kb-reclassify"><Field label="调整归档末级目录"><select aria-label="调整归档末级目录" value={doc._categoryChoice??doc.category_id??''} onChange={e=>setDoc({...doc,_categoryChoice:e.target.value})}><CategoryOptions categories={categories}/></select></Field><button disabled={!doc._categoryChoice||doc._categoryChoice===doc.category_id} onClick={()=>c.run(async()=>{const d=await patch('/knowledge/'+doc.id,{category_id:doc._categoryChoice});setDoc({...doc,category_id:d.category_id,category_path:d.category_path,_categoryChoice:d.category_id});},'归档目录已更新；内容及核验状态保留')}>更新归档目录</button></div>}
          <textarea
            className="document-text"
            readOnly={c.me.role === "member"}
            value={doc.text}
            onChange={(e) => setDoc({ ...doc, text: e.target.value })}
          />
          {c.me.role !== "member" && (
            <>
              <Field label="资料有效期（可选）">
                <input
                  type="date"
                  value={doc.valid_until || ""}
                  onChange={(e) =>
                    setDoc({ ...doc, valid_until: e.target.value })
                  }
                />
              </Field>
              <button
                onClick={() =>
                  c.run(async () => {
                    const d = await patch("/knowledge/" + doc.id, {
                      text: doc.text,
                      valid_until: doc.valid_until || null,
                    });
                    setDoc({
                      ...d,
                      _savedText: d.text,
                      _savedValid: d.valid_until || null,
                    });
                  }, "修订已保存为待核验；受影响稿件将重新审核")
                }
              >
                保存修订，重新核验
              </button>
            </>
          )}
          <div className="button-row">
            <Badge value={doc.status} />
            {c.me.role !== "member" && doc.status !== "template" && (
              <>
                <button
                  className="primary"
                  disabled={
                    doc.text !== doc._savedText ||
                    (doc.valid_until || null) !== doc._savedValid
                  }
                  onClick={() =>
                    c.run(async () => {
                      await patch("/knowledge/" + doc.id, {
                        status: "verified",
                      });
                      setDoc(null);
                    }, "资料已核验，可供写手引用")
                  }
                >
                  核验通过
                </button>
                <button
                  onClick={() =>
                    c.run(async () => {
                      await patch("/knowledge/" + doc.id, {
                        status: "archived",
                      });
                      setDoc(null);
                    }, "已归档")
                  }
                >
                  归档
                </button>
              </>
            )}
          </div>
          <Notice>
            核验前请确认内容真实、适用范围和有效期。模板资料不能直接核验为业务事实。
          </Notice>
        </Modal>
      )}
    </>
  );
}
