export interface PreviewPlaybackState {
  available: boolean;
  active: boolean;
  message: string;
}

/** Browser playback only. Never changes the camera microphone or speaker. */
export class PreviewAudioPlayback {
  private intent = false;
  private available = false;
  private epoch = 0;
  private active = false;
  constructor(private readonly video: HTMLVideoElement, private readonly changed: (state: PreviewPlaybackState) => void) {}

  private publish(message: string): void {
    this.changed({ available: this.available, active: this.active, message });
  }

  setAvailable(available: boolean): void {
    this.available = available;
    if (!available) {
      ++this.epoch;
      this.active = false;
      this.video.muted = true;
      this.publish(this.intent ? "Playback suspended. Waiting for WebRTC audio." : "Waiting for WebRTC audio");
    } else if (!this.active) {
      this.publish(this.intent ? "Resuming camera audio…" : "Playback muted. Click Listen to hear the camera microphone.");
    }
  }

  reset(): void {
    this.intent = false;
    ++this.epoch;
    this.active = false;
    this.video.muted = true;
    this.publish(this.available ? "Playback muted. Camera microphone is unchanged." : "Waiting for WebRTC audio");
  }

  toggle(): void {
    if (!this.available) return;
    if (this.intent && !this.video.muted) { this.reset(); return; }
    this.intent = true;
    // Called synchronously from the user gesture, never after an async request.
    this.mediaReady();
  }

  mediaReady(): void {
    const epoch = ++this.epoch;
    this.video.muted = !(this.intent && this.available);
    void this.video.play().then(() => {
      if (epoch !== this.epoch) return;
      this.active = this.intent && this.available && !this.video.muted;
      this.publish(this.active
        ? "Playback enabled. Sound requires an enabled, unmuted camera microphone."
        : this.available ? "Playback muted. Click Listen to hear the camera microphone." : "Waiting for WebRTC audio");
    }).catch(() => {
      if (epoch !== this.epoch) return;
      this.video.muted = true;
      this.active = false;
      this.publish(this.intent && this.available
        ? "Browser blocked playback. Click Listen to try again."
        : this.available ? "Playback muted. Click Listen to hear the camera microphone." : "Waiting for WebRTC audio");
      if (this.intent && this.available) {
        // Keep video moving if audible autoplay was rejected. This attempt is
        // bounded, stays muted, and never changes intent or claims audio success.
        void this.video.play().catch(() => undefined);
      }
    });
  }
}
