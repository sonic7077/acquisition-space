import { test, expect } from "@playwright/test";

test("管理员完成知识上传、写手、审稿、排期和模型设置", async ({
  page,
  request,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/");
  await expect(page).toHaveTitle("获客空间");
  await expect(page.locator("form input")).toHaveCount(2);
  await expect(page.getByLabel("用户名")).toHaveValue("admin");
  await expect(page.getByLabel("密码",{exact:true})).toHaveValue("admin");
  await expect(page.getByText("初始化令牌",{exact:true})).toHaveCount(0);
  await page.getByLabel("用户名").fill("admin");
  await page.getByLabel("密码", { exact: true }).fill("admin");
  await page.getByRole("button", { name: "进入工作台" }).click();
  await expect(
    page.getByRole("heading", { name: "工作台", exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: "/tmp/acquisition-dashboard.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "用户管理", exact: true }).click();
  await page.getByRole("button", { name: "创建团队", exact: true }).click();
  await page
    .getByRole("dialog")
    .getByLabel("名称", { exact: true })
    .fill("内容团队");
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "保存", exact: true })
    .click();
  await expect(page.getByRole("dialog")).not.toBeVisible();
  await page.getByRole("button", { name: "创建IP", exact: true }).click();
  await page
    .getByRole("dialog")
    .getByLabel("名称", { exact: true })
    .fill("顾问IP");
  await page
    .getByRole("dialog")
    .getByLabel("所属团队")
    .selectOption({ label: "内容团队" });
  await page
    .getByRole("dialog")
    .getByLabel("身份档案")
    .fill("为客户讲清服务选择，表达清晰简洁。");
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "保存", exact: true })
    .click();
  await expect(page.getByRole("dialog")).not.toBeVisible();
  await page.getByRole("button", { name: "知识库", exact: true }).click();
  await page
    .getByLabel("筛选IP", { exact: true })
    .selectOption({ label: "顾问IP" });
  await page.getByLabel("导入末级目录", { exact: true }).selectOption("methods-service");
  const chooser = page.waitForEvent("filechooser");
  await page.getByRole("button", { name: "上传文件 / ZIP" }).click();
  await (
    await chooser
  ).setFiles({
    name: "服务说明.md",
    mimeType: "text/markdown",
    buffer: Buffer.from(
      "咨询服务包括每周沟通和方案解释，具体内容以合同约定为准。",
    ),
  });
  await expect(
    page.getByRole("dialog", { name: "确认知识库导入" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "确认导入", exact: true }).click();
  await expect(page.getByText("服务说明", { exact: true })).toBeVisible();
  await page.locator("tr").filter({ hasText: "服务说明" }).getByRole("button", { name: "查看内容", exact: true }).click();
  await page.getByRole("button", { name: "核验通过", exact: true }).click();
  await expect(
    page
      .locator("tr")
      .filter({ hasText: "服务说明" })
      .getByText("已核验", { exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: "/tmp/acquisition-knowledge.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "IP与写手", exact: true }).click();
  await page
    .getByLabel("目标IP", { exact: true })
    .selectOption({ label: "顾问IP" });
  await page.getByLabel("内容主题与要求").fill("咨询服务包含哪些内容");
  await page.getByRole("button", { name: "生成上下文预览" }).click();
  await expect(page.getByText(/已引用 [1-9]\d* 条已核验知识/)).toBeVisible();
  await page.getByRole("button", { name: "审稿", exact: true }).click();
  await page.getByRole("button", { name: "导入 / 新建稿件" }).click();
  await page
    .getByRole("dialog")
    .getByLabel("目标IP", { exact: true })
    .selectOption({ label: "顾问IP" });
  await page
    .getByRole("dialog")
    .getByLabel("标题", { exact: true })
    .fill("如何选服务");
  await page
    .getByRole("dialog")
    .getByLabel("正文", { exact: true })
    .fill(
      "先把你的需求说清楚，再选择适合的服务。问清范围，认真核对约定，把沟通放在决定之前。",
    );
  await page.getByRole("dialog").getByRole("checkbox", {name:"服务说明",exact:true}).check();
  await page.getByLabel("事实核验状态").selectOption("none");
  await page.getByRole("button", { name: "保存新版本", exact: true }).click();
  await expect(page.getByRole("dialog")).not.toBeVisible();
  await page.getByRole("button", { name: "批准通过" }).click();
  await expect(page.getByText("当前版本已批准", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "拍摄上传", exact: true }).click();
  await expect(page.getByRole("heading", { name: "AI视频生成", exact: true })).toHaveCount(0);
  await page.getByLabel("明确关联稿件").selectOption({ index: 1 });
  const videoChooser = page.waitForEvent("filechooser");
  await page.getByRole("button", { name: "选择视频与封面" }).click();
  await (
    await videoChooser
  ).setFiles({
    name: "video.mp4",
    mimeType: "video/mp4",
    buffer: Buffer.concat([
      Buffer.from([0, 0, 0, 24]),
      Buffer.from("ftypmp42"),
      Buffer.alloc(32),
    ]),
  });
  await expect(page.getByText("video.mp4", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "发布排期", exact: true }).click();
  await page.getByRole("button", { name: "新增排期" }).click();
  await page.getByLabel("当前批准稿件").selectOption({ index: 1 });
  await page.getByLabel("明确发布账号").fill("顾问视频号");
  await page.getByLabel("计划时间（北京时间）").fill("2026-11-20T10:00");
  await page.getByRole("button", { name: "创建计划", exact: true }).click();
  await expect(page.getByRole("dialog")).not.toBeVisible();
  await expect(page.getByText("顾问视频号 / 抖音")).toBeVisible();
  await page.getByRole("button", { name: "系统设置", exact: true }).click();
  await expect(page.getByRole("button", { name: "视频生成", exact: true })).toHaveCount(0);
  await expect(
    page.getByRole("heading", { name: "系统设置", exact: true }),
  ).toBeVisible();
  await page.getByLabel("服务地址（包含 /v1）").fill("https://example.org/v1");
  await page.getByLabel("模型名称", { exact: true }).fill("test-model");
  await page.getByLabel("API Key · 未配置").fill("browser-test-secret");
  await page.getByRole("button", { name: "保存设置" }).click();
  await expect(page.getByLabel("API Key · 已配置")).toBeVisible();
  await expect(page.getByLabel("API Key · 已配置")).toHaveValue("");
  await page.screenshot({
    path: "/tmp/acquisition-settings.png",
    fullPage: true,
  });
  expect(errors).toEqual([]);
});

test("团队长和队员无法访问系统设置及用户管理", async ({ page, request }) => {
  const adminLogin = await request.post("/api/auth/login", {
    data: { username: "admin", password: "admin" },
  });
  expect(adminLogin.ok()).toBeTruthy();
  const teams = await (await request.get("/api/teams")).json();
  const ips = await (await request.get("/api/ips")).json();
  const workflowIp = ips.find((i: any) => i.name === "顾问IP");
  const workflowTeam = teams.find((t: any) => t.id === workflowIp.team_id);
  for (const role of ["leader", "member"]) {
    const username = "browser-" + role;
    const created = await request.post("/api/users", {
      data: {
        username,
        password: "Role-123456!",
        name: role === "leader" ? "验收团队长" : "验收队员",
        role,
        team_id: workflowTeam.id,
        ip_ids: [workflowIp.id],
      },
    });
    expect(created.ok()).toBeTruthy();
    await page.goto("/");
    await page.getByLabel("用户名").fill(username);
    await page.getByLabel("密码", { exact: true }).fill("Role-123456!");
    await page.getByRole("button", { name: "进入工作台" }).click();
    await expect(
      page.getByRole("heading", { name: "工作台", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "系统设置", exact: true }),
    ).toHaveCount(0);
    await expect(
      page.getByRole("button", { name: "用户管理", exact: true }),
    ).toHaveCount(0);
    await page.goto("/#settings");
    await expect(
      page.getByRole("heading", { name: "仅管理员可访问" }),
    ).toBeVisible();
    expect((await page.request.get("/api/settings")).status()).toBe(403);
    expect((await page.request.get("/api/users")).status()).toBe(403);
    await page.goto("/#drafts");
    if (role === "member")
      await expect(page.getByRole("button", { name: "批准通过" })).toHaveCount(
        0,
      );
    else
      await expect(
        page.getByRole("button", { name: "批准通过" }),
      ).toBeVisible();
    await page.request.post("/api/auth/logout");
    await page.reload();
  }
});

test("手机宽度下知识库与侧栏可用", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await page.getByLabel("用户名").fill("admin");
  await page.getByLabel("密码", { exact: true }).fill("admin");
  await page.getByRole("button", { name: "进入工作台" }).click();
  await expect(
    page.getByRole("heading", { name: "工作台", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "知识库", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "知识库", exact: true }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  await page.screenshot({
    path: "/tmp/acquisition-mobile.png",
    fullPage: true,
  });
});

test("指标预览先确认再入库，线索记录可跟进", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("用户名").fill("admin");
  await page.getByLabel("密码", { exact: true }).fill("admin");
  await page.getByRole("button", { name: "进入工作台" }).click();
  await page.getByRole("button", { name: "数据复盘", exact: true }).click();
  await page
    .getByLabel("目标IP", { exact: true })
    .selectOption({ label: "顾问IP" });
  const chooser = page.waitForEvent("filechooser");
  await page.getByRole("button", { name: "选择报表", exact: true }).click();
  await (
    await chooser
  ).setFiles({
    name: "metrics.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("date,work_id,views,leads\n2026-11-16,work-1,120,3\n"),
  });
  await expect(
    page.getByRole("dialog", { name: "确认指标导入" }),
  ).toBeVisible();
  expect(
    (await (await page.request.get("/api/records/metrics")).json()).length,
  ).toBe(0);
  await page.getByRole("button", { name: "确认写入指标" }).click();
  await expect(page.getByRole("dialog")).not.toBeVisible();
  await expect(
    page.getByRole("heading", { name: "经营复盘", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "客户线索", exact: true }).click();
  await page.getByRole("button", { name: "新增线索", exact: true }).click();
  const modal = page.getByRole("dialog");
  await modal
    .getByLabel("目标IP", { exact: true })
    .selectOption({ label: "顾问IP" });
  await modal.getByLabel("标题", { exact: true }).fill("客户想了解方案");
  await modal.getByLabel("评论或咨询原话").fill("服务有哪些具体内容？");
  await modal.getByLabel("负责人").fill("验收管理员");
  await modal.getByRole("button", { name: "保存线索", exact: true }).click();
  await expect(page.getByRole("dialog")).not.toBeVisible();
  await expect(page.getByText("客户想了解方案", { exact: true })).toBeVisible();
});
