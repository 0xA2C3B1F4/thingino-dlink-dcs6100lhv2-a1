import type {
  AccessConfig,
  AccessUpdate,
  AccessUpdateResponse,
  HomeAssistantConfig,
  HomeAssistantAction,
  HomeAssistantRuntime,
  JsonObject,
  LiveControlCommand,
  LoginRequest,
  LoginResponse,
  NetworkConfig,
  MotionRuntime,
  RecorderConfig,
  RecorderResponse,
  RecorderUpdate,
  RuntimeHeartbeat,
  RuntimeMedia,
  RuntimeSystem,
  SessionState,
  TimeConfig,
  TimeUpdate,
  WebuiConfig,
  WifiScanResponse,
} from "./contracts";
import { ApiClient } from "./client";
import { decodeAccess, decodeHeartbeat, decodeHomeAssistant, decodeHomeAssistantRuntime, decodeMotionRuntime, decodeNetwork, decodeRecorder, decodeRuntimeMedia, decodeRuntimeSystem, decodeSession, decodeStream, decodeTime, decodeWebui, decodeWifiScan } from "./decode";
import { routes } from "./routes";

export class ControlApi {
  constructor(readonly http: ApiClient) {}

  async session(): Promise<SessionState> {
    return decodeSession(await this.http.json<unknown>(routes.auth.session));
  }

  login(credentials: LoginRequest): Promise<LoginResponse> {
    return this.http.postJson<LoginResponse>(routes.auth.login, credentials);
  }

  logout(): Promise<void> {
    return this.http.empty(routes.auth.logout, { method: "POST" });
  }

  async heartbeat(): Promise<RuntimeHeartbeat> {
    return decodeHeartbeat(await this.http.json<unknown>(routes.runtime.heartbeat));
  }

  async motionRuntime(): Promise<MotionRuntime> {
    return decodeMotionRuntime(await this.http.json<unknown>(routes.runtime.motion));
  }

  async media(): Promise<RuntimeMedia> {
    // The camera has a deliberately small bounded worker pool. Keep the
    // initial WebUI load below that limit instead of queuing a request burst.
    const runtime = decodeRuntimeMedia(await this.http.json<unknown>(routes.runtime.media));
    const readStream = async (stream: "ch0" | "ch1"): Promise<RuntimeMedia["stream0"]> => {
      const state = runtime.streams[stream];
      if (!state.available || !state.enabled) return state;
      const domain = stream === "ch0" ? "stream0" : "stream1";
      const config = decodeStream(await this.http.json<unknown>(routes.prudynt.domain(domain)), domain);
      return { ...config, ...state };
    };
    const stream0 = await readStream("ch0");
    const stream1 = await readStream("ch1");
    const result: RuntimeMedia = { stream0, stream1 };
    if ((runtime.streams.ch0.available && runtime.streams.ch0.enabled) || (runtime.streams.ch1.available && runtime.streams.ch1.enabled)) {
      const access = await this.access();
      result.rtsp = {
        ...(access.username !== null ? { username: access.username } : {}),
        ...(access.rtsp_port !== null ? { port: access.rtsp_port } : {}),
      };
    }
    return result;
  }

  async system(): Promise<RuntimeSystem> {
    return decodeRuntimeSystem(await this.http.json<unknown>(routes.runtime.system));
  }

  async network(): Promise<NetworkConfig> {
    return decodeNetwork(await this.http.json<unknown>(routes.config.network));
  }

  saveNetwork(config: NetworkConfig): Promise<JsonObject> {
    return this.http.postJson<JsonObject>(routes.config.network, config);
  }

  async wifiScan(): Promise<WifiScanResponse> {
    return decodeWifiScan(await this.http.json<unknown>(routes.network.wifiScan));
  }

  async time(): Promise<TimeConfig> {
    return decodeTime(await this.http.json<unknown>(routes.config.time));
  }

  saveTime(config: TimeUpdate): Promise<JsonObject> {
    return this.http.postJson<JsonObject>(routes.config.time, config);
  }

  async access(): Promise<AccessConfig> {
    return decodeAccess(await this.http.json<unknown>(routes.config.access));
  }

  saveAccess(config: AccessUpdate): Promise<AccessUpdateResponse> {
    return this.http.postJson<AccessUpdateResponse>(routes.config.access, config);
  }

  async webui(): Promise<WebuiConfig> {
    return decodeWebui(await this.http.json<unknown>(routes.config.webui));
  }

  async homeAssistant(): Promise<HomeAssistantConfig> {
    return decodeHomeAssistant(await this.http.json<unknown>(routes.config.homeAssistant));
  }

  saveHomeAssistant(config: HomeAssistantConfig): Promise<JsonObject> {
    return this.http.postJson<JsonObject>(routes.config.homeAssistant, config);
  }

  async homeAssistantRuntime(): Promise<HomeAssistantRuntime> {
    return decodeHomeAssistantRuntime(await this.http.json<unknown>(routes.runtime.homeAssistant));
  }

  homeAssistantAction(action: HomeAssistantAction): Promise<JsonObject> {
    return this.http.postJson<JsonObject>(routes.actions.homeAssistant, { action });
  }

  async recorder(): Promise<RecorderResponse> {
    return decodeRecorder(await this.http.json<unknown>(routes.recorder));
  }

  saveRecorder(config: RecorderUpdate): Promise<RecorderResponse> {
    return this.http.postJson<RecorderResponse>(routes.recorder, config);
  }

  async setLiveControl(command: LiveControlCommand): Promise<JsonObject> {
    if (command.kind === "daynight") {
      return this.http.postJson<JsonObject>(routes.actions.daynight, { mode: command.mode });
    }
    if (command.kind === "motion" || command.kind === "privacy") {
      return this.http.postJson<JsonObject>(routes.actions.control, {
        [command.kind]: { enabled: command.enabled },
      });
    }
    if (command.kind === "microphone" || command.kind === "speaker") {
      const key = command.kind === "microphone" ? "mic_enabled" : "spk_enabled";
      return this.http.postJson<JsonObject>(routes.actions.control, { audio: { [key]: command.enabled } });
    }
    if (command.kind === "recording") {
      const action = command.enabled ? "start" : "stop";
      return this.http.postJson<JsonObject>(routes.actions.control, {
        mp4: { [action]: { channel: command.stream } },
      });
    }
    return this.http.postJson<JsonObject>(routes.actions.control, {
      cmd: command.kind,
      val: command.enabled ? 1 : 0,
    });
  }
}
