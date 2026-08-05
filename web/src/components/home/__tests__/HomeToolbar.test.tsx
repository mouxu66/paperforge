import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "@/test-utils";
import HomeToolbar from "../HomeToolbar";

describe("HomeToolbar", () => {
  const renderToolbar = (props = {}) => {
    return render(
      <MemoryRouter>
        <HomeToolbar
          selectMode={false}
          onToggleSelectMode={vi.fn()}
          onReflectionUpload={vi.fn()}
          onTagManager={vi.fn()}
          {...props}
        />
      </MemoryRouter>,
    );
  };

  it("renders action buttons", () => {
    renderToolbar();
    expect(screen.getByText("nav.depth")).toBeInTheDocument();
    expect(screen.getByText("reflection.upload.quickAction")).toBeInTheDocument();
    expect(screen.getByText("tag.manager.button")).toBeInTheDocument();
    expect(screen.getByText("home.toolbar.selectPapers")).toBeInTheDocument();
  });

  it("toggles select mode label", () => {
    renderToolbar({ selectMode: true });
    expect(screen.getByText("home.toolbar.exitSelection")).toBeInTheDocument();
  });

  it("calls onToggleSelectMode when select button clicked", async () => {
    const onToggleSelectMode = vi.fn();
    const user = userEvent.setup();
    renderToolbar({ onToggleSelectMode });
    await user.click(screen.getByText("home.toolbar.selectPapers"));
    expect(onToggleSelectMode).toHaveBeenCalledTimes(1);
  });

  it("calls onReflectionUpload when reflection button clicked", async () => {
    const onReflectionUpload = vi.fn();
    const user = userEvent.setup();
    renderToolbar({ onReflectionUpload });
    await user.click(screen.getByText("reflection.upload.quickAction"));
    expect(onReflectionUpload).toHaveBeenCalledTimes(1);
  });

  it("calls onTagManager when tag manager button clicked", async () => {
    const onTagManager = vi.fn();
    const user = userEvent.setup();
    renderToolbar({ onTagManager });
    await user.click(screen.getByText("tag.manager.button"));
    expect(onTagManager).toHaveBeenCalledTimes(1);
  });
});
