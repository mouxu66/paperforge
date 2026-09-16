import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import SettingsPage from "../SettingsPage";
import { importFromZotero } from "@/api/zotero";

const mockNavigate = vi.hoisted(() => vi.fn());
const messageWarning = vi.hoisted(() => vi.fn());
const messageSuccess = vi.hoisted(() => vi.fn());
const messageInfo = vi.hoisted(() => vi.fn());

vi.mock("react-router-dom", () => ({
  useNavigate: () => mockNavigate,
}));

vi.mock("@/api/zotero", () => ({
  importFromZotero: vi.fn(),
}));

vi.mock("@/components/DepthSettingsPanel", () => ({
  default: () => <div data-testid="depth-settings-panel" />,
}));

vi.mock("@/components/VramEventPanel", () => ({
  default: () => <div data-testid="vram-event-panel" />,
}));

vi.mock("antd", async () => {
  const actual = await vi.importActual<typeof import("antd")>("antd");
  const message = {
    ...actual.message,
    warning: messageWarning,
    success: messageSuccess,
    info: messageInfo,
    error: vi.fn(),
  };
  return {
    ...actual,
    message,
    App: {
      ...actual.App,
      useApp: () => ({ message, notification: {}, modal: {} }),
    },
  };
});

const mockedImport = vi.mocked(importFromZotero);

describe("SettingsPage — Zotero import", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("requires a Zotero user ID before submitting", async () => {
    const user = userEvent.setup();
    render(<SettingsPage />);

    await user.click(screen.getByRole("button", { name: "settings.importButton" }));

    expect(messageWarning).toHaveBeenCalledWith("settings.userIdRequired");
    expect(mockedImport).not.toHaveBeenCalled();
  });

  it("associates visible labels with both credential fields", () => {
    render(<SettingsPage />);

    expect(screen.getByLabelText(/settings\.userIdLabel/)).toHaveAttribute("id", "zotero-user-id");
    expect(screen.getByLabelText(/API Key/)).toHaveAttribute("id", "zotero-api-key");
  });

  it("submits credentials and renders the import summary", async () => {
    const user = userEvent.setup();
    mockedImport.mockResolvedValue({
      results: [
        {
          id: "z1",
          title: "Imported paper",
          authors: ["Author"],
          year: 2025,
          abstract: "Abstract",
          source: "zotero",
          success: true,
        },
      ],
      successCount: 1,
      failCount: 0,
      totalFetched: 1,
    });

    render(<SettingsPage />);
    await user.type(screen.getByRole("textbox", { name: /settings\.userIdLabel/ }), "user-123");
    await user.type(screen.getByLabelText(/API Key/), "secret-key");
    await user.click(screen.getByRole("button", { name: "settings.importButton" }));

    await waitFor(() => {
      expect(mockedImport).toHaveBeenCalledWith("user-123", "secret-key");
    });
    expect(await screen.findByText("Imported paper")).toBeInTheDocument();
    expect(screen.getByText("settings.tagSuccess")).toBeInTheDocument();
    expect(messageSuccess).toHaveBeenCalledWith("settings.importSuccess");
  });
});
