export function element<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  options: { className?: string; text?: string; attrs?: Record<string, string>; children?: readonly Node[] } = {},
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (options.className) node.className = options.className;
  if (options.text !== undefined) node.textContent = options.text;
  for (const [name, value] of Object.entries(options.attrs ?? {})) node.setAttribute(name, value);
  if (options.children) node.append(...options.children);
  return node;
}

export function clear(node: Element): void {
  node.replaceChildren();
}

export function button(label: string, className = "button secondary"): HTMLButtonElement {
  return element("button", { className, text: label, attrs: { type: "button" } });
}

/**
 * Render the shared compact switch pattern used by settings forms.  Keeping
 * the copy and the control in separate columns makes the switch easy to find
 * on both desktop and narrow screens while retaining a real label association.
 */
export function switchField(label: string, input: HTMLInputElement, description?: string): HTMLDivElement {
  const row = element("div", { className: "field switch-field" });
  const copy = element("div", { className: "switch-copy" });
  copy.append(element("span", { className: "switch-label", text: label }));
  if (description) copy.append(element("small", { text: description }));
  input.setAttribute("aria-label", label);
  const control = element("label", { className: "switch-control", attrs: { for: input.id } });
  control.append(input);
  row.append(copy, control);
  return row;
}

export function statusMessage(): HTMLDivElement {
  return element("div", { className: "message", attrs: { role: "status", "aria-live": "polite" } });
}

export function setMessage(node: HTMLElement, message = "", variant: "error" | "success" | "info" = "info"): void {
  node.textContent = message;
  node.dataset.variant = variant;
  node.hidden = !message;
}

export function formatKiB(value: number | undefined): string {
  if (!Number.isFinite(value)) return "Not reported";
  if ((value ?? 0) >= 1024 * 1024) return `${((value ?? 0) / (1024 * 1024)).toFixed(1)} GiB`;
  if ((value ?? 0) >= 1024) return `${((value ?? 0) / 1024).toFixed(1)} MiB`;
  return `${value} KiB`;
}
