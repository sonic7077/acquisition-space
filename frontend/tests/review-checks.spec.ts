import { expect, test } from "@playwright/test";

test("编辑口播时实时检查格式和对标重合度，保存前原稿不变", async ({ page, request }) => {
  expect((await request.post("/api/auth/login", {
    data: { username: "admin", password: "admin" },
  })).ok()).toBe(true);
  const team = await (await request.post("/api/teams", { data: { name: "审稿体检验收组" } })).json();
  const ip = await (await request.post("/api/ips", { data: { name: "审稿体检IP", team_id: team.id } })).json();
  const reference = "甲乙丙丁，戊己庚辛。";
  const saved = await (await request.post("/api/drafts", {
    data: { ip_id: ip.id, skill: "viral-rewrite-writer", title: "来源核对", body: reference, reference },
  })).json();

  await page.goto("/#drafts");
  await page.getByRole("button", { name: "进入工作台" }).click();
  await page.locator(".draft-item").filter({ hasText: saved.id.slice(0, 8) }).click();
  const report = page.getByRole("region", { name: "和对标逐字稿的重合度" });
  await expect(report).toContainText("100% 超过 8%");
  await expect(report.getByText("甲乙丙丁戊己庚辛", { exact: true })).toBeVisible();
  await expect(report).toContainText("8 字");
  await page.getByRole("button", { name: "编辑稿件", exact: true }).click();
  const editor = page.getByRole("dialog", { name: "编辑稿件", exact: true });
  await editor.getByLabel("标题", { exact: true }).fill("这是一个已经写到十六个字以上的测试标题");
  await editor.getByLabel("正文", { exact: true }).fill("先核对合同——随后到评 论 区私信领取资料。");
  const live = editor.getByRole("region", { name: "和对标逐字稿的重合度" });
  await expect(live).toContainText("0% 未超过 8%");
  await expect(editor.getByRole("region", { name: "规则体检" })).toContainText("私信领");
  await expect(editor.getByRole("region", { name: "规则体检" })).toContainText(/上限 16 字/);
  await expect(editor.getByRole("region", { name: "规则体检" })).toContainText("提示");
  const detail = await (await request.get("/api/drafts/" + saved.id)).json();
  expect(detail.body).toBe(reference);

  await editor.getByLabel("标题", { exact: true }).fill("如何看合同");
  await editor.getByLabel("正文", { exact: true }).fill("先核对合同。最后，我把核对步骤整理成了一份《家庭保单检查清单》，里面有三个检查项。");
  await expect(editor.getByRole("region", { name: "规则体检" })).toContainText("全部通过");
  await editor.getByRole("button", { name: "保存新版本", exact: true }).click();
  await expect(editor).not.toBeVisible();
  await expect(report).toContainText("0% 未超过 8%");
  await page.getByRole("button", { name: "批准通过", exact: true }).click();
  await expect(page.getByText("当前版本已批准", { exact: true })).toBeVisible();
});
