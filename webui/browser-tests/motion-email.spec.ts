import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

async function openMotionEmail(page: Page, request: APIRequestContext, scenario: string): Promise<void> {
  await request.post("/__fixture__/reset");
  await request.post("/__fixture__/scenario", { data: { kind: scenario } });
  await page.goto("/");
  await page.getByLabel("Password", { exact: true }).fill("thingino");
  await page.getByRole("button", { name: "Log in", exact: true }).click();
  await page.goto("/#/motion-privacy");
  await page.getByRole("heading", { name: "Motion Email", exact: true }).waitFor();
}

function emailForm(page: Page) {
  return page.locator("form").filter({
    has: page.getByRole("button", { name: "Save Email", exact: true }),
  });
}

test("Raptor Motion Email saves, reloads, preserves a blank password, and clears it explicitly", async ({ page, request }) => {
  await openMotionEmail(page, request, "raptor");
  const form = emailForm(page);
  const enabled = form.getByLabel("Send Motion events by email", { exact: true });
  const host = form.getByLabel("SMTP host", { exact: true });
  const port = form.getByLabel("SMTP port", { exact: true });
  const tlsMode = form.getByLabel("TLS mode", { exact: true });
  const username = form.getByLabel("SMTP username", { exact: true });
  const password = form.getByLabel("SMTP password", { exact: true });
  const from = form.getByLabel("From address", { exact: true });
  const to = form.getByLabel("To address", { exact: true });
  const save = form.getByRole("button", { name: "Save Email", exact: true });
  const configured = {
    enabled: true,
    host: "smtp.example.test",
    port: 465,
    tls_mode: "implicit",
    username: "fixture-camera",
    password: "__BROWSER_FIXTURE_SMTP_PASSWORD__",
    from_address: "camera@example.test",
    to_address: "owner@example.test",
  };

  await host.fill(configured.host);
  await port.fill(String(configured.port));
  await tlsMode.selectOption(configured.tls_mode);
  await username.fill(configured.username);
  await password.fill(configured.password);
  await from.fill(configured.from_address);
  await to.fill(configured.to_address);
  await enabled.check();
  let saving = page.waitForRequest(candidate =>
    candidate.url().endsWith("/api/v1/config/motion-email") && candidate.method() === "POST");
  await save.click();
  expect((await saving).postDataJSON()).toEqual(configured);
  await expect(page.getByText("Settings saved.", { exact: true })).toBeVisible();

  await page.reload();
  await expect(enabled).toBeChecked();
  await expect(host).toHaveValue(configured.host);
  await expect(port).toHaveValue(String(configured.port));
  await expect(tlsMode).toHaveValue(configured.tls_mode);
  await expect(username).toHaveValue(configured.username);
  await expect(from).toHaveValue(configured.from_address);
  await expect(to).toHaveValue(configured.to_address);
  await expect(password).toHaveValue("");
  await expect(form.getByLabel("SMTP password is saved", { exact: true })).toBeChecked();
  await expect(page.locator("body")).not.toContainText(configured.password);

  saving = page.waitForRequest(candidate =>
    candidate.url().endsWith("/api/v1/config/motion-email") && candidate.method() === "POST");
  await save.click();
  const preserved = (await saving).postDataJSON();
  expect(preserved).not.toHaveProperty("password");
  expect(preserved).not.toHaveProperty("clear_password");
  await expect(form.locator("..").locator(":scope > [role=status]")).toHaveText("Settings saved.");
  await page.reload();
  await expect(form.getByLabel("SMTP password is saved", { exact: true })).toBeChecked();

  await enabled.uncheck();
  await form.getByLabel("Clear saved SMTP password", { exact: true }).check();
  saving = page.waitForRequest(candidate =>
    candidate.url().endsWith("/api/v1/config/motion-email") && candidate.method() === "POST");
  await save.click();
  const cleared = (await saving).postDataJSON();
  expect(cleared).toMatchObject({ enabled: false, clear_password: true });
  expect(cleared).not.toHaveProperty("password");
  await expect(form.locator("..").locator(":scope > [role=status]")).toHaveText("Settings saved.");
  await page.reload();
  await expect(form.getByLabel("SMTP password is saved", { exact: true })).not.toBeChecked();
  await expect(form.getByLabel("Clear saved SMTP password", { exact: true })).not.toBeChecked();
});

test("Raptor Motion Email shows saved/live mismatch and transport unavailability", async ({ page, request }) => {
  await openMotionEmail(page, request, "raptor-email-mismatch");
  let form = emailForm(page);
  await expect(form.getByLabel("SMTP host", { exact: true })).toHaveValue("saved.smtp.example.test");
  await expect(form.getByLabel("Email runtime matches saved setting", { exact: true })).not.toBeChecked();

  await openMotionEmail(page, request, "raptor-email-unavailable");
  form = emailForm(page);
  await form.getByLabel("SMTP host", { exact: true }).fill("smtp.example.test");
  await form.getByLabel("From address", { exact: true }).fill("camera@example.test");
  await form.getByLabel("To address", { exact: true }).fill("owner@example.test");
  await form.getByLabel("Send Motion events by email", { exact: true }).check();
  await form.getByRole("button", { name: "Save Email", exact: true }).click();
  await expect(page.getByText("SMTP delivery is unavailable on this camera.", { exact: true })).toBeVisible();
  await expect(form.getByLabel("SMTP and SMTPS transport is available", { exact: true })).not.toBeChecked();
});

test("an Email destination failure does not break other Raptor Motion controls", async ({ page, request }) => {
  await openMotionEmail(page, request, "raptor-email-failure");
  const form = emailForm(page);
  const failedSave = page.waitForResponse(response =>
    response.url().endsWith("/api/v1/config/motion-email") && response.request().method() === "POST");
  await form.getByRole("button", { name: "Save Email", exact: true }).click();
  expect((await failedSave).status()).toBe(503);
  await expect(page.getByText("Email settings were not applied. Other Motion controls remain available.", { exact: true })).toBeVisible();

  const motionForm = page.locator("form").filter({
    has: page.getByRole("button", { name: "Save settings", exact: true }),
  });
  const motionEnabled = motionForm.getByLabel("Enable motion detection", { exact: true });
  await expect(motionEnabled).toBeChecked();
  await motionEnabled.uncheck();
  await motionForm.getByRole("button", { name: "Save settings", exact: true }).click();
  await expect(page.getByText("Motion is saved as off. Region edits were not saved. Turn motion on to save a region.", { exact: true })).toBeVisible();
  await expect(motionEnabled).not.toBeChecked();
  await expect(page.getByRole("heading", { name: "Motion ntfy", exact: true })).toBeVisible();
});
