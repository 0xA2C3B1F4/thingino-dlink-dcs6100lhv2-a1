import type { JsonObject, OsdConfig, OsdEntry } from "../../api/contracts";
import { ApiClient } from "../../api/client";
import { routes } from "../../api/routes";
import { decodeOsd } from "../../api/decode";
import { button, element, setMessage, statusMessage, switchField } from "../../app/dom";

export function composeRgba(rgb: string, alpha: number): string {
  if (!/^#[0-9a-fA-F]{6}$/.test(rgb) || !Number.isInteger(alpha) || alpha < 0 || alpha > 255) {
    throw new TypeError("OSD color must use #RRGGBB and an alpha value from 0 to 255.");
  }
  return `${rgb.toLowerCase()}${alpha.toString(16).padStart(2, "0")}`;
}

export function buildOsdUpdate(
  loaded: OsdConfig & JsonObject,
  burnin: OsdConfig["burnin"],
  seiEnabled: boolean,
  entries: Record<string, OsdEntry>,
): JsonObject {
  const next = structuredClone(loaded);
  const previousEntries = next.sei.entries;
  const mergedEntries: Record<string, OsdEntry> = {};
  for (const [name, entry] of Object.entries(entries)) {
    mergedEntries[name] = { ...(previousEntries[name] ?? {} as OsdEntry), ...entry };
  }
  next.burnin = { ...next.burnin, ...burnin };
  next.sei = { ...next.sei, enabled: seiEnabled, entries: mergedEntries };
  return next;
}

export function splitRgba(value: string): { rgb: string; alpha: number } {
  const match = /^#([0-9a-fA-F]{6})([0-9a-fA-F]{2})$/.exec(value);
  if (!match) throw new TypeError("OSD color must use #RRGGBBAA.");
  return { rgb: `#${match[1]!.toLowerCase()}`, alpha: Number.parseInt(match[2]!, 16) };
}

export function withOsdTimezoneToken(format: string, enabled: boolean): string {
  const withoutToken = format.replace(/\s*%Z\b/g, "").replace(/\s{2,}/g, " ").trim();
  if (!enabled) return withoutToken;
  return withoutToken ? `${withoutToken} %Z` : "%Z";
}

interface OsdColorEditor {
  node: HTMLElement;
  input: HTMLInputElement;
  set(value: string): void;
  setDisabled(disabled: boolean): void;
  value(): string;
}

function osdColorEditor(label: string, id: string): OsdColorEditor {
  const row = element("div", { className: "field osd-color-field" });
  const color = element("input", { className: "osd-color-picker", attrs: { type: "color", id: `${id}-picker`, "aria-label": `${label} RGB color` } });
  const alpha = element("input", { className: "osd-alpha", attrs: { type: "range", min: "0", max: "255", step: "1", id: `${id}-alpha`, "aria-label": `${label} alpha` } });
  const input = element("input", { className: "input osd-rgba", attrs: { pattern: "#[0-9A-Fa-f]{8}", id, required: "", "aria-label": `${label} RGBA value` } });
  const alphaValue = element("output", { className: "osd-alpha-value", attrs: { for: alpha.id } });
  const syncFromParts = (): void => {
    input.value = composeRgba(color.value, Number(alpha.value));
    alphaValue.value = alpha.value;
  };
  color.addEventListener("input", syncFromParts);
  alpha.addEventListener("input", syncFromParts);
  input.addEventListener("input", () => {
    if (!input.checkValidity()) return;
    const next = splitRgba(input.value);
    color.value = next.rgb;
    alpha.value = String(next.alpha);
    alphaValue.value = alpha.value;
  });
  const controls = element("div", { className: "osd-color-controls" });
  controls.append(color, alpha, alphaValue, input);
  row.append(element("label", { text: label, attrs: { for: color.id } }), controls);
  return {
    node: row,
    input,
    set(value) {
      const next = splitRgba(value);
      color.value = next.rgb;
      alpha.value = String(next.alpha);
      alphaValue.value = alpha.value;
      input.value = composeRgba(next.rgb, next.alpha);
    },
    setDisabled(disabled) {
      color.disabled = disabled;
      alpha.disabled = disabled;
      input.disabled = disabled;
      row.classList.toggle("is-disabled", disabled);
    },
    value() {
      if (!input.checkValidity()) throw new TypeError(`${label} must use #RRGGBBAA.`);
      return composeRgba(color.value, Number(alpha.value));
    },
  };
}

const DEFAULT_OSD_FORMAT = "%F %T %Z";

export function renderOsdPage(client: ApiClient): { node: HTMLElement; cleanup: () => void } {
  const page = element("section", { className: "page" });
  const header = element("header", { className: "page-heading" });
  header.append(element("span", { className: "eyebrow", text: "Streamer / OSD" }), element("h1", { text: "On-screen display" }), element("p", { text: "Burned-in overlay styling and named structured metadata entries." }));
  const message = statusMessage();
  const form = element("form", { className: "card form-card" });
  const burnEnabled = element("input", { className: "switch-input", attrs: { type: "checkbox", id: "osd-burn-enabled" } });
  const burnFormat = element("input", { className: "input", attrs: { id: "osd-burn-format" } });
  const burnScale = element("select", { className: "input", attrs: { id: "osd-burn-scale" } });
  for (let scale = 0; scale <= 10; scale += 1) burnScale.append(element("option", { text: String(scale), attrs: { value: String(scale) } }));
  const burnTimezone = element("input", { className: "switch-input", attrs: { type: "checkbox", id: "osd-burn-timezone" } });
  const seiEnabled = element("input", { className: "switch-input", attrs: { type: "checkbox", id: "osd-sei-enabled" } });
  const entries = element("div", { className: "osd-entry-list" });
  const addEntry = button("Add entry", "button secondary button-compact");
  const reload = button("Reload", "button secondary");
  const save = element("button", { className: "button primary", text: "Save settings", attrs: { type: "submit" } });
  save.disabled = true;
  const field = (label: string, control: HTMLElement): HTMLElement => {
    const row = element("div", { className: "field" });
    row.append(element("label", { text: label, attrs: { for: control.id } }), control);
    return row;
  };
  const actions = element("div", { className: "form-actions" });
  actions.append(reload, save);
  let fill!: OsdColorEditor;
  let outline!: OsdColorEditor;
  let background!: OsdColorEditor;
  fill = osdColorEditor("Fill color", "osd-fill");
  outline = osdColorEditor("Outline color", "osd-outline");
  background = osdColorEditor("Background color", "osd-background");
  const burnSettings = element("div", { className: "osd-burn-settings" });
  burnSettings.append(
    field("Text format", burnFormat),
    switchField("Include short timezone (%Z)", burnTimezone, "Append the camera's abbreviated timezone to the burn-in text."),
    field("Text scale", burnScale),
    fill.node, outline.node, background.node,
  );
  burnFormat.addEventListener("input", () => {
    burnTimezone.checked = burnFormat.value.includes("%Z");
  });
  burnTimezone.addEventListener("change", () => {
    burnFormat.value = withOsdTimezoneToken(burnFormat.value, burnTimezone.checked);
  });
  const burnToggle = switchField("Burn-in overlay", burnEnabled, "Render OSD text directly into the video.");
  burnToggle.classList.add("osd-burn-toggle");
  const seiToggle = switchField("Publish structured metadata", seiEnabled, "Expose named metadata entries through the video stream.");
  const seiHeading = element("div", { className: "form-section-heading" });
  seiHeading.append(element("h2", { className: "form-section-title", text: "Structured SEI entries" }), addEntry);
  form.append(
    burnToggle,
    burnSettings,
    seiHeading, seiToggle, entries,
    actions,
  );
  page.append(header, message, form);
  let loaded: ReturnType<typeof decodeOsd> | null = null;
  let cancelled = false;
  let entrySerial = 0;

  const addEntryRow = (name = "", type = "text", format = "", position = "10,10"): void => {
    const row = element("fieldset", { className: "osd-entry card" });
    const serial = ++entrySerial;
    const nameInput = element("input", { className: "input osd-entry-name", attrs: { id: `osd-entry-${serial}-name`, value: name, required: "" } });
    const typeInput = element("select", { className: "input osd-entry-type", attrs: { id: `osd-entry-${serial}-type` } });
    for (const option of ["text", "gain", "hostname", "ipaddress", "timestamp", "uptime"]) typeInput.append(element("option", { text: option, attrs: { value: option } }));
    typeInput.value = type;
    const formatInput = element("input", { className: "input osd-entry-format", attrs: { id: `osd-entry-${serial}-format`, value: format } });
    const positionInput = element("input", { className: "input osd-entry-position", attrs: { id: `osd-entry-${serial}-position`, value: position, required: "" } });
    const remove = button("Remove entry", "button danger");
    remove.addEventListener("click", () => row.remove());
    const entryField = (label: string, control: HTMLElement): HTMLElement => {
      const field = element("div", { className: "field" });
      field.append(element("label", { text: label, attrs: { for: control.id } }), control);
      return field;
    };
    row.append(
      element("legend", { text: "Structured entry" }),
      entryField("Name", nameInput),
      entryField("Type", typeInput),
      entryField("Format", formatInput),
      entryField("Position", positionInput),
      remove,
    );
    entries.append(row);
  };

  const setBurnEnabled = (): void => {
    const disabled = !burnEnabled.checked;
    burnSettings.classList.toggle("is-disabled", disabled);
    burnFormat.disabled = disabled;
    burnTimezone.disabled = disabled;
    burnScale.disabled = disabled;
    fill.setDisabled(disabled);
    outline.setDisabled(disabled);
    background.setDisabled(disabled);
  };
  burnEnabled.addEventListener("change", setBurnEnabled);
  setBurnEnabled();

  async function load(): Promise<boolean> {
    save.disabled = true;
    setMessage(message, "Loading OSD settings…");
    try {
      loaded = decodeOsd(await client.json<unknown>(routes.prudynt.domain("osd")));
      if (cancelled) return false;
      burnEnabled.checked = loaded.burnin.enabled;
      burnFormat.value = loaded.burnin.format ?? DEFAULT_OSD_FORMAT;
      burnTimezone.checked = burnFormat.value.includes("%Z");
      burnScale.value = String(loaded.burnin.scale);
      fill.set(loaded.burnin.fill_color);
      outline.set(loaded.burnin.outline_color);
      background.set(loaded.burnin.background_color);
      seiEnabled.checked = loaded.sei.enabled;
      entries.replaceChildren();
      for (const [name, entry] of Object.entries(loaded.sei.entries)) addEntryRow(name, entry.type, entry.format, entry.position);
      setBurnEnabled();
      save.disabled = false;
      setMessage(message);
      return true;
    } catch (error) {
      if (!cancelled) setMessage(message, error instanceof Error ? error.message : "Unable to load OSD settings.", "error");
      return false;
    }
  }
  addEntry.addEventListener("click", () => addEntryRow());
  reload.addEventListener("click", () => void load());
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!loaded || !form.checkValidity()) return form.reportValidity();
    const nextEntries: Record<string, OsdEntry> = {};
    const names = new Set<string>();
    for (const row of entries.querySelectorAll<HTMLElement>(".osd-entry")) {
      const name = row.querySelector<HTMLInputElement>(".osd-entry-name")!.value.trim();
      if (!name || names.has(name)) {
        setMessage(message, "Each OSD entry needs a unique non-empty name.", "error");
        return;
      }
      names.add(name);
      nextEntries[name] = {
        type: row.querySelector<HTMLSelectElement>(".osd-entry-type")!.value as OsdEntry["type"],
        format: row.querySelector<HTMLInputElement>(".osd-entry-format")!.value,
        position: row.querySelector<HTMLInputElement>(".osd-entry-position")!.value,
      };
    }
    const next = buildOsdUpdate(
      loaded,
      { enabled: burnEnabled.checked, format: burnFormat.value, scale: Number(burnScale.value), fill_color: fill.value(), outline_color: outline.value(), background_color: background.value() },
      seiEnabled.checked,
      nextEntries,
    );
    save.disabled = true;
    setMessage(message, "Saving OSD settings…");
    try {
      await client.postJson<unknown>(routes.prudynt.command, { osd: next });
      if (await load() && !cancelled) {
        window.dispatchEvent(new CustomEvent("thingino:config-saved", { detail: { endpoint: routes.prudynt.command, refreshStreamerPreview: true } }));
        setMessage(message, "OSD settings saved.", "success");
      }
    } catch (error) {
      if (!cancelled) setMessage(message, error instanceof Error ? error.message : "Unable to save OSD settings.", "error");
    } finally {
      save.disabled = loaded === null;
    }
  });
  void load();
  return { node: page, cleanup: () => { cancelled = true; } };
}
