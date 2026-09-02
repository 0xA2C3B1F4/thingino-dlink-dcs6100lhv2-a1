import { ControlApi } from "../api/control";
import { element, setMessage, statusMessage } from "../app/dom";

export function renderLogin(api: ControlApi, onSuccess: () => void): HTMLElement {
  const main = element("main", { className: "login-page" });
  const card = element("section", { className: "card login-card" });
  const brand = element("div", { className: "login-brand" });
  brand.append(element("span", { className: "brand-maker", text: "D-Link" }), element("strong", { text: "DCS-6100LHV2" }), element("small", { text: "Thingino · hardware revision A1" }));
  const form = element("form", { className: "login-form" });
  const message = statusMessage();
  const userLabel = element("label", { text: "Username", attrs: { for: "login-username" } });
  const username = element("input", { className: "input", attrs: { id: "login-username", name: "username", autocomplete: "username", required: "", value: "root" } });
  const passwordLabel = element("label", { text: "Password", attrs: { for: "login-password" } });
  const password = element("input", { className: "input", attrs: { id: "login-password", name: "password", type: "password", autocomplete: "current-password", required: "" } });
  const submit = element("button", { className: "button primary", text: "Log in", attrs: { type: "submit" } });
  form.append(userLabel, username, passwordLabel, password, message, submit);
  card.append(brand, element("span", { className: "eyebrow", text: "Local administration" }), element("h1", { text: "Sign in to the camera" }), form);
  main.append(card);

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!form.checkValidity()) return form.reportValidity();
    submit.disabled = true;
    setMessage(message, "Signing in…");
    try {
      await api.login({ username: username.value, password: password.value });
      onSuccess();
    } catch (error) {
      setMessage(message, error instanceof Error ? error.message : "Sign-in failed.", "error");
      password.select();
    } finally {
      submit.disabled = false;
    }
  });
  window.setTimeout(() => password.focus(), 0);
  return main;
}
