import { useState, type FormEvent } from "react";
import { ArrowRight } from "lucide-react";
import { post, type Row } from "../api";
import { Field, getError } from "../components/ui";

export default function Login({onLogin}: {onLogin: (u: Row) => void}) {
  const [error, setError] = useState(""), [busy, setBusy] = useState(false);
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault(); setBusy(true); setError("");
    const form = new FormData(e.currentTarget);
    try {
      onLogin(await post("/auth/login", {username: form.get("username"), password: form.get("password")}));
    } catch (e) {setError(getError(e));} finally {setBusy(false);}
  }
  return <div className="simple-login">
    <section className="login-panel">
      <div className="login-brand"><span className="brand-icon">获</span><h1>获客空间</h1></div>
      <h2>管理员登录</h2>
      <form onSubmit={submit}>
        <Field label="用户名"><input name="username" autoComplete="username" required defaultValue="admin"/></Field>
        <Field label="密码"><input name="password" type="password" autoComplete="current-password" required defaultValue="admin"/></Field>
        {error&&<p className="error-text" role="alert">{error}</p>}
        <button className="primary full" disabled={busy}>{busy?"正在登录…":"进入工作台"}<ArrowRight size={17}/></button>
      </form>
    </section>
  </div>;
}
