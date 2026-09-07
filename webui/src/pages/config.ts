import { ApiClient } from "../api/client";
import { renderConfigForm, type ConfigFormSpec } from "../app/forms";
import type { PageId } from "../app/navigation";
import { network, time, addWifiScan, addTimeSync } from "./config/network-time";
import { access, webui, admin, logging, addWebuiSecurity } from "./config/access";
import { daynight, renderGpioPage } from "./config/hardware";
import { audio, imaging, streams } from "./config/media";
import { motionPrivacy } from "./config/motion-privacy";
import { homeAssistant, recorder, timelapse, addHomeAssistantRuntime } from "./config/services";
import { renderOsdPage } from "./config/osd";

// Keep the original helper imports available to callers of this page module.
export { visibleTimezoneOptions, resolveTimezoneSearch, buildTimeUpdate, cameraLocalTime, loadTimeConfig } from "./config/network-time";
export { buildAccessUpdate, buildWebuiUpdate } from "./config/access";
export { buildDayNightUpdate, buildGpioUpdate } from "./config/hardware";
export { buildImagingRequests } from "./config/media";
export { buildMotionPrivacyUpdate } from "./config/motion-privacy";
export { buildHomeAssistantUpdate } from "./config/services";
export { composeRgba, buildOsdUpdate, splitRgba, withOsdTimezoneToken } from "./config/osd";

const specs: Partial<Record<PageId, ConfigFormSpec>> = {
  network, time, audio, access, webui, admin, logging, daynight,
  imaging, streams, "motion-privacy": motionPrivacy,
  "home-assistant": homeAssistant, recorder, timelapse,
};

export function renderConfigPage(client: ApiClient, id: PageId): { node: HTMLElement; cleanup: () => void } | null {
  if (id === "osd") return renderOsdPage(client);
  if (id === "gpio") return renderGpioPage(client);
  const spec = specs[id];
  if (!spec) return null;
  const rendered = renderConfigForm(client, spec);
  if (id === "network") addWifiScan(client, rendered);
  if (id === "time") addTimeSync(client, rendered);
  if (id === "webui") addWebuiSecurity(client, rendered);
  if (id === "home-assistant") {
    const cleanupRuntime = addHomeAssistantRuntime(client, rendered);
    return { node: rendered.node, cleanup: () => { cleanupRuntime(); rendered.cleanup(); } };
  }
  return rendered;
}
