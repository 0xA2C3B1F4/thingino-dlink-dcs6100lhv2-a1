import { ApiClient } from "../api/client";
import { renderConfigForm, type ConfigFormSpec } from "../app/forms";
import type { PageId } from "../app/navigation";
import { network, time, addWifiScan, addTimeSync } from "./config/network-time";
import { access, webui, admin, logging, addWebuiSecurity } from "./config/access";
import { daynight, renderGpioPage } from "./config/hardware";
import { audio, imaging, streams } from "./config/media";
import { addRaptorMotionMqttStatus, motionPrivacy, raptorMotionEmail, raptorMotionFtp, raptorMotionGotify, raptorMotionNtfy, raptorMotionTelegram, raptorMotionWebhook, raptorPrivacy } from "./config/motion-privacy";
import { homeAssistant, recorder, timelapse, renderRecorderPage, renderTimelapsePage, addHomeAssistantRuntime } from "./config/services";
import { renderOsdPage } from "./config/osd";

// Keep the original helper imports available to callers of this page module.
export { visibleTimezoneOptions, resolveTimezoneSearch, buildTimeUpdate, cameraLocalTime, loadTimeConfig } from "./config/network-time";
export { buildAccessUpdate, buildWebuiUpdate } from "./config/access";
export { buildDayNightUpdate, buildGpioUpdate } from "./config/hardware";
export { buildImagingRequests } from "./config/media";
export { buildMotionEmailUpdate, buildMotionFtpUpdate, buildMotionGotifyUpdate, buildMotionNtfyUpdate, buildMotionPrivacyUpdate, buildMotionTelegramUpdate, buildMotionWebhookUpdate } from "./config/motion-privacy";
export { buildHomeAssistantUpdate } from "./config/services";
export { composeRgba, buildOsdUpdate, splitRgba, withOsdTimezoneToken } from "./config/osd";

const specs: Partial<Record<PageId, ConfigFormSpec>> = {
  network, time, audio, access, webui, admin, logging, daynight,
  imaging, streams, "motion-privacy": motionPrivacy,
  "home-assistant": homeAssistant, recorder, timelapse,
};

export function renderConfigPage(client: ApiClient, id: PageId): { node: HTMLElement; cleanup: () => void } | null {
  if (id === "timelapse") return renderTimelapsePage(client);
  if (id === "recorder") return renderRecorderPage(client);
  if (id === "osd") return renderOsdPage(client);
  if (id === "gpio") return renderGpioPage(client);
  if (id === "motion-privacy") {
    let privacy: ReturnType<typeof renderConfigForm> | undefined;
    let ntfy: ReturnType<typeof renderConfigForm> | undefined;
    let email: ReturnType<typeof renderConfigForm> | undefined;
    let ftp: ReturnType<typeof renderConfigForm> | undefined;
    let gotify: ReturnType<typeof renderConfigForm> | undefined;
    let telegram: ReturnType<typeof renderConfigForm> | undefined;
    let webhook: ReturnType<typeof renderConfigForm> | undefined;
    let mqtt: ReturnType<typeof addRaptorMotionMqttStatus> | undefined;
    let disposed = false;
    const rendered = renderConfigForm(client, { ...motionPrivacy, onLoaded: (value) => {
      if (!disposed && !privacy && value.motion_full_controls === false) {
        ntfy = renderConfigForm(client, raptorMotionNtfy);
        rendered.node.append(ntfy.node);
        email = renderConfigForm(client, raptorMotionEmail);
        rendered.node.append(email.node);
        ftp = renderConfigForm(client, raptorMotionFtp);
        rendered.node.append(ftp.node);
        gotify = renderConfigForm(client, raptorMotionGotify);
        rendered.node.append(gotify.node);
        telegram = renderConfigForm(client, raptorMotionTelegram);
        rendered.node.append(telegram.node);
        webhook = renderConfigForm(client, raptorMotionWebhook);
        rendered.node.append(webhook.node);
        privacy = renderConfigForm(client, raptorPrivacy);
        rendered.node.append(privacy.node);
      }
      if (!disposed && value.motion_full_controls === false) mqtt?.refresh();
    } });
    mqtt = addRaptorMotionMqttStatus(client, rendered);
    return { node: rendered.node, cleanup: () => { disposed = true; mqtt?.cleanup(); email?.cleanup(); ftp?.cleanup(); gotify?.cleanup(); ntfy?.cleanup(); telegram?.cleanup(); webhook?.cleanup(); privacy?.cleanup(); rendered.cleanup(); } };
  }
  const spec = specs[id];
  if (!spec) return null;
  const rendered = renderConfigForm(client, spec);
  if (id === "network") addWifiScan(client, rendered);
  if (id === "time") {
    const cleanupSync = addTimeSync(client, rendered);
    return { node: rendered.node, cleanup: () => { cleanupSync(); rendered.cleanup(); } };
  }
  if (id === "webui") addWebuiSecurity(client, rendered);
  if (id === "home-assistant") {
    const cleanupRuntime = addHomeAssistantRuntime(client, rendered);
    return { node: rendered.node, cleanup: () => { cleanupRuntime(); rendered.cleanup(); } };
  }
  return rendered;
}
