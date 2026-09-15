import { expect, test } from "@playwright/test";

test("Raptor OSD size and RGBA layers are saved and reloaded", async ({ page, request }) => {
  await request.post("/__fixture__/reset");
  await request.post("/__fixture__/scenario", { data: { kind: "raptor" } });
  const state = {
    source: "raptor", persistent: true, enabled: true, available: true,
    matches_saved: true, font_size: 48, format: "%H:%M:%S",
    fill_color: "#ffffffff", outline_color: "#000000ff", background_color: "#00000000",
    fields: { format: true, fill_color: true, outline_color: true, fill_alpha: true, outline_alpha: true,
      font_size: true, enabled: true, background_color: true },
  };
  const saved: unknown[] = [];
  await page.route("**/api/v1/prudynt/osd", route => route.fulfill({ json: state }));
  await page.route("**/api/v1/prudynt", async route => {
    const body = route.request().postDataJSON();
    saved.push(body);
    Object.assign(state, body.osd);
    await route.fulfill({ json: { status: "accepted", persistent: true } });
  });
  await page.goto("/");
  await page.getByLabel("Password", { exact: true }).fill("thingino");
  await page.getByRole("button", { name: "Log in", exact: true }).click();
  await page.getByRole("heading", { name: "Preview", exact: true, level: 1 }).waitFor();
  await page.goto("/#/osd");
  const size = page.getByLabel("Font size (main-stream pixels)", { exact: true });
  await expect(size).toHaveValue("48");
  await expect(size).toBeEnabled();
  await expect(page.getByLabel("Text scale", { exact: true })).toBeHidden();
  for (const pixels of [16, 32, 48]) {
    await size.fill(String(pixels));
    await page.getByRole("button", { name: "Save settings", exact: true }).click();
    await expect(page.locator("main")).toContainText("OSD settings saved.");
    await expect(size).toHaveValue(String(pixels));
    expect(saved.at(-1)).toMatchObject({ osd: { font_size: pixels } });
    expect((saved.at(-1) as { osd: object }).osd).not.toHaveProperty("scale");
  }
  await size.fill("49");
  await page.getByRole("button", { name: "Save settings", exact: true }).click();
  expect(await size.evaluate((input: HTMLInputElement) => input.validity.rangeOverflow)).toBe(true);
  expect(saved).toHaveLength(3);
  await size.fill("48");
  const fill = page.getByLabel("Fill color RGBA value", { exact: true });
  const outline = page.getByLabel("Outline color RGBA value", { exact: true });
  const background = page.getByLabel("Background color RGBA value", { exact: true });
  await fill.fill("#ff000000");
  await outline.fill("#00ff0080");
  await background.fill("#0000ffff");
  await page.getByRole("button", { name: "Save settings", exact: true }).click();
  await expect(page.locator("main")).toContainText("OSD settings saved.");
  expect(saved.at(-1)).toMatchObject({ osd: {
    fill_color: "#ff000000", outline_color: "#00ff0080", background_color: "#0000ffff",
  } });
  await page.getByRole("button", { name: "Reload", exact: true }).click();
  await expect(fill).toHaveValue("#ff000000");
  await expect(outline).toHaveValue("#00ff0080");
  await expect(background).toHaveValue("#0000ffff");
  await expect(page.getByLabel("Fill color alpha", { exact: true })).toHaveValue("0");
  await expect(page.getByLabel("Outline color alpha", { exact: true })).toHaveValue("128");
  await expect(page.getByLabel("Background color alpha", { exact: true })).toHaveValue("255");
  const visibility = page.getByLabel("Burn-in overlay", { exact: true });
  await expect(visibility).toBeChecked();
  for (const enabled of [false, true]) {
    await visibility.setChecked(enabled);
    await page.getByRole("button", { name: "Save settings", exact: true }).click();
    await expect(page.locator("main")).toContainText("OSD settings saved.");
    expect(saved.at(-1)).toMatchObject({ osd: { enabled } });
    await page.getByRole("button", { name: "Reload", exact: true }).click();
    await expect(visibility).toBeChecked({ checked: enabled });
    await expect(visibility).toBeEnabled();
    await expect(size).toBeEnabled({ enabled });
    await expect(page.getByRole("button", { name: "Save settings", exact: true })).toBeEnabled();
  }
  state.fields.font_size = false;
  await page.getByRole("button", { name: "Reload", exact: true }).click();
  await expect(size).toBeDisabled();
});
