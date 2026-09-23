import type { SessionState } from "../api/contracts";
import { button, element } from "./dom";
import { pageDefinition, pages, primaryGroups, sectionNavigation, type PageId } from "./navigation";

export interface Shell {
  main: HTMLElement;
  page: HTMLElement;
  streamerPreviewHost: HTMLElement;
  setActive: (id: PageId) => void;
  setSession: (session: SessionState) => void;
}

export type ThemePreference = "auto" | "light" | "dark";

export function applyThemePreference(preference: ThemePreference): void {
  const resolved = preference === "auto"
    ? (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light")
    : preference;
  document.documentElement.dataset.themePreference = preference;
  document.documentElement.dataset.theme = resolved;
}

function pageLink(id: PageId, label: string, className: string): HTMLAnchorElement {
  return element("a", { className, text: label, attrs: { href: `#/${id}`, "data-page-link": id } });
}

export function buildShell(onLogout: () => void): Shell {
  const root = element("div", { className: "app-shell" });
  const header = element("header", { className: "masthead" });
  const mast = element("div", { className: "masthead-row frame" });
  const brand = pageLink("preview", "", "brand");
  brand.append(
    element("span", { className: "brand-maker", text: "D-Link" }),
    element("span", { className: "brand-device", text: "DCS-6100LHV2" }),
    element("small", { text: "Thingino · hardware revision A1" }),
  );
  const device = element("div", { className: "device-session" });
  const online = element("span", { className: "online", text: "Online" });
  const passwordWarning = element("span", { className: "session-warning", text: "Default password" });
  passwordWarning.hidden = true;
  const theme = button(document.documentElement.dataset.theme === "dark" ? "Light theme" : "Dark theme", "button quiet theme-toggle");
  const logout = button("Log out", "button quiet desktop-logout");
  const menu = button("Menu", "button secondary mobile-menu-button");
  device.append(online, passwordWarning, theme, logout, menu);
  mast.append(brand, device);

  const desktop = element("nav", { className: "desktop-nav frame", attrs: { "aria-label": "Primary" } });
  for (const group of primaryGroups) {
    const target = pages.find((page) => page.group === group)!;
    desktop.append(pageLink(target.id, group, "primary-link"));
  }
  header.append(mast, desktop);

  const main = element("main", { className: "frame", attrs: { id: "main-content", tabindex: "-1" } });
  const sectionLayout = element("div", { className: "section-layout" });
  const sectionNav = element("aside", { className: "card section-nav", attrs: { hidden: "" } });
  const sectionContent = element("div", { className: "section-content" });
  const page = element("div", { className: "page-host" });
  const streamerPreviewHost = element("div", { className: "streamer-preview-host", attrs: { hidden: "" } });
  sectionContent.append(streamerPreviewHost, page);
  sectionLayout.append(sectionNav, sectionContent);
  main.append(sectionLayout);

  const dialog = element("dialog", { className: "mobile-drawer", attrs: { "aria-label": "Navigation" } });
  const drawerHeader = element("div", { className: "drawer-header" });
  drawerHeader.append(element("strong", { text: "DCS-6100LHV2" }), button("Close", "button quiet drawer-close"));
  const drawerNav = element("nav", { className: "drawer-nav", attrs: { "aria-label": "Mobile" } });
  for (const group of primaryGroups) {
    const details = element("details", { className: "drawer-group" });
    if (group === "Preview") {
      details.append(element("summary", { text: group }), pageLink("preview", "Live preview", "drawer-link"));
    } else {
      details.append(element("summary", { text: group }));
      for (const page of pages.filter((entry) => entry.group === group)) details.append(pageLink(page.id, page.label, "drawer-link"));
    }
    drawerNav.append(details);
  }
  const mobileTheme = button(document.documentElement.dataset.theme === "dark" ? "Use light theme" : "Use dark theme", "button secondary drawer-theme");
  const mobileLogout = button("Log out", "button danger drawer-logout");
  dialog.append(drawerHeader, drawerNav, mobileTheme, mobileLogout);
  root.append(header, main, dialog);
  document.querySelector("#app")!.replaceChildren(root);

  const toggleTheme = (): void => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    applyThemePreference(next);
    theme.textContent = next === "dark" ? "Light theme" : "Dark theme";
    mobileTheme.textContent = next === "dark" ? "Use light theme" : "Use dark theme";
    try { localStorage.setItem("dcs6100-theme", next); } catch (_) { /* storage can be unavailable */ }
  };
  theme.addEventListener("click", toggleTheme);
  mobileTheme.addEventListener("click", toggleTheme);
  const closeDrawer = (): void => { if (dialog.open) dialog.close(); };
  menu.addEventListener("click", () => dialog.showModal());
  dialog.querySelector<HTMLButtonElement>(".drawer-close")!.addEventListener("click", closeDrawer);
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) closeDrawer();
  });
  dialog.querySelectorAll("a").forEach((link) => link.addEventListener("click", closeDrawer));
  matchMedia("(min-width: 901px)").addEventListener("change", (event) => {
    if (event.matches) closeDrawer();
  });
  mobileLogout.addEventListener("click", onLogout);
  logout.addEventListener("click", onLogout);

  function setActive(id: PageId): void {
    const definition = pageDefinition(id);
    document.querySelectorAll("[data-page-link]").forEach((link) => {
      const active = link.getAttribute("data-page-link") === id;
      link.classList.toggle("active", active);
      if (active) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    });
    desktop.querySelectorAll(".primary-link").forEach((link) => {
      const page = pages.find((entry) => entry.id === link.getAttribute("data-page-link"));
      link.classList.toggle("active", page?.group === definition.group);
    });
    const groups = sectionNavigation[definition.group];
    sectionNav.replaceChildren();
    sectionNav.hidden = !groups;
    sectionLayout.classList.toggle("has-section-navigation", Boolean(groups));
    if (groups) {
      const selectId = "section-page-select";
      const select = element("select", {
        className: "input section-select",
        attrs: { id: selectId, "aria-label": `${definition.group} page` },
      });
      for (const group of groups) {
        const groupSection = element("section", { className: "section-nav-group" });
        groupSection.append(element("h2", { text: group.label }));
        const options = element("optgroup", { attrs: { label: group.label } });
        for (const pageId of group.pages) {
          const page = pageDefinition(pageId);
          const active = pageId === id;
          const link = pageLink(pageId, page.label, `section-nav-link${active ? " active" : ""}`);
          if (active) link.setAttribute("aria-current", "page");
          groupSection.append(link);
          options.append(element("option", { text: page.label, attrs: { value: pageId } }));
        }
        sectionNav.append(groupSection);
        select.append(options);
      }
      select.value = id;
      select.addEventListener("change", () => { location.hash = `#/${select.value}`; });
      sectionNav.append(
        element("label", { className: "section-select-label", text: `${definition.group} page`, attrs: { for: selectId } }),
        select,
      );
    }
    dialog.querySelectorAll("details").forEach((details) => {
      const active = [...details.querySelectorAll("a")].some((link) => link.getAttribute("data-page-link") === id);
      if (active) details.open = true;
    });
  }

  return {
    main,
    page,
    streamerPreviewHost,
    setActive,
    setSession(session) {
      online.textContent = session.authenticated ? "Online" : "Signed out";
      online.classList.toggle("offline", !session.authenticated);
      online.title = `Client IP: ${session.client_ip}`;
      passwordWarning.hidden = !session.is_default_password;
    },
  };
}
