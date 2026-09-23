import { expect, test, type Page } from "@playwright/test";

type AudioControl = Record<string, boolean | null | string>;

const audioControl = (
  state: "legacy" | "legacy-pending" | "settled" | "pending",
  enabled: boolean,
  rmrRequired = true,
): AudioControl => ({
  supported: true,
  available: true,
  editable: true,
  apply: "full-camera-restart",
  selection_required: state === "legacy",
  legacy_selection_pending: state === "legacy-pending",
  configured_enabled: state === "legacy" ? null : enabled,
  saved_readback_available: true,
  saved_available: state !== "legacy",
  saved_enabled: state === "legacy" ? null : enabled,
  configured_matches_saved: state === "legacy" ? null : true,
  active_available: state === "settled" || state === "pending",
  active_enabled: state === "settled" || state === "pending" ? state === "settled" && enabled : null,
  pending_restart: state === "legacy" ? null : state !== "settled",
  rsd_active_enabled: state === "legacy" || state === "legacy-pending",
  rmr_required: rmrRequired,
  rmr_active_enabled: rmrRequired ? false : null,
  rsd_source_available: true,
  rmr_source_available: rmrRequired ? true : null,
});

const disabledSubstream = {
  supported: true,
  available: true,
  editable: true,
  required: false,
  active_enabled: false,
  configured_enabled: false,
  saved_available: true,
  saved_enabled: false,
  configured_matches_saved: true,
  pending_restart: false,
  motion_blocks_disable: false,
  recorder_blocks_disable: false,
  recorder_state_known: true,
  recorder_active_blocks_disable: false,
  apply: "full-camera-restart",
};

async function logIn(page: Page): Promise<void> {
  await page.goto("/");
  await page.getByLabel("Password", { exact: true }).fill("thingino");
  await page.getByRole("button", { name: "Log in", exact: true }).click();
  await page.getByRole("heading", { name: "Preview", exact: true, level: 1 }).waitFor();
}

function streamResponse(id: number, gop: number, audio: AudioControl): Record<string, unknown> {
  return {
    source: "raptor",
    persistent: true,
    stream_id: id,
    supported: true,
    available: true,
    gop,
    saved_gop: gop,
    matches_saved: true,
    audio_control: audio,
    ...(id === 1 ? { enable_control: disabledSubstream } : {}),
  };
}

test("per-stream audio requires an explicit legacy choice and leaves unrelated GOP saves alone", async ({ page, request }) => {
  await request.post("/__fixture__/reset");
  await request.post("/__fixture__/scenario", { data: { kind: "raptor" } });

  const gops = [30, 30];
  const audio = [audioControl("legacy", false), audioControl("legacy", false, false)];
  const posts: unknown[] = [];
  await page.route(/\/api\/v1\/prudynt\/stream[01]$/, route => {
    const id = Number(route.request().url().slice(-1));
    return route.fulfill({ json: streamResponse(id, gops[id]!, audio[id]!) });
  });
  await page.route("**/api/v1/prudynt", async route => {
    const body = route.request().postDataJSON() as Record<string, Record<string, unknown>>;
    posts.push(body);
    for (const name of Object.keys(body)) {
      const id = Number(name.slice(-1));
      if (typeof body[name]?.gop === "number") gops[id] = body[name].gop as number;
      if (typeof body[name]?.audio_enabled === "boolean") {
        audio[id] = audioControl("legacy-pending", body[name].audio_enabled as boolean, id === 0);
      }
    }
    return route.fulfill({ json: {
      status: "accepted",
      persistent: true,
      ...(Object.values(body).some(fields => fields.audio_enabled !== undefined)
        ? { pending_restart: true }
        : {}),
    } });
  });

  await logIn(page);
  await page.goto("/#/streams");
  const cards = page.locator(".stream-card");
  const mainAudio = cards.nth(0).getByLabel("Include audio", { exact: true });
  const subAudio = cards.nth(1).getByLabel("Include audio", { exact: true });
  const save = page.getByRole("button", { name: "Save settings", exact: true });
  const pageSection = page.locator("section.page").filter({
    has: page.getByRole("heading", { name: "Video streams", exact: true }),
  });
  const status = pageSection.locator(":scope > [role=status]");

  await expect(status).toHaveCount(1);
  await expect(status).toBeEmpty();
  await expect(save).toBeEnabled();
  await expect(mainAudio).toBeEnabled();
  await expect(subAudio).toBeEnabled();
  expect(await mainAudio.evaluate(input => (input as HTMLInputElement).indeterminate)).toBe(true);
  expect(await subAudio.evaluate(input => (input as HTMLInputElement).indeterminate)).toBe(true);
  await expect(cards.nth(0).getByLabel("Audio inclusion status", { exact: true })).toHaveValue(/Choose an explicit shared policy/);
  await expect(cards.nth(1).getByLabel("Audio inclusion status", { exact: true })).toHaveValue(/Choose an explicit shared policy/);

  await cards.nth(0).getByLabel("GOP", { exact: true }).fill("45");
  await save.click();
  await expect(status).toHaveText("Stream settings applied and saved with checked readback.");
  expect(posts).toEqual([{ stream0: { gop: 45 } }]);
  expect(await mainAudio.evaluate(input => (input as HTMLInputElement).indeterminate)).toBe(true);

  await mainAudio.click();
  await mainAudio.click();
  await expect(mainAudio).not.toBeChecked();
  expect(await mainAudio.evaluate(input => (input as HTMLInputElement).indeterminate)).toBe(false);
  await save.click();
  expect(posts.at(-1)).toEqual({ stream0: { audio_enabled: false } });
  await expect(status).toHaveText(/Pending stream configuration remains inactive/);

  await mainAudio.check();
  await save.click();
  expect(posts.at(-1)).toEqual({ stream0: { audio_enabled: true } });

  await subAudio.click();
  await expect(subAudio).toBeChecked();
  await save.click();
  expect(posts.at(-1)).toEqual({ stream1: { audio_enabled: true } });
  await expect(cards.nth(1).getByLabel("Audio inclusion status", { exact: true })).toHaveValue(/legacy RTSP and recorder owners remain split/);
});

test("per-stream audio keeps the draft and retries a persistence mismatch", async ({ page, request }) => {
  await request.post("/__fixture__/reset");
  await request.post("/__fixture__/scenario", { data: { kind: "raptor" } });

  const audio = [audioControl("settled", false), audioControl("settled", false, false)];
  const posts: unknown[] = [];
  let failPersistence = true;
  await page.route(/\/api\/v1\/prudynt\/stream[01]$/, route => {
    const id = Number(route.request().url().slice(-1));
    return route.fulfill({ json: streamResponse(id, 30, audio[id]!) });
  });
  await page.route("**/api/v1/prudynt", async route => {
    const body = route.request().postDataJSON() as Record<string, Record<string, unknown>>;
    posts.push(body);
    if (failPersistence) {
      failPersistence = false;
      return route.fulfill({ json: { status: "accepted", persistent: false, pending_restart: true } });
    }
    audio[0] = audioControl("pending", true);
    return route.fulfill({ json: { status: "accepted", persistent: true, pending_restart: true } });
  });

  await logIn(page);
  await page.goto("/#/streams");
  const main = page.locator(".stream-card").nth(0);
  const includeAudio = main.getByLabel("Include audio", { exact: true });
  const save = page.getByRole("button", { name: "Save settings", exact: true });
  const pageSection = page.locator("section.page").filter({
    has: page.getByRole("heading", { name: "Video streams", exact: true }),
  });
  const status = pageSection.locator(":scope > [role=status]");

  await expect(status).toBeEmpty();
  await expect(save).toBeEnabled();
  await includeAudio.check();
  await save.click();
  await expect(status).toHaveText(/Stream audio save was not confirmed/);
  await expect(includeAudio).toBeChecked();
  await save.click();
  expect(posts).toEqual([
    { stream0: { audio_enabled: true } },
    { stream0: { audio_enabled: true } },
  ]);
  await expect(status).toHaveText(/Pending stream configuration remains inactive/);
  await expect(main.getByLabel("Audio inclusion status", { exact: true })).toHaveValue(/Saved audio included; active RTSP and recorder policy excludes audio/);
});
