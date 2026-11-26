import { expect, test } from "@playwright/test";

test("从审稿规则定位事实状态，人工保存后才能批准", async ({ page, request }) => {
  expect((await request.post("/api/auth/login", {
    data: { username: "admin", password: "admin" },
  })).ok()).toBe(true);
  const team = await (await request.post("/api/teams", { data: { name: "事实核验验收组" } })).json();
  const ip = await (await request.post("/api/ips", { data: { name: "事实核验IP", team_id: team.id } })).json();
  const draft = await (await request.post("/api/drafts", {
    data: { ip_id: ip.id, skill: "gzh-writer", title: "核验正文", body: "资料说明以当前宣传页为准。", fact_status: "pending" },
  })).json();

  await page.goto("/#drafts");
  await page.getByRole("button", { name: "进入工作台" }).click();
  await page.locator(".draft-item").filter({ hasText: draft.id.slice(0, 8) }).click();
  const rules = page.getByRole("region", { name: "规则体检" });
  await rules.getByRole("button", { name: "去核验事实", exact: true }).click();
  const editor = page.getByRole("dialog", { name: "编辑稿件" });
  const facts = editor.getByRole("combobox", { name: "事实核验状态", exact: true });
  await expect(facts).toBeFocused();
  await expect(facts).toHaveValue("pending");
  await facts.selectOption("verified");
  await editor.getByLabel("正文", { exact: true }).fill("核对责任范围时，请根据现行合同条款逐项确认。");
  await expect(facts).toHaveValue("pending");
  await facts.selectOption("verified");
  expect((await (await request.get(`/api/drafts/${draft.id}`)).json()).fact_status).toBe("pending");
  await editor.getByRole("button", { name: "保存新版本", exact: true }).click();
  await expect(editor).toBeHidden();
  await expect(page.getByText("事实：已核实", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "批准通过", exact: true }).click();
  await expect(page.getByText("当前版本已批准", { exact: true })).toBeVisible();
  const approved = await (await request.get(`/api/drafts/${draft.id}`)).json();
  expect(approved.approved_version).toBe(2);

  await page.getByRole("button", { name: "编辑稿件", exact: true }).click();
  await editor.getByLabel("正文", { exact: true }).fill("咨询前请查看当前有效的服务约定。");
  await expect(facts).toHaveValue("pending");
  await facts.selectOption("verified");
  await editor.getByRole("button", { name: "保存新版本", exact: true }).click();
  await expect(editor).toBeHidden();
  const rechecked = await (await request.get(`/api/drafts/${draft.id}`)).json();
  expect(rechecked.fact_status).toBe("verified");
  expect(rechecked.approved_version).toBeNull();
});

test("队员编辑已核验稿件时保留无事实改动的核验，正文改动转待核实", async ({ page, request }) => {
  expect((await request.post("/api/auth/login", {
    data: { username: "admin", password: "admin" },
  })).ok()).toBe(true);
  const team = await (await request.post("/api/teams", { data: { name: "核验队员组" } })).json();
  const ip = await (await request.post("/api/ips", { data: { name: "核验队员IP", team_id: team.id } })).json();
  const draft = await (await request.post("/api/drafts", {
    data: { ip_id: ip.id, skill: "gzh-writer", title: "已核验稿件", body: "核对合同内容。", fact_status: "verified" },
  })).json();
  const username = `fact_member_${Date.now()}`;
  const password = "Facts-123456!";
  expect((await request.post("/api/users", {
    data: { username, password, name: "事实队员", role: "member", team_id: team.id, ip_ids: [ip.id] },
  })).ok()).toBe(true);

  await page.goto("/#drafts");
  await page.getByLabel("用户名").fill(username);
  await page.getByLabel("密码", { exact: true }).fill(password);
  await page.getByRole("button", { name: "进入工作台" }).click();
  const edit = page.getByRole("button", { name: "编辑稿件", exact: true });
  await edit.click();
  const editor = page.getByRole("dialog", { name: "编辑稿件" });
  const save = editor.getByRole("button", { name: "保存新版本", exact: true });
  await editor.getByLabel("封面字", { exact: true }).fill("测试封面");
  await save.click();
  await expect(editor).toBeHidden();
  const retained = await (await request.get(`/api/drafts/${draft.id}`)).json();
  expect(retained.fact_status).toBe("verified");
  expect(retained.cover).toBe("测试封面");

  await edit.click();
  await editor.getByLabel("正文", { exact: true }).fill("新增待审核的合同事实。");
  const facts = editor.getByRole("combobox", { name: "事实核验状态", exact: true });
  await expect(facts).toHaveValue("pending");
  await expect(facts.locator('option[value="verified"]')).toHaveCount(0);
  await save.click();
  await expect(editor).toBeHidden();
  expect((await (await request.get(`/api/drafts/${draft.id}`)).json()).fact_status).toBe("pending");
});
