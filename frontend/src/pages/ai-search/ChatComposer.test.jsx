import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ThemeProvider } from "@gravity-ui/uikit";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../../api.js";
import { ChatComposer } from "./ChatComposer.jsx";

vi.mock("../../api.js", () => ({ api: vi.fn() }));
vi.mock("../../Logo.jsx", () => ({ Logo: () => null }));

class Recorder {
  static isTypeSupported() {
    return true;
  }

  constructor(stream, options) {
    this.stream = stream;
    this.mimeType = options.mimeType;
  }

  start() {}

  stop() {
    this.ondataavailable?.({ data: new Blob(["voice"], { type: this.mimeType }) });
    this.onstop?.();
  }
}

function renderComposer(onSend) {
  render(
    <ThemeProvider theme="dark">
      <ChatComposer onSend={onSend} disabled={false} />
    </ThemeProvider>,
  );
}

describe("ChatComposer voice search", () => {
  beforeEach(() => {
    globalThis.MediaRecorder = Recorder;
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: {
        getUserMedia: vi.fn().mockResolvedValue({
          getTracks: () => [{ stop: vi.fn() }],
        }),
      },
    });
  });

  it("transcribes and sends a recording from the arrow action", async () => {
    const onSend = vi.fn();
    api.mockResolvedValue({ text: "Найди пекарни в Казани" });
    const user = userEvent.setup();
    renderComposer(onSend);

    await user.click(screen.getByRole("button", { name: "Голосовой ввод" }));
    await user.click(screen.getByRole("button", { name: "Расшифровать и отправить" }));

    await waitFor(() => {
      expect(onSend).toHaveBeenCalledWith("Найди пекарни в Казани");
    });
  });
});
