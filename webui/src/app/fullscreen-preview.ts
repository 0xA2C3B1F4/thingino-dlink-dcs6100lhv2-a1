import { button } from "./dom";

/**
 * Presents an existing preview frame full screen without replacing its media or
 * changing the media source. Native Fullscreen API and viewport fallback share
 * the same DOM element and therefore the same MJPEG connection.
 */
export class PreviewFullscreen {
  private readonly action: HTMLButtonElement;
  private readonly media: readonly HTMLElement[];
  private fallbackActive = false;
  private destroyed = false;

  constructor(
    private readonly frame: HTMLElement,
    media: HTMLElement | readonly HTMLElement[],
    private readonly label: string,
  ) {
    this.media = Array.isArray(media) ? media : [media as HTMLElement];
    frame.classList.add("fullscreen-preview");
    for (const element of this.media) {
      element.classList.add("fullscreen-preview-image");
      element.tabIndex = 0;
      element.setAttribute("role", "button");
      element.setAttribute("aria-label", `Open ${label} full screen`);
    }

    this.action = button("Full screen", "button secondary preview-fullscreen-action");
    this.action.setAttribute("aria-label", `Open ${label} full screen`);
    frame.append(this.action);

    this.action.addEventListener("click", this.onActionClick);
    for (const element of this.media) {
      element.addEventListener("click", this.onImageClick);
      element.addEventListener("keydown", this.onImageKeyDown);
    }
    document.addEventListener("fullscreenchange", this.onFullscreenChange);
    document.addEventListener("keydown", this.onDocumentKeyDown);
    this.syncState();
  }

  isActive(): boolean {
    return this.fallbackActive || document.fullscreenElement === this.frame;
  }

  async enter(): Promise<void> {
    if (this.destroyed || this.isActive()) return;
    if (typeof this.frame.requestFullscreen === "function") {
      try {
        await this.frame.requestFullscreen();
        this.syncState();
        return;
      } catch (_) {
        // Browser policy can reject native fullscreen; use the same-frame fallback.
      }
    }
    this.fallbackActive = true;
    document.documentElement.classList.add("preview-fullscreen-lock");
    this.syncState();
  }

  async exit(): Promise<void> {
    if (this.fallbackActive) {
      this.fallbackActive = false;
      document.documentElement.classList.remove("preview-fullscreen-lock");
      this.syncState();
      return;
    }
    if (document.fullscreenElement === this.frame && typeof document.exitFullscreen === "function") {
      try { await document.exitFullscreen(); } catch (_) { /* browser already left fullscreen */ }
    }
    this.syncState();
  }

  destroy(): void {
    if (this.destroyed) return;
    this.destroyed = true;
    this.action.removeEventListener("click", this.onActionClick);
    for (const element of this.media) {
      element.removeEventListener("click", this.onImageClick);
      element.removeEventListener("keydown", this.onImageKeyDown);
    }
    document.removeEventListener("fullscreenchange", this.onFullscreenChange);
    document.removeEventListener("keydown", this.onDocumentKeyDown);
    if (this.fallbackActive) {
      this.fallbackActive = false;
      document.documentElement.classList.remove("preview-fullscreen-lock");
    }
    if (document.fullscreenElement === this.frame && typeof document.exitFullscreen === "function") {
      void document.exitFullscreen().catch(() => undefined);
    }
    this.frame.classList.remove("fullscreen-active", "fullscreen-fallback");
    for (const element of this.media) {
      element.removeAttribute("role");
      element.removeAttribute("tabindex");
    }
    this.action.remove();
  }

  private readonly onActionClick = (): void => { void this.toggle(); };
  private readonly onImageClick = (): void => { void this.toggle(); };
  private readonly onImageKeyDown = (event: KeyboardEvent): void => {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    void this.toggle();
  };
  private readonly onDocumentKeyDown = (event: KeyboardEvent): void => {
    if (event.key === "Escape" && this.fallbackActive) void this.exit();
  };
  private readonly onFullscreenChange = (): void => { this.syncState(); };

  private async toggle(): Promise<void> {
    if (this.isActive()) await this.exit();
    else await this.enter();
  }

  private syncState(): void {
    const active = this.isActive();
    this.frame.classList.toggle("fullscreen-active", active);
    this.frame.classList.toggle("fullscreen-fallback", this.fallbackActive);
    this.action.textContent = active ? "Close full screen" : "Full screen";
    this.action.setAttribute("aria-label", active ? `Close ${this.label} full screen` : `Open ${this.label} full screen`);
    this.action.setAttribute("aria-pressed", String(active));
    for (const element of this.media) {
      element.setAttribute("aria-label", active ? `Close ${this.label} full screen` : `Open ${this.label} full screen`);
    }
  }
}
