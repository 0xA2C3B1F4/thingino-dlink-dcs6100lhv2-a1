import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

async function openMotionFtp(page: Page, request: APIRequestContext, scenario: string): Promise<void> {
  await request.post("/__fixture__/reset");
  await request.post("/__fixture__/scenario", { data: { kind: scenario } });
  await page.goto("/");
  await page.getByLabel("Password", { exact: true }).fill("thingino");
  await page.getByRole("button", { name: "Log in", exact: true }).click();
  await page.goto("/#/motion-privacy");
  await page.getByRole("heading", { name: "Motion FTP", exact: true }).waitFor();
}

function ftpForm(page: Page) {
  return page.locator("form").filter({
    has: page.getByRole("button", { name: "Save FTP", exact: true }),
  });
}

test("Raptor Motion FTP saves, reloads, preserves a blank password, and clears it explicitly", async ({ page, request }) => {
  await openMotionFtp(page, request, "raptor");
  const form = ftpForm(page);
  const enabled = form.getByLabel("Upload Motion JPEG by FTPS", { exact: true });
  const host = form.getByLabel("FTP host", { exact: true });
  const port = form.getByLabel("FTP port", { exact: true });
  const tlsMode = form.getByLabel("TLS mode", { exact: true });
  const username = form.getByLabel("FTP username", { exact: true });
  const password = form.getByLabel("FTP password", { exact: true });
  const path = form.getByLabel("Remote directory", { exact: true });
  const save = form.getByRole("button", { name: "Save FTP", exact: true });
  const configured = {
    enabled: true,
    host: "ftp.example.test",
    port: 21,
    tls_mode: "explicit",
    username: "fixture-camera",
    password: "__BROWSER_FIXTURE_FTP_PASSWORD__",
    path: "motion/events",
  };

  await host.fill(configured.host);
  await port.fill(String(configured.port));
  await tlsMode.selectOption(configured.tls_mode);
  await username.fill(configured.username);
  await password.fill(configured.password);
  await path.fill(configured.path);
  await enabled.check();
  let saving = page.waitForRequest(candidate =>
    candidate.url().endsWith("/api/v1/config/motion-ftp") && candidate.method() === "POST");
  await save.click();
  expect((await saving).postDataJSON()).toEqual(configured);
  await expect(page.getByText("Settings saved.", { exact: true })).toBeVisible();

  await page.reload();
  await expect(enabled).toBeChecked();
  await expect(host).toHaveValue(configured.host);
  await expect(port).toHaveValue(String(configured.port));
  await expect(tlsMode).toHaveValue(configured.tls_mode);
  await expect(username).toHaveValue(configured.username);
  await expect(path).toHaveValue(configured.path);
  await expect(password).toHaveValue("");
  await expect(form.getByLabel("FTP password is saved", { exact: true })).toBeChecked();
  await expect(page.locator("body")).not.toContainText(configured.password);

  saving = page.waitForRequest(candidate =>
    candidate.url().endsWith("/api/v1/config/motion-ftp") && candidate.method() === "POST");
  await save.click();
  const preserved = (await saving).postDataJSON();
  expect(preserved).not.toHaveProperty("password");
  expect(preserved).not.toHaveProperty("clear_password");
  await expect(form.locator("..").locator(":scope > [role=status]")).toHaveText("Settings saved.");
  await page.reload();
  await expect(form.getByLabel("FTP password is saved", { exact: true })).toBeChecked();

  await enabled.uncheck();
  await form.getByLabel("Clear saved FTP password", { exact: true }).check();
  saving = page.waitForRequest(candidate =>
    candidate.url().endsWith("/api/v1/config/motion-ftp") && candidate.method() === "POST");
  await save.click();
  const cleared = (await saving).postDataJSON();
  expect(cleared).toMatchObject({ enabled: false, clear_password: true });
  expect(cleared).not.toHaveProperty("password");
  await expect(form.locator("..").locator(":scope > [role=status]")).toHaveText("Settings saved.");
  await page.reload();
  await expect(form.getByLabel("FTP password is saved", { exact: true })).not.toBeChecked();
  await expect(form.getByLabel("Clear saved FTP password", { exact: true })).not.toBeChecked();
});

test("Raptor Motion FTP shows saved/live mismatch and transport unavailability", async ({ page, request }) => {
  await openMotionFtp(page, request, "raptor-ftp-mismatch");
  let form = ftpForm(page);
  await expect(form.getByLabel("FTP host", { exact: true })).toHaveValue("saved.ftp.example.test");
  await expect(form.getByLabel("FTP runtime matches saved setting", { exact: true })).not.toBeChecked();

  await openMotionFtp(page, request, "raptor-ftp-unavailable");
  form = ftpForm(page);
  await form.getByLabel("FTP host", { exact: true }).fill("ftp.example.test");
  await form.getByLabel("FTP username", { exact: true }).fill("fixture-camera");
  await form.getByLabel("FTP password", { exact: true }).fill("__BROWSER_FIXTURE_FTP_PASSWORD__");
  await form.getByLabel("Upload Motion JPEG by FTPS", { exact: true }).check();
  await form.getByRole("button", { name: "Save FTP", exact: true }).click();
  await expect(page.getByText("Verified FTPS delivery is unavailable on this camera.", { exact: true })).toBeVisible();
  await expect(form.getByLabel("Verified explicit FTPS transport is available", { exact: true })).not.toBeChecked();
});

test("an FTP destination failure does not break other Raptor Motion controls", async ({ page, request }) => {
  await openMotionFtp(page, request, "raptor-ftp-failure");
  const form = ftpForm(page);
  const failedSave = page.waitForResponse(response =>
    response.url().endsWith("/api/v1/config/motion-ftp") && response.request().method() === "POST");
  await form.getByRole("button", { name: "Save FTP", exact: true }).click();
  expect((await failedSave).status()).toBe(503);
  await expect(page.getByText("FTP settings were not applied. Other Motion controls remain available.", { exact: true })).toBeVisible();

  const motionForm = page.locator("form").filter({
    has: page.getByRole("button", { name: "Save settings", exact: true }),
  });
  const motionEnabled = motionForm.getByLabel("Enable motion detection", { exact: true });
  await expect(motionEnabled).toBeChecked();
  await motionEnabled.uncheck();
  await motionForm.getByRole("button", { name: "Save settings", exact: true }).click();
  await expect(page.getByText("Motion is saved as off. Region edits were not saved. Turn motion on to save a region.", { exact: true })).toBeVisible();
  await expect(motionEnabled).not.toBeChecked();
  await expect(page.getByRole("heading", { name: "Motion Email", exact: true })).toBeVisible();
});
