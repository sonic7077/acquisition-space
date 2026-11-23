import { useState, type FormEvent } from "react";
import { Plus } from "lucide-react";
import { post, patch, type Row } from "../api";
import { Badge, Empty, Card, Modal, Field, fmt, ROLE } from "../components/ui";
import { type Ctx, useRows } from "../context";
export default function UsersPage(c: Ctx) {
  const users = useRows("/users", c.version),
    teams = useRows("/teams", c.version),
    audit = useRows("/audit", c.version),
    [modal, setModal] = useState(""),
    [target, setTarget] = useState<Row | null>(null),
    [team, setTeam] = useState(""),
    [role, setRole] = useState("member"),
    [selected, setSelected] = useState<string[]>([]);
  function openUser(u?: Row) {
    setTarget(u || null);
    setTeam(u?.team_id || "");
    setRole(u?.role || "member");
    setSelected(u?.ip_ids || []);
    setModal("user");
  }
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const d = Object.fromEntries(new FormData(e.currentTarget));
    await c.run(async () => {
      if (modal === "team") await post("/teams", d);
      else if (modal === "ip") await post("/ips", d);
      else {
        const body: Row = {
          ...d,
          role,
          team_id: team || null,
          ip_ids: selected,
        };
        if (target) {
          delete body.username;
          if (!body.password) delete body.password;
          await patch("/users/" + target.id, body);
        } else await post("/users", body);
      }
      setModal("");
    }, "保存成功");
  }
  return (
    <>
      <div className="three-columns">
        <Card title="管理员" subtitle="系统和组织管理">
          <p>配置模型与API Key，管理用户、团队和IP，执行全局业务操作。</p>
        </Card>
        <Card title="团队长" subtitle="团队业务管理">
          <p>访问本团队IP，核验知识、批准稿件、采纳写手规则。</p>
        </Card>
        <Card title="队员" subtitle="获授权IP内容制作">
          <p>上传知识、整理选题、写作、拍摄和记录发表，不能操作系统设置。</p>
        </Card>
      </div>
      <Card
        title={`团队账号 · ${users.length}`}
        action={
          <div className="button-row">
            <button onClick={() => setModal("team")}>创建团队</button>
            <button onClick={() => setModal("ip")}>创建IP</button>
            <button className="primary" onClick={() => openUser()}>
              <Plus size={16} />
              新增用户
            </button>
          </div>
        }
      >
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>用户</th>
                <th>角色</th>
                <th>团队</th>
                <th>授权IP</th>
                <th>状态 / 会话</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id}>
                  <td>
                    <strong>{u.name}</strong>
                    <small>
                      {u.username}
                      {u.id === c.me.id ? " · 我" : ""}
                    </small>
                  </td>
                  <td>{ROLE[u.role]}</td>
                  <td>
                    {teams.find((t) => t.id === u.team_id)?.name || "全局"}
                  </td>
                  <td>
                    {u.role === "admin"
                      ? "全部"
                      : u.role === "leader"
                        ? "本团队全部IP"
                        : c.ips
                            .filter((i) => u.ip_ids.includes(i.id))
                            .map((i) => i.name)
                            .join("、") || "未授权"}
                  </td>
                  <td>
                    <Badge value={u.active ? "正常" : "已停用"} /> ·{" "}
                    {u.sessions}个会话
                  </td>
                  <td>
                    <div className="button-row">
                      <button
                        className="text-button"
                        onClick={() => openUser(u)}
                      >
                        编辑
                      </button>
                      <button
                        className="text-button"
                        onClick={() =>
                          c.run(
                            () => post(`/users/${u.id}/revoke`, {}),
                            "登录会话已撤销",
                          )
                        }
                      >
                        撤销会话
                      </button>
                      <button
                        className="text-button danger"
                        onClick={() =>
                          c.run(
                            () =>
                              patch("/users/" + u.id, { active: !u.active }),
                            "账号状态已更新",
                          )
                        }
                      >
                        {u.active ? "停用" : "启用"}
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
      <Card title="团队与IP">
        <div className="team-list">
          {teams.map((t) => (
            <div key={t.id}>
              <strong>{t.name}</strong>
              <p>
                {c.ips
                  .filter((i) => i.team_id === t.id)
                  .map((i) => i.name)
                  .join(" / ") || "尚未创建IP"}
              </p>
            </div>
          ))}
          {!teams.length && (
            <Empty
              title="先建立第一个团队"
              body="创建团队和IP后，即可上传知识与制作内容。"
            />
          )}
        </div>
      </Card>
      <Card title="操作日志" subtitle="最近200条；系统设置日志不包含密钥值">
        <table>
          <thead>
            <tr>
              <th>时间</th>
              <th>操作人</th>
              <th>操作</th>
              <th>对象</th>
            </tr>
          </thead>
          <tbody>
            {audit.map((a) => (
              <tr key={a.id}>
                <td>{fmt(a.created_at)}</td>
                <td>{a.actor}</td>
                <td>{a.action}</td>
                <td>{a.object_id?.slice(0, 12) || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
      {modal && (
        <Modal
          title={
            modal === "team"
              ? "创建团队"
              : modal === "ip"
                ? "创建IP"
                : target
                  ? "编辑用户（保存后撤销其会话）"
                  : "新增用户"
          }
          onClose={() => setModal("")}
        >
          <form onSubmit={submit}>
            <Field label={modal === "user" ? "姓名" : "名称"}>
              <input name="name" required defaultValue={target?.name || ""} />
            </Field>
            {modal === "ip" && (
              <>
                <Field label="所属团队">
                  <select name="team_id" required>
                    <option value="">请选择团队</option>
                    {teams.map((t) => (
                      <option key={t.id} value={t.id}>
                        {t.name}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label="身份档案">
                  <textarea
                    name="profile"
                    rows={3}
                    placeholder="可稍后完善产品、受众和表达习惯"
                  />
                </Field>
              </>
            )}
            {modal === "user" && (
              <>
                <Field label="登录名">
                  <input
                    name="username"
                    required={!target}
                    disabled={!!target}
                    defaultValue={target?.username || ""}
                  />
                </Field>
                <Field
                  label={target ? "新密码（留空保留）" : "初始密码（至少10位）"}
                >
                  <input
                    name="password"
                    type="password"
                    minLength={10}
                    required={!target}
                    autoComplete="new-password"
                  />
                </Field>
                <Field label="角色">
                  <select
                    value={role}
                    onChange={(e) => setRole(e.target.value)}
                  >
                    {Object.entries(ROLE).map(([k, v]) => (
                      <option key={k} value={k}>
                        {v}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label="所属团队">
                  <select
                    required={role !== "admin"}
                    value={team}
                    onChange={(e) => {
                      setTeam(e.target.value);
                      setSelected([]);
                    }}
                  >
                    <option value="">全局（仅管理员）</option>
                    {teams.map((t) => (
                      <option key={t.id} value={t.id}>
                        {t.name}
                      </option>
                    ))}
                  </select>
                </Field>
                {role === "member" && (
                  <Field label="授权IP">
                    {c.ips
                      .filter((i) => i.team_id === team)
                      .map((i) => (
                        <label className="check-row" key={i.id}>
                          <input
                            type="checkbox"
                            checked={selected.includes(i.id)}
                            onChange={(e) =>
                              setSelected(
                                e.target.checked
                                  ? [...selected, i.id]
                                  : selected.filter((v) => v !== i.id),
                              )
                            }
                          />
                          {i.name}
                        </label>
                      ))}
                  </Field>
                )}
              </>
            )}
            <button className="primary">保存</button>
          </form>
        </Modal>
      )}
    </>
  );
}
