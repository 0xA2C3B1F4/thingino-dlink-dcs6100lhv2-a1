import type { JsonObject, JsonValue } from "../api/contracts";
import { ApiClient } from "../api/client";
import { button, element, setMessage, statusMessage, switchField } from "./dom";

export type FieldType = "checkbox" | "number" | "password" | "search" | "select" | "text" | "time";

export interface FieldOption {
  label: string;
  value: string;
}

export interface FieldSpec {
  path: string;
  label: string;
  type: FieldType;
  card?: string;
  section?: string;
  description?: string;
  min?: number;
  max?: number;
  step?: number;
  pattern?: string;
  maxLength?: number;
  required?: boolean;
  options?: FieldOption[];
  optionsFrom?: string;
  optionsFilter?: (options: FieldOption[], currentValue: JsonValue | undefined) => FieldOption[];
  valueType?: "number" | "string";
  placeholder?: string;
  readOnly?: boolean;
  writeOnlySecret?: boolean;
  enabledWhen?: { path: string; equals: boolean } | ((read: (path: string) => JsonValue | undefined) => boolean);
  visibleWhen?: { path: string; equals: boolean };
  rangeFrom?: string;
}

export interface ConfigFormSpec {
  title: string;
  eyebrow: string;
  description: string;
  endpoint: string;
  fieldIdPrefix?: string;
  saveEndpoint?: string;
  load?: (client: ApiClient) => Promise<JsonObject>;
  decode?: (value: unknown) => JsonObject;
  fields: FieldSpec[];
  groupSections?: boolean;
  collapsibleSections?: readonly string[];
  saveLabel?: string | ((loaded: JsonObject) => string);
  successMessage?: (loaded: JsonObject) => string;
  loadTransform?: (value: JsonObject) => JsonObject;
  saveTransform?: (value: JsonObject, loaded: JsonObject) => JsonObject;
  validate?: (value: JsonObject, loaded: JsonObject) => string | undefined;
  save?: (client: ApiClient, value: JsonObject, loaded: JsonObject) => Promise<void>;
  onLoaded?: (value: JsonObject) => void;
  refreshStreamerPreview?: boolean;
  cardGroups?: boolean;
  cardSummary?: (card: string, value: JsonObject) => string;
  cardState?: (card: string, value: JsonObject) => { label: string; state: "enabled" | "inactive" | "disabled" };
  cardFocusStreams?: boolean;
}

function getPath(root: JsonObject, path: string): JsonValue | undefined {
  let value: JsonValue | undefined = root;
  for (const key of path.split(".")) {
    if (typeof value !== "object" || value === null || Array.isArray(value)) return undefined;
    value = value[key];
  }
  return value;
}

function setPath(root: JsonObject, path: string, value: JsonValue): void {
  const parts = path.split(".");
  let cursor: JsonObject = root;
  parts.forEach((key, index) => {
    if (index === parts.length - 1) cursor[key] = value;
    else {
      const next = cursor[key];
      if (typeof next !== "object" || next === null || Array.isArray(next)) cursor[key] = {};
      cursor = cursor[key] as JsonObject;
    }
  });
}

function deletePath(root: JsonObject, path: string): void {
  const parts = path.split(".");
  let cursor: JsonObject = root;
  for (const key of parts.slice(0, -1)) {
    const next = cursor[key];
    if (typeof next !== "object" || next === null || Array.isArray(next)) return;
    cursor = next;
  }
  delete cursor[parts.at(-1)!];
}

function cloneObject(value: JsonObject): JsonObject {
  return structuredClone(value);
}

export function buildConfigPayload(
  loaded: JsonObject,
  values: ReadonlyMap<string, JsonValue | undefined>,
  fields: readonly FieldSpec[],
): JsonObject {
  const body = cloneObject(loaded);
  for (const field of fields) {
    if (field.readOnly) continue;
    const value = values.get(field.path);
    if (field.writeOnlySecret && (value === undefined || value === "" || value === null)) {
      deletePath(body, field.path);
      continue;
    }
    if (value !== undefined) setPath(body, field.path, value);
  }
  return body;
}

function inputFor(field: FieldSpec, prefix = "field"): HTMLInputElement | HTMLSelectElement {
  const id = `${prefix}-${field.path.replaceAll(".", "-")}`;
  if (field.type === "select") {
    const select = element("select", { className: "input", attrs: { id, name: field.path } });
    for (const option of field.options ?? []) {
      select.append(element("option", { text: option.label, attrs: { value: option.value } }));
    }
    return select;
  }
  const input = element("input", {
    className: field.type === "checkbox" ? "switch-input" : "input",
    attrs: { id, name: field.path, type: field.type },
  });
  if (field.type === "search" && field.optionsFrom) {
    input.setAttribute("list", `${id}-options`);
    input.setAttribute("autocomplete", "off");
  }
  if (field.required) input.required = true;
  if (field.min !== undefined) input.min = String(field.min);
  if (field.max !== undefined) input.max = String(field.max);
  if (field.step !== undefined) input.step = String(field.step);
  if (field.pattern) input.pattern = field.pattern;
  if (field.maxLength !== undefined) input.maxLength = field.maxLength;
  if (field.placeholder) input.placeholder = field.placeholder;
  if (field.readOnly && input instanceof HTMLInputElement) {
    input.readOnly = true;
    if (input.type === "checkbox") input.disabled = true;
  }
  return input;
}

function writeValue(input: HTMLInputElement | HTMLSelectElement, value: JsonValue | undefined): void {
  if (input instanceof HTMLInputElement && input.type === "checkbox") {
    input.checked = value === true || value === 1;
    input.indeterminate = value === null;
  }
  else input.value = value === undefined || value === null ? "" : String(value);
}

function optionValues(value: JsonValue | undefined): FieldOption[] {
  if (typeof value === "string") return value.split(",").map((item) => item.trim()).filter(Boolean).map((item) => ({ label: item, value: item }));
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    if (typeof item === "string" || typeof item === "number") return [{ label: String(item), value: String(item) }];
    if (typeof item !== "object" || item === null || Array.isArray(item)) return [];
    const candidate = item.path ?? item.mount ?? item.name ?? item.device;
    return typeof candidate === "string" ? [{ label: candidate, value: candidate }] : [];
  });
}

function readValue(input: HTMLInputElement | HTMLSelectElement, field: FieldSpec): JsonValue | undefined {
  if (input instanceof HTMLInputElement && input.type === "checkbox") return input.indeterminate ? undefined : input.checked;
  if (field.type === "number") return input.value === "" ? undefined : Number(input.value);
  if (field.type === "select" && field.valueType === "number") return input.value === "" ? undefined : Number(input.value);
  return input.value;
}

export function renderConfigForm(client: ApiClient, spec: ConfigFormSpec): { node: HTMLElement; cleanup: () => void } {
  const section = element("section", { className: "page" });
  const heading = element("header", { className: "page-heading" });
  heading.append(
    element("span", { className: "eyebrow", text: spec.eyebrow }),
    element("h1", { text: spec.title }),
    element("p", { text: spec.description }),
  );
  const message = statusMessage();
  const form = element("form", {
    className: `card form-card${spec.groupSections ? " grouped-form" : ""}${spec.cardGroups ? " card-groups" : ""}`,
    attrs: { novalidate: "" },
  });
  const fields = new Map<string, {
    input: HTMLInputElement | HTMLSelectElement;
    row: HTMLElement;
    optionsList?: HTMLDataListElement;
    spec: FieldSpec;
  }>();
  let loadedRaw: JsonObject = {};
  let loadedView: JsonObject = {};
  let loadedSuccessfully = false;
  let cancelled = false;

  const refreshDependencies = (): void => {
    const read = (path: string): JsonValue | undefined => {
      const field = fields.get(path);
      return field ? readValue(field.input, field.spec) : getPath(loadedView, path);
    };
    for (const field of fields.values()) {
      const condition = field.spec.enabledWhen;
      if (!condition) continue;
      const active = typeof condition === "function" ? condition(read) : read(condition.path) === condition.equals;
      field.input.disabled = !active;
    }
  };

  let currentCard = "";
  let currentSection = "";
  let fieldContainer: HTMLElement = form;
  let sectionParent: HTMLElement = form;
  const cardHeaders = new Map<string, { state: HTMLElement; controls: HTMLElement; summary: HTMLElement }>();
  const resolutionRows = new Map<string, HTMLElement>();
  for (const field of spec.fields) {
    if (spec.cardGroups && field.card && field.card !== currentCard) {
      currentCard = field.card;
      currentSection = "";
      const card = element("fieldset", { className: "stream-card", attrs: { "data-stream-card": currentCard } });
      const header = element("div", { className: "stream-card-header" });
      const state = element("span", { className: "stream-card-state", text: "Loading" });
      const controls = element("div", { className: "stream-card-controls" });
      const summary = element("p", { className: "stream-card-summary", text: "Reading stream settings…" });
      header.append(
        element("h2", { text: currentCard === "stream0" ? "Main stream · CH0" : "Sub stream · CH1" }),
        state,
        controls,
        summary,
      );
      card.append(header);
      form.append(card);
      fieldContainer = card;
      sectionParent = card;
      cardHeaders.set(currentCard, { state, controls, summary });
      if (spec.cardFocusStreams && (currentCard === "stream0" || currentCard === "stream1")) {
        const stream = currentCard === "stream0" ? 0 : 1;
        const focus = (): void => { window.dispatchEvent(new CustomEvent("thingino:stream-card-focus", { detail: { stream } })); };
        card.addEventListener("focusin", focus);
        card.addEventListener("click", focus);
      }
    }
    if (field.section && field.section !== currentSection) {
      currentSection = field.section;
      if (spec.groupSections) {
        const collapsible = spec.collapsibleSections?.includes(field.section) === true;
        const group = element(collapsible ? "details" : "section", { className: "form-section-card" });
        group.append(element(
          collapsible ? "summary" : "h2",
          collapsible ? { text: field.section } : { className: "form-section-title", text: field.section },
        ));
        sectionParent.append(group);
        fieldContainer = group;
      } else {
        form.append(element("h2", { className: "form-section-title", text: field.section }));
      }
    }
    const input = inputFor(field, spec.fieldIdPrefix);
    const optionsList = field.type === "search" && field.optionsFrom
      ? element("datalist", { attrs: { id: `${input.id}-options` } })
      : undefined;
    input.addEventListener("change", refreshDependencies);
    const row = field.type === "checkbox"
      ? switchField(field.label, input as HTMLInputElement, field.description)
      : element("div", { className: "field" });
    fields.set(field.path, { input, row, ...(optionsList ? { optionsList } : {}), spec: field });
    if (field.type !== "checkbox") {
      const label = element("label", { text: field.label, attrs: { for: input.id } });
      row.append(label, input);
      if (optionsList) row.append(optionsList);
      if (field.description) row.append(element("small", { text: field.description }));
    }
    if (spec.cardGroups && field.card && field.path === `${field.card}.enabled`) {
      const header = cardHeaders.get(field.card);
      if (header) header.controls.append(row);
    } else if (spec.cardGroups && field.card && field.path === `${field.card}.width`) {
      const resolution = element("div", { className: "stream-resolution-field" });
      const dimensions = element("div", { className: "stream-resolution-inputs" });
      const dimension = element("div", { className: "stream-resolution-dimension" });
      dimension.append(element("label", { text: "Width", attrs: { for: input.id } }), input);
      dimensions.append(dimension, element("span", { className: "stream-resolution-times", text: "×", attrs: { "aria-hidden": "true" } }));
      resolution.append(element("span", { className: "stream-resolution-label", text: "Resolution" }), dimensions);
      fieldContainer.append(resolution);
      resolutionRows.set(field.card, dimensions);
    } else if (spec.cardGroups && field.card && field.path === `${field.card}.height`) {
      const dimensions = resolutionRows.get(field.card);
      if (dimensions) {
        const dimension = element("div", { className: "stream-resolution-dimension" });
        dimension.append(element("label", { text: "Height", attrs: { for: input.id } }), input);
        dimensions.append(dimension);
      } else fieldContainer.append(row);
    } else fieldContainer.append(row);
  }

  const actions = element("div", { className: "form-actions" });
  const reload = button("Reload", "button secondary");
  const save = element("button", {
    className: "button primary",
    text: typeof spec.saveLabel === "string" ? spec.saveLabel : "Save settings",
    attrs: { type: "submit" },
  });
  save.disabled = true;
  actions.append(reload, save);
  form.append(actions);
  section.append(heading, message, form);

  async function load(): Promise<boolean> {
    loadedSuccessfully = false;
    save.disabled = true;
    setMessage(message, "Loading settings…");
    form.setAttribute("aria-busy", "true");
    try {
      if (spec.load) loadedRaw = await spec.load(client);
      else {
        if (!spec.decode) throw new TypeError(`No decoder is configured for ${spec.title}.`);
        loadedRaw = spec.decode(await client.json<unknown>(spec.endpoint));
      }
      if (cancelled) return false;
      loadedView = spec.loadTransform ? spec.loadTransform(loadedRaw) : cloneObject(loadedRaw);
      for (const [path, field] of fields) {
        if (field.spec.visibleWhen) {
          field.row.hidden = getPath(loadedView, field.spec.visibleWhen.path) !== field.spec.visibleWhen.equals;
        }
        if (field.spec.optionsFrom && (field.input instanceof HTMLSelectElement || field.optionsList)) {
          let currentOptions = optionValues(getPath(loadedView, field.spec.optionsFrom));
          const currentValue = getPath(loadedView, path);
          if ((typeof currentValue === "string" || typeof currentValue === "number") && !currentOptions.some((option) => option.value === String(currentValue))) {
            currentOptions.unshift({ label: `${String(currentValue)} (current)`, value: String(currentValue) });
          }
          currentOptions = field.spec.optionsFilter?.(currentOptions, currentValue) ?? currentOptions;
          const optionNodes = currentOptions.map((option) => element("option", { text: option.label, attrs: { value: option.value } }));
          if (field.input instanceof HTMLSelectElement) field.input.replaceChildren(...optionNodes);
          else field.optionsList!.replaceChildren(...optionNodes);
        }
        if (field.spec.rangeFrom && field.input instanceof HTMLInputElement) {
          const metadata = getPath(loadedView, field.spec.rangeFrom);
          if (typeof metadata !== "object" || metadata === null || Array.isArray(metadata)) {
            throw new TypeError(`Missing range metadata for ${path}.`);
          }
          const supported = metadata.supported === true && metadata.available !== false;
          field.input.disabled = !supported;
          if (typeof metadata.min === "number") field.input.min = String(metadata.min);
          if (typeof metadata.max === "number") field.input.max = String(metadata.max);
          field.input.title = supported ? `${metadata.min}–${metadata.max}` : "Unavailable on this device";
        }
        writeValue(field.input, getPath(loadedView, path));
      }
      refreshDependencies();
      for (const [card, header] of cardHeaders) {
        const value = getPath(loadedView, `${card}.enabled`);
        const state = spec.cardState?.(card, loadedView) ?? {
          label: value === true ? "Enabled" : value === false ? "Disabled" : "Unavailable",
          state: value === true ? "enabled" as const : "disabled" as const,
        };
        header.state.textContent = state.label;
        header.state.dataset.state = state.state;
        header.summary.textContent = spec.cardSummary?.(card, loadedView) ?? "";
      }
      spec.onLoaded?.(loadedView);
      if (typeof spec.saveLabel === "function") save.textContent = spec.saveLabel(loadedRaw);
      loadedSuccessfully = true;
      save.disabled = false;
      setMessage(message);
      return true;
    } catch (error) {
      if (!cancelled) setMessage(message, error instanceof Error ? error.message : "Unable to load settings.", "error");
      return false;
    } finally {
      form.removeAttribute("aria-busy");
    }
  }

  reload.addEventListener("click", () => void load());
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!loadedSuccessfully) {
      setMessage(message, "Wait for the complete camera settings before saving.", "error");
      return;
    }
    if (!form.checkValidity()) {
      form.reportValidity();
      setMessage(message, "Check the highlighted fields before saving.", "error");
      return;
    }
    const values = new Map<string, JsonValue | undefined>();
    for (const [path, field] of fields) values.set(path, field.input.disabled && !field.spec.readOnly ? undefined : readValue(field.input, field.spec));
    setMessage(message, "Saving settings…");
    save.disabled = true;
    try {
      const fullValue = buildConfigPayload(loadedView, values, spec.fields);
      const body = spec.saveTransform ? spec.saveTransform(fullValue, loadedRaw) : fullValue;
      const validationError = spec.validate?.(body, loadedRaw);
      if (validationError) throw new TypeError(validationError);
      if (spec.save) await spec.save(client, body, loadedRaw);
      else await client.postJson<unknown>(spec.saveEndpoint ?? spec.endpoint, body);
      if (cancelled) return;
      if (await load() && !cancelled) {
        window.dispatchEvent(new CustomEvent("thingino:config-saved", {
          detail: { endpoint: spec.saveEndpoint ?? spec.endpoint, refreshStreamerPreview: spec.refreshStreamerPreview === true },
        }));
        setMessage(message, spec.successMessage?.(loadedRaw) ?? "Settings saved.", "success");
      }
    } catch (error) {
      if (!cancelled) setMessage(message, error instanceof Error ? error.message : "Unable to save settings.", "error");
      // A checked save may update capability/persistence metadata before
      // throwing. Refresh that metadata without replacing the user's inputs.
      loadedView = spec.loadTransform ? spec.loadTransform(loadedRaw) : cloneObject(loadedRaw);
      refreshDependencies();
      if (typeof spec.saveLabel === "function") save.textContent = spec.saveLabel(loadedRaw);
      for (const [card, header] of cardHeaders) {
        const state = spec.cardState?.(card, loadedView);
        if (state) {
          header.state.textContent = state.label;
          header.state.dataset.state = state.state;
        }
        header.summary.textContent = spec.cardSummary?.(card, loadedView) ?? "";
      }
    } finally {
      save.disabled = !loadedSuccessfully;
    }
  });

  void load();
  return { node: section, cleanup: () => { cancelled = true; } };
}
