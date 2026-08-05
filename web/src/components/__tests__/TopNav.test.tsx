import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "@/test-utils";
import { Routes, Route  } from "react-router-dom";
import TopNav from "@/components/TopNav";
import { useFavoriteStore } from "@/store/useFavoriteStore";
import { useDepthStore } from "@/store/useDepthStore";
import { useThemeStore } from "@/store/useThemeStore";
import { changeLanguage } from "@/i18n";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------
vi.mock("antd", async (importOriginal) => {
  const actual = await importOriginal<typeof import("antd")>();
  return {
    ...actual,
    Menu: ({ items, selectedKeys }: any) => {
      const renderItem = (item: any) => {
        if (!item) return null;
        if (item.children) {
          return (
            <div key={item.key}>
              <span>{item.label}</span>
              <ul>{item.children.map(renderItem)}</ul>
            </div>
          );
        }
        return (
          <li key={item.key} aria-selected={selectedKeys?.includes(item.key) ? "true" : "false"}>
            {item.icon}
            {item.label}
          </li>
        );
      };
      return <ul>{items.map(renderItem)}</ul>;
    },
  };
});

vi.mock("@/api/qwen", () => ({
  getQwenStatus: vi.fn(),
}));

vi.mock("@/i18n", () => ({
  changeLanguage: vi.fn(),
}));

vi.mock("@/store/useFavoriteStore", () => ({
  useFavoriteStore: vi.fn(),
}));

vi.mock("@/store/useDepthStore", () => ({
  useDepthStore: vi.fn(),
}));

vi.mock("@/store/useThemeStore", () => ({
  useThemeStore: vi.fn(),
}));

vi.mock("@/components/ModelSelector", () => ({
  default: () => <div data-testid="model-selector">ModelSelector</div>,
}));

vi.mock("@/components/DevTools", () => ({
  default: () => <div data-testid="dev-tools">DevTools</div>,
}));

vi.mock("@/components/VramStatusIndicator", () => ({
  default: () => null,
}));

const mockSetMode = vi.fn();

beforeEach(() => {
  vi.clearAllMocks();
  (useFavoriteStore as unknown as ReturnType<typeof vi.fn>).mockImplementation(
    (selector: (s: { items: unknown[] }) => unknown) => selector({ items: [] }),
  );
  (useDepthStore as unknown as ReturnType<typeof vi.fn>).mockImplementation(
    (selector: (s: { tasks: unknown[] }) => unknown) => selector({ tasks: [] }),
  );
  (useThemeStore as unknown as ReturnType<typeof vi.fn>).mockReturnValue({
    mode: "light",
    setMode: mockSetMode,
  });
});

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function renderWithRouter(initialEntries: string[] = ["/"]) {
  return render(
    <MemoryRouter initialEntries={initialEntries}>
      <TopNav />
      <Routes>
        <Route path="*" element={<div data-testid="route-dummy" />} />
      </Routes>
    </MemoryRouter>,
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------
describe("TopNav — six-domain grouped menu", () => {
  it("renders the six domain labels", () => {
    renderWithRouter();

    expect(screen.getByText("nav.domain.collect")).toBeInTheDocument();
    expect(screen.getByText("nav.domain.organize")).toBeInTheDocument();
    expect(screen.getByText("nav.domain.discover")).toBeInTheDocument();
    expect(screen.getByText("nav.domain.understand")).toBeInTheDocument();
    expect(screen.getByText("nav.domain.create")).toBeInTheDocument();
    expect(screen.getByText("nav.domain.configure")).toBeInTheDocument();
  });

  it("renders child links under each domain", () => {
    renderWithRouter();

    expect(screen.getByRole("link", { name: "nav.home" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "nav.favorites" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "nav.duplicates" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "nav.compare" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "nav.figures" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "nav.ask" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "nav.depth" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "nav.generate" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "nav.write" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "nav.models" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "nav.settings" })).toBeInTheDocument();
  });

  it("links point to the correct routes", () => {
    renderWithRouter();

    expect(screen.getByRole("link", { name: "nav.home" })).toHaveAttribute("href", "/");
    expect(screen.getByRole("link", { name: "nav.favorites" })).toHaveAttribute(
      "href",
      "/favorites",
    );
    expect(screen.getByRole("link", { name: "nav.duplicates" })).toHaveAttribute(
      "href",
      "/duplicates",
    );
    expect(screen.getByRole("link", { name: "nav.compare" })).toHaveAttribute("href", "/compare");
    expect(screen.getByRole("link", { name: "nav.figures" })).toHaveAttribute("href", "/figures");
    expect(screen.getByRole("link", { name: "nav.ask" })).toHaveAttribute("href", "/ask");
    expect(screen.getByRole("link", { name: "nav.depth" })).toHaveAttribute("href", "/depth");
    expect(screen.getByRole("link", { name: "nav.generate" })).toHaveAttribute("href", "/generate");
    expect(screen.getByRole("link", { name: "nav.write" })).toHaveAttribute("href", "/write");
    expect(screen.getByRole("link", { name: "nav.models" })).toHaveAttribute("href", "/models");
    expect(screen.getByRole("link", { name: "nav.settings" })).toHaveAttribute("href", "/settings");
  });

  it("highlights the active child route", () => {
    renderWithRouter(["/depth"]);
    const depthItem = screen.getByRole("link", { name: "nav.depth" });
    expect(depthItem.closest("li")).toHaveAttribute("aria-selected", "true");
  });

  it("falls back to path-based selection for unmatched routes", () => {
    renderWithRouter(["/paper/123"]);
    const homeItem = screen.getByRole("link", { name: "nav.home" });
    expect(homeItem.closest("li")).toHaveAttribute("aria-selected", "true");
  });

  it("shows favorite count badge", () => {
    (useFavoriteStore as unknown as ReturnType<typeof vi.fn>).mockImplementation(
      (selector: (s: { items: unknown[] }) => unknown) =>
        selector({ items: [{ id: "1" }, { id: "2" }] }),
    );

    renderWithRouter();
    expect(screen.getByText("2")).toBeInTheDocument();
  });

  it("shows running depth count tag", () => {
    (useDepthStore as unknown as ReturnType<typeof vi.fn>).mockImplementation(
      (selector: (s: { tasks: unknown[] }) => unknown) =>
        selector({ tasks: [{ id: "1", status: "running" }] }),
    );

    renderWithRouter(["/depth"]);
    expect(screen.getByText("1")).toBeInTheDocument();
  });

  it("toggles language when language button is clicked", async () => {
    const user = userEvent.setup();
    renderWithRouter();

    // 按钮同时包含 GlobalOutlined 图标和 "中" 文本，accessible name 为 "global 中"
    const langButton = screen.getByRole("button", { name: /中/ });
    expect(langButton).toBeInTheDocument();

    await user.click(langButton);
    expect(changeLanguage).toHaveBeenCalledTimes(1);
    expect(changeLanguage).toHaveBeenCalledWith("en");
  });
});
