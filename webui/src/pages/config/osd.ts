import type {
  JsonObject,
  OsdConfig,
  OsdEntry,
  RaptorOsdMetadataConfig,
} from "../../api/contracts";
import { ApiClient } from "../../api/client";
import { buildRaptorOsdMetadataUpdate, decodeRaptorOsdMetadata } from "../../api/osd-metadata";
import { routes } from "../../api/routes";
import { decodeOsd } from "../../api/decode";
import { button, element, setMessage, statusMessage, switchField } from "../../app/dom";

export function decodeOsdForPage(value: unknown): OsdConfig & JsonObject {
  if (typeof value !== "object" || value === null || !("source" in value) || value.source !== "raptor") return decodeOsd(value);
  const root = value as JsonObject;
  for (const key of ["persistent", "enabled", "available", "matches_saved"]) {
    if (typeof root[key] !== "boolean") throw new TypeError("OSD state is incomplete.");
  }
  const fields = root.fields as JsonObject;
  if (!fields || typeof fields !== "object" || Array.isArray(fields) || ["format", "fill_color", "outline_color"].some((key) => typeof fields[key] !== "boolean")) throw new TypeError("OSD capabilities are incomplete.");
  if ((fields.font_size !== undefined && typeof fields.font_size !== "boolean") ||
      (fields.font_size === true && (typeof root.font_size !== "number" || !Number.isInteger(root.font_size) || root.font_size < 16 || root.font_size > 48))) {
    throw new TypeError("OSD font size observation is incomplete.");
  }
  if (fields.enabled !== undefined && typeof fields.enabled !== "boolean") {
    throw new TypeError("OSD text visibility capability is incomplete.");
  }
  if (fields.background_color !== undefined && typeof fields.background_color !== "boolean") {
    throw new TypeError("OSD background capability is incomplete.");
  }
  for (const key of ["fill_alpha", "outline_alpha"]) {
    if (fields[key] !== undefined && typeof fields[key] !== "boolean") throw new TypeError("OSD alpha capability is incomplete.");
  }
  if (typeof root.format !== "string" || root.format.length > 63) throw new TypeError("OSD format is invalid.");
  for (const key of ["fill_color", "outline_color"]) {
    if (typeof root[key] !== "string" || !/^#[0-9a-fA-F]{8}$/i.test(root[key] as string)) throw new TypeError("OSD color is invalid.");
  }
  const backgroundColor = fields.background_color === true ? root.background_color : "#00000000";
  if (typeof backgroundColor !== "string" || !/^#[0-9a-fA-F]{8}$/i.test(backgroundColor)) {
    throw new TypeError("OSD background color is invalid.");
  }
  return { source: "raptor", persistent: root.persistent as boolean, available: root.available as boolean,
    matches_saved: root.matches_saved as boolean, editable: fields, font_size: fields.font_size === true ? root.font_size! : null,
    burnin: { enabled: root.enabled as boolean, format: root.format, scale: 0,
      fill_color: root.fill_color as string, outline_color: root.outline_color as string, background_color: backgroundColor },
    sei: { enabled: false, entries: {} } };
}

export function buildRaptorOsdUpdate(loaded: OsdConfig & JsonObject, format: string, fill: string, outline: string, fontSize?: number, enabled?: boolean, background?: string): JsonObject {
  if (loaded.source !== "raptor" || loaded.available !== true || loaded.persistent !== true) throw new TypeError("OSD settings are unavailable. Reload before saving.");
  const editable = loaded.editable as JsonObject;
  const values: JsonObject = {};
  if (editable.enabled === true) {
    if (typeof enabled !== "boolean") throw new TypeError("Choose whether the text overlay is enabled.");
    values.enabled = enabled;
  }
  for (const [key, value] of [["format", format], ["fill_color", fill], ["outline_color", outline]] as const) {
    if (editable[key] === true) values[key] = value;
  }
  if (editable.background_color === true) {
    if (typeof background !== "string") throw new TypeError("Choose an OSD background color.");
    values.background_color = background;
  }
  if (editable.font_size === true) {
    if (typeof fontSize !== "number" || !Number.isInteger(fontSize) || fontSize < 16 || fontSize > 48) {
      throw new TypeError("Choose an OSD font size from 16 to 48 pixels.");
    }
    values.font_size = fontSize;
  }
  if (!Object.keys(values).length) throw new TypeError("No OSD text settings are available.");
  return { osd: values };
}

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
  setOpaqueOnly(opaque: boolean): void;
  value(): string;
}

function osdColorEditor(label: string, id: string): OsdColorEditor {
  const row = element("div", { className: "field osd-color-field" });
  const color = element("input", { className: "osd-color-picker", attrs: { type: "color", id: `${id}-picker`, "aria-label": `${label} RGB color` } });
  const alpha = element("input", { className: "osd-alpha", attrs: { type: "range", min: "0", max: "255", step: "1", id: `${id}-alpha`, "aria-label": `${label} alpha` } });
  const input = element("input", { className: "input osd-rgba", attrs: { pattern: "#[0-9A-Fa-f]{8}", id, required: "", "aria-label": `${label} RGBA value` } });
  let opaqueOnly = false;
  let disabledState = false;
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
    setOpaqueOnly(opaque) {
      opaqueOnly = opaque;
      alpha.disabled = opaque || disabledState;
      input.pattern = opaque ? "#[0-9A-Fa-f]{6}[fF]{2}" : "#[0-9A-Fa-f]{8}";
    },
    setDisabled(disabled) {
      disabledState = disabled;
      color.disabled = disabled;
      alpha.disabled = disabled || opaqueOnly;
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
  const fontSize = element("input", { className: "input", attrs: { id: "osd-font-size", type: "number", min: "16", max: "48", step: "1", required: "" } });
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
  const scaleField = field("Text scale", burnScale);
  const sizeField = field("Font size (main-stream pixels)", fontSize);
  sizeField.append(element("p", { className: "muted", text: "16–48 pixels. Substream text scales with its resolution, with a 12-pixel minimum. Long text must fit the reserved overlay area." }));
  sizeField.hidden = true;
  burnSettings.append(
    field("Text format", burnFormat),
    switchField("Include short timezone (%Z)", burnTimezone, "Append the camera's abbreviated timezone to the burn-in text."),
    scaleField, sizeField,
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
  let loaded: ReturnType<typeof decodeOsdForPage> | null = null;
  let metadata: RaptorOsdMetadataConfig | null = null;
  const scopeNote = element("p", { className: "muted" });
  scopeNote.hidden = true;
  form.prepend(scopeNote);
  let cancelled = false;
  let entrySerial = 0;

  const addEntryRow = (
    name = "",
    type = "text",
    format = "",
    position = "10,10",
    available = true,
    unavailableReason = "",
  ): void => {
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
    if (!available) {
      row.append(element("p", {
        className: "muted",
        text: `Value unavailable: ${unavailableReason || "producer unavailable"}. The entry remains configurable.`,
      }));
    }
    entries.append(row);
  };

  const setMetadataEnabled = (enabled: boolean): void => {
    seiEnabled.disabled = !enabled;
    addEntry.disabled = !enabled;
    const controls = entries.querySelectorAll<HTMLInputElement | HTMLSelectElement>(
      "input, select, button",
    );
    for (const control of controls) {
      control.disabled = !enabled;
    }
  };

  const setBurnEnabled = (): void => {
    const raptor = loaded?.source === "raptor";
    const editable = (loaded?.editable ?? {}) as JsonObject;
    const disabled = !burnEnabled.checked || (raptor && loaded?.available !== true);
    burnSettings.classList.toggle("is-disabled", disabled);
    burnFormat.disabled = disabled || (raptor && editable.format !== true);
    burnTimezone.disabled = burnFormat.disabled;
    burnScale.disabled = disabled || raptor;
    fontSize.disabled = disabled || !raptor || editable.font_size !== true;
    fill.setDisabled(disabled || (raptor && editable.fill_color !== true));
    outline.setDisabled(disabled || (raptor && editable.outline_color !== true));
    background.setDisabled(disabled || (raptor && editable.background_color !== true));
  };
  burnEnabled.addEventListener("change", setBurnEnabled);
  setBurnEnabled();

  async function load(): Promise<boolean> {
    save.disabled = true;
    metadata = null;
    setMessage(message, "Loading OSD settings…");
    try {
      const [hardwareResult, metadataResult] = await Promise.allSettled([
        client.json<unknown>(routes.prudynt.domain("osd")),
        client.json<unknown>(routes.config.osdMetadata),
      ]);
      if (cancelled) return false;
      let hardwareProblem = "";
      if (hardwareResult.status === "fulfilled") {
        try {
          loaded = decodeOsdForPage(hardwareResult.value);
        } catch (error) {
          hardwareProblem = error instanceof Error
            ? error.message
            : "Hardware OSD is unavailable.";
          loaded = null;
        }
      } else {
        hardwareProblem = hardwareResult.reason instanceof Error
          ? hardwareResult.reason.message
          : "Hardware OSD is unavailable.";
        loaded = null;
      }
      let metadataProblem = "";
      if (metadataResult.status === "fulfilled") {
        try {
          metadata = decodeRaptorOsdMetadata(metadataResult.value);
        } catch (error) {
          metadataProblem = error instanceof Error
            ? error.message
            : "Structured metadata is unavailable.";
        }
      } else {
        metadataProblem = metadataResult.reason instanceof Error
          ? metadataResult.reason.message
          : "Structured metadata is unavailable.";
      }
      if (!loaded && metadata) {
        loaded = decodeOsdForPage({
          source: "raptor",
          persistent: true,
          enabled: false,
          available: false,
          matches_saved: false,
          fields: { format: false, fill_color: false, outline_color: false },
          format: DEFAULT_OSD_FORMAT,
          fill_color: "#ffffffff",
          outline_color: "#000000ff",
        });
      }
      if (!loaded) {
        throw hardwareResult.status === "rejected"
          ? hardwareResult.reason
          : new TypeError("Unable to load OSD settings.");
      }
      const raptor = loaded.source === "raptor" || metadata !== null;
      if (!raptor) {
        metadata = null;
        metadataProblem = "";
      }
      burnEnabled.disabled = raptor && (loaded.available !== true || loaded.persistent !== true ||
        (loaded.editable as JsonObject).enabled !== true);
      fill.setOpaqueOnly(raptor && (loaded.editable as JsonObject).fill_alpha !== true);
      outline.setOpaqueOnly(raptor && (loaded.editable as JsonObject).outline_alpha !== true);
      scopeNote.hidden = !raptor;
      const metadataAvailable = metadata?.saved.available === true;
      const hardwareStatus = hardwareProblem
        ? ` ${hardwareProblem}`
        : loaded.available !== true
          ? " Text editing is currently unavailable."
          : loaded.matches_saved === false
            ? " Current text settings differ from the saved settings."
            : "";
      const metadataStatus = metadataProblem
        ? ` ${metadataProblem}`
        : metadata && !metadataAvailable
          ? " Saved structured metadata is unavailable; reload after repairing its configuration."
          : metadata && (
            !metadata.confirmed ||
            !metadata.published.fresh ||
            !metadata.published.matches_saved
          )
            ? " Structured metadata is saved, but its current publication is not confirmed."
            : "";
      scopeNote.textContent = raptor
        ? `Hardware text and named stream metadata are saved and verified independently. The text switch does not change Privacy.${hardwareStatus}${metadataStatus}`
        : "";
      burnEnabled.checked = loaded.burnin.enabled;
      burnFormat.value = loaded.burnin.format ?? DEFAULT_OSD_FORMAT;
      burnTimezone.checked = burnFormat.value.includes("%Z");
      burnScale.value = raptor ? "" : String(loaded.burnin.scale);
      scaleField.hidden = raptor;
      sizeField.hidden = !raptor;
      fontSize.value = raptor && typeof loaded.font_size === "number" ? String(loaded.font_size) : "";
      fill.set(loaded.burnin.fill_color);
      outline.set(loaded.burnin.outline_color);
      background.set(loaded.burnin.background_color);
      seiEnabled.indeterminate = raptor && !metadataAvailable;
      seiEnabled.checked = raptor ? metadata?.saved.enabled === true : loaded.sei.enabled;
      entries.replaceChildren();
      if (raptor && metadataAvailable) {
        for (const entry of metadata!.saved.entries) {
          addEntryRow(
            entry.name,
            entry.type,
            entry.format,
            entry.position,
            entry.available,
            entry.unavailable_reason,
          );
        }
      } else if (!raptor) {
        for (const [name, entry] of Object.entries(loaded.sei.entries)) addEntryRow(name, entry.type, entry.format, entry.position);
      }
      setMetadataEnabled(!raptor || metadataAvailable);
      setBurnEnabled();
      const hardwareEditable = loaded.available === true &&
        loaded.persistent === true &&
        Object.values(loaded.editable as JsonObject).some((value) => value === true);
      save.disabled = raptor && !hardwareEditable && !metadataAvailable;
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
    const next = loaded.source === "raptor" ? null : buildOsdUpdate(
      loaded,
      { enabled: burnEnabled.checked, format: burnFormat.value, scale: Number(burnScale.value), fill_color: fill.value(), outline_color: outline.value(), background_color: background.value() },
      seiEnabled.checked,
      nextEntries,
    );
    save.disabled = true;
    setMessage(message, "Saving OSD settings…");
    const savedSections: string[] = [];
    try {
      if (loaded.source === "raptor") {
        let hardwarePayload: JsonObject | null = null;
        let metadataPayload: JsonObject | null = null;
        const endpoints: string[] = [];
        const hardwareEditable = loaded.available === true &&
          loaded.persistent === true &&
          Object.values(loaded.editable as JsonObject).some((value) => value === true);
        if (hardwareEditable) {
          hardwarePayload = buildRaptorOsdUpdate(
            loaded,
            burnFormat.value,
            fill.value(),
            outline.value(),
            Number(fontSize.value),
            burnEnabled.checked,
            background.value(),
          );
          endpoints.push(routes.prudynt.command);
        }
        if (metadata?.saved.available === true) {
          const values = Object.entries(nextEntries).map(([name, entry]) => ({ name, ...entry }));
          metadataPayload = buildRaptorOsdMetadataUpdate(seiEnabled.checked, values);
          endpoints.push(routes.config.osdMetadata);
        }
        setMessage(message, "Saving OSD settings…");
        let hardwareSaved = false;
        let metadataSaved = false;
        let metadataAcknowledgedId = "";
        if (hardwarePayload) {
          const response = await client.postJson<unknown>(routes.prudynt.command, hardwarePayload);
          const persistent = typeof response === "object" &&
            response !== null &&
            "persistent" in response &&
            response.persistent === true;
          if (!persistent) {
            throw new TypeError(
              "Hardware OSD saving was not confirmed. Reload and explicitly retry saving.",
            );
          }
          hardwareSaved = true;
          savedSections.push("hardware OSD");
        }
        if (metadataPayload) {
          const response = decodeRaptorOsdMetadata(
            await client.postJson<unknown>(routes.config.osdMetadata, metadataPayload),
          );
          const publicationConfirmed = response.confirmed &&
            response.published.fresh &&
            response.published.matches_saved &&
            response.published.status === 0;
          if (!publicationConfirmed) {
            throw new TypeError(
              "Named metadata publication was not confirmed. Reload and explicitly retry saving.",
            );
          }
          metadataSaved = true;
          metadataAcknowledgedId = response.saved.id;
          savedSections.push("named metadata");
        }
        const reloaded = await load();
        const hardwareConfirmed = !hardwareSaved || loaded?.matches_saved === true;
        const metadataConfirmed = !metadataSaved || (
          metadata?.confirmed === true &&
          metadata.saved.id === metadataAcknowledgedId &&
          metadata.published.id === metadataAcknowledgedId &&
          metadata.published.fresh &&
          metadata.published.matches_saved &&
          metadata.published.status === 0
        );
        if (!reloaded || !hardwareConfirmed || !metadataConfirmed) {
          throw new TypeError("Some OSD settings may have changed, but complete readback was not confirmed. Reload and explicitly retry saving.");
        }
        if (!cancelled) {
          window.dispatchEvent(new CustomEvent("thingino:config-saved", {
            detail: { endpoint: endpoints.join(","), refreshStreamerPreview: true },
          }));
          setMessage(message, "OSD settings saved.", "success");
        }
        return;
      }
      await client.postJson<unknown>(routes.prudynt.command, { osd: next });
      if (await load() && !cancelled) {
        window.dispatchEvent(new CustomEvent("thingino:config-saved", { detail: { endpoint: routes.prudynt.command, refreshStreamerPreview: true } }));
        setMessage(message, "OSD settings saved.", "success");
      }
    } catch (error) {
      if (!cancelled) {
        const reason = error instanceof Error ? error.message : "Unable to save OSD settings.";
        const prefix = savedSections.length
          ? `${savedSections.join(" and ")} saved, but the complete operation was not confirmed. `
          : "";
        setMessage(message, `${prefix}${reason}`, "error");
      }
    } finally {
      const hardwareEditable = loaded?.source === "raptor" &&
        loaded.available === true &&
        loaded.persistent === true &&
        Object.values(loaded.editable as JsonObject).some((value) => value === true);
      save.disabled = loaded === null || (
        loaded.source === "raptor" &&
        !hardwareEditable &&
        metadata?.saved.available !== true
      );
    }
  });
  void load();
  return { node: page, cleanup: () => { cancelled = true; } };
}
