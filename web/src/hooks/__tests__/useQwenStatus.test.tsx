import { renderHook, act } from "@testing-library/react";
import { useQwenStatus } from "../useQwenStatus";
import * as qwenApi from "@/api/qwen";

vi.mock("@/api/qwen", () => ({
  getQwenStatus: vi.fn(),
}));

const mockedGet = vi.mocked(qwenApi.getQwenStatus);

describe("useQwenStatus", () => {
  beforeEach(() => {
    mockedGet.mockReset();
  });

  it("showHint=false on idle/ready", async () => {
    mockedGet.mockResolvedValue({
      managed: true,
      ready: true,
      vram_exclusive: true,
      event: { kind: "idle", eta_seconds: 0, state: "idle" },
    });
    const { result } = renderHook(() => useQwenStatus());
    await act(async () => {
      result.current.start();
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(result.current.showHint).toBe(false);
  });

  it("showHint=true when loading (cold start)", async () => {
    mockedGet.mockResolvedValue({
      managed: true,
      ready: false,
      vram_exclusive: true,
      event: { kind: "loading", eta_seconds: 240, state: "idle" },
    });
    const { result } = renderHook(() => useQwenStatus());
    await act(async () => {
      result.current.start();
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(result.current.showHint).toBe(true);
    expect(result.current.status?.event.eta_seconds).toBe(240);
  });

  it("showHint=true when switching (mutual exclusion)", async () => {
    mockedGet.mockResolvedValue({
      managed: true,
      ready: false,
      vram_exclusive: true,
      event: { kind: "switching", eta_seconds: 180, state: "ocr_active" },
    });
    const { result } = renderHook(() => useQwenStatus());
    await act(async () => {
      result.current.start();
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(result.current.showHint).toBe(true);
  });

  it("stop() does not crash and resets active flag", async () => {
    mockedGet.mockResolvedValue({
      managed: true,
      ready: true,
      vram_exclusive: true,
      event: { kind: "ready", eta_seconds: 0, state: "qwen_active" },
    });
    const { result } = renderHook(() => useQwenStatus());
    await act(async () => {
      result.current.start();
      await Promise.resolve();
      await Promise.resolve();
    });
    await act(async () => {
      result.current.stop();
      await Promise.resolve();
    });
    expect(result.current.showHint).toBe(false);
  });
});
