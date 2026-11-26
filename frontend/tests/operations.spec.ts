import { test, expect } from "@playwright/test";

test("运营主菜单、双模式设置、账号授权iframe、扫脸QR、草稿派发与作品清洗", async ({
  page,
  request,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));

  const mockMcpUrl = process.env.E2E_MOCK_MCP_URL || "http://127.0.0.1:3199/mcp";
  const mockMcpToken = process.env.E2E_MOCK_MCP_TOKEN || "mock-mcp-token-secret";

  await page.goto("/");
  await page.getByLabel("用户名").fill("admin");
  await page.getByLabel("密码", { exact: true }).fill("admin");
  await page.getByRole("button", { name: "进入工作台" }).click();
  await expect(page.getByRole("heading", { name: "工作台", exact: true })).toBeVisible();

  // Verify original 发布排期 remains under 内容生产 and new 运营 nav items exist
  await expect(page.getByRole("button", { name: "发布排期", exact: true })).toBeVisible();
  for (const label of ["账号与授权", "授权中心", "发布任务", "作品与数据", "评论与私信", "监控与告警"]) {
    await expect(page.getByRole("button", { name: label, exact: true })).toBeVisible();
  }

  await request.post("/api/auth/login", { data: { username: "admin", password: "admin" } });

  // Seed team, IP, knowledge, approved draft, video asset & schedule via API
  const team = await (await request.post("/api/teams", { data: { name: "运营E2E团队" } })).json();
  const ip = await (await request.post("/api/ips", { data: { name: "运营E2E-IP", team_id: team.id } })).json();
  const up = await request.post("/api/knowledge/preview", { multipart: { category_id: "methods-service", files: { name: "facts.md", mimeType: "text/markdown", buffer: Buffer.from("Transparent consulting facts.") } } });
  expect(up.ok()).toBeTruthy();
  const batch = await up.json();
  const confirmResp = await request.post("/api/knowledge/confirm", { data: { batch_id: batch.id } });
  expect(confirmResp.ok()).toBeTruthy();
  const doc = (await confirmResp.json()).items[0];
  await request.patch(`/api/knowledge/${doc.id}`, { data: { status: "verified" } });
  let draft = await (await request.post("/api/drafts", { data: { ip_id: ip.id, title: "运营联调稿件", body: "基于事实核验的测试内容。", sources: [{ id: doc.id, hash: doc.hash, title: doc.title }] } })).json();
  draft = await (await request.post(`/api/drafts/${draft.id}/approve`, { data: { version: draft.version } })).json();
  await request.post("/api/assets", { multipart: { draft_id: draft.id, file: { name: "demo.mp4", mimeType: "video/mp4", buffer: Buffer.from("\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00") } } });
  await request.post("/api/schedules", { data: { draft_id: draft.id, platform: "抖音", account: "测试抖音号", planned_at: new Date().toISOString() } });

  // 1. Settings -> 发布执行器 dual-mode configuration & pairing command
  await page.getByRole("button", { name: "系统设置", exact: true }).click();
  await page.getByRole("button", { name: "发布执行器", exact: true }).click();
  await page.getByLabel("执行模式").selectOption("server_direct");
  await page.getByLabel("小V猫 MCP 地址（同机直连用）").fill(mockMcpUrl);
  await page.getByLabel(/小V猫 MCP 令牌/).fill(mockMcpToken);
  await page.getByRole("button", { name: "保存设置", exact: true }).click();
  await page.getByRole("button", { name: "生成前端本机代理配对命令", exact: true }).click();
  await expect(page.locator("pre")).toContainText("executor/vcat_agent.py --mode client_local");

  // 2. 运营 -> 账号与授权: sync accounts, bind IP, open iframe login modal, trigger Channels verify link
  await page.getByRole("button", { name: "账号与授权", exact: true }).click();
  await page.getByRole("button", { name: "同步节点账号", exact: true }).click();
  await expect(page.getByText("测试抖音号")).toBeVisible();
  await expect(page.getByText("测试视频号")).toBeVisible();

  await page.locator("tr", { hasText: "测试抖音号" }).getByRole("button", { name: "绑定IP" }).click();
  await page.getByRole("dialog").getByLabel("目标授权IP").selectOption(ip.id);
  await page.getByRole("dialog").getByRole("button", { name: "保存绑定" }).click();

  // P0: Unbind and rebind, then test login iframe cancel
  await page.locator("tr", { hasText: "测试抖音号" }).getByRole("button", { name: "解绑IP" }).click();
  await page.locator("tr", { hasText: "测试抖音号" }).getByRole("button", { name: "绑定IP" }).click();
  await page.getByRole("dialog").getByLabel("目标授权IP").selectOption(ip.id);
  await page.getByRole("dialog").getByRole("button", { name: "保存绑定" }).click();

  await page.getByRole("button", { name: "Web 扫码新增账号", exact: true }).click();
  await page.getByRole("dialog").getByRole("button", { name: "生成并加载登录助手" }).click();
  await expect(page.locator("iframe.ops-auth-iframe")).toBeVisible();
  await page.getByRole("dialog").getByRole("button", { name: "取消并回收链接" }).click();

  // P0: Remove 小红书 account with nickname confirm, then toggle history visibility
  await page.locator("tr", { hasText: "测试小红书" }).getByRole("button", { name: "移除账号" }).click();
  await page.getByRole("dialog").getByPlaceholder("测试小红书").fill("测试小红书");
  await page.getByRole("dialog").getByRole("button", { name: "确认从小V猫移除" }).click();
  await expect(page.locator("tr", { hasText: "测试小红书" })).toHaveCount(0);
  await page.getByLabel("显示已移除历史账号").check();
  await expect(page.locator("tr", { hasText: "测试小红书" })).toContainText("已从小V猫移除");
  await page.getByLabel("显示已移除历史账号").uncheck();

  // Trigger Channels face verify link -> navigates to 授权中心, shows QR PNG & allows cancel
  await page.locator("tr", { hasText: "测试视频号" }).getByRole("button", { name: "扫脸验证" }).click();
  await expect(page.getByRole("heading", { name: "授权中心", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "展示二维码" }).first().click();
  const qrImg = page.locator("img.ops-qr-img");
  await expect(qrImg).toBeVisible();
  await page.getByRole("dialog").getByRole("button", { name: "关闭" }).click();
  await page.getByRole("button", { name: "取消待办" }).first().click();

  // P1: 发布排期 -> 去运营派发一键直达, dispatch draft, then promote draft -> 正式发布
  await page.getByRole("button", { name: "发布排期", exact: true }).click();
  await page.getByRole("button", { name: "去运营派发" }).first().click();
  await expect(page.getByRole("heading", { name: "发布任务", exact: true })).toBeVisible();
  const dlg = page.getByRole("dialog");
  await expect(dlg.getByLabel("选择已有排期")).not.toHaveValue("");
  await dlg.getByLabel("目标平台账号").selectOption({ index: 1 });
  await dlg.getByLabel("执行动作").selectOption("draft");
  await dlg.getByRole("button", { name: "提交执行" }).click();
  await expect(page.getByText("已推草稿箱")).toBeVisible();
  await page.getByRole("button", { name: "一键转正式发布" }).first().click();
  await page.getByRole("dialog").getByRole("button", { name: "确认转正式发布" }).click();
  await expect(page.getByText("平台已受理")).toBeVisible();

  // 4. P1/P2 运营 -> 作品与数据: sync posts, check daily modal, visibility modify & disabled delete placeholder
  await page.getByRole("button", { name: "作品与数据", exact: true }).click();
  await page.getByLabel("选择账号").selectOption({ index: 1 });
  await page.getByRole("button", { name: "同步账号作品", exact: true }).click();
  await expect(page.getByText("小红书实测作品")).toBeVisible();
  const delPostBtn = page.getByRole("button", { name: "删除作品（暂不开放）" }).first();
  await expect(delPostBtn).toBeVisible();
  await expect(delPostBtn).toBeDisabled();
  await page.getByRole("button", { name: "分日明细" }).first().click();
  await expect(page.getByRole("dialog")).toContainText("2026-11-26");
  await page.getByRole("dialog").getByRole("button", { name: "关闭" }).click();
  await page.getByRole("button", { name: "改仅自己可见" }).first().click();
  await page.getByRole("dialog").getByRole("button", { name: "确认设为仅自己可见" }).click();

  // 5. P1 互动与监控: AI draft, session messages, monitor run, flow report, punish hide & disabled delete placeholder
  await page.getByRole("button", { name: "评论与私信", exact: true }).click();
  await expect(page.getByRole("heading", { name: /评论聚合/ })).toBeVisible();
  await expect(page.getByText("怎么联系购买")).toBeVisible();
  await page.getByRole("button", { name: "查看消息" }).first().click();
  await expect(page.getByRole("dialog")).toContainText("这个服务怎么收费");
  await page.getByRole("dialog").getByRole("button", { name: "关闭" }).click();

  await page.getByRole("button", { name: "监控与告警", exact: true }).click();
  await expect(page.getByRole("heading", { name: /账号异常与待办/ })).toBeVisible();
  await expect(page.getByText("测试异常作品")).toBeVisible();
  const delPunishBtn = page.locator("tr", { hasText: "测试异常作品" }).getByRole("button", { name: "删除作品（暂不开放）" });
  await expect(delPunishBtn).toBeVisible();
  await expect(delPunishBtn).toBeDisabled();
  await page.getByRole("button", { name: "立即执行一次" }).first().click();
  await page.getByRole("dialog").getByRole("button", { name: "确认立即执行" }).click();

  expect(errors).toEqual([]);
});
