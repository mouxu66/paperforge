import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ReflectionReportsPage from "@/pages/ReflectionReportsPage";

// 页面依赖的列表与上传组件在单测中打桩：列表无需真实拉取 API，
// 上传组件仅验证 open 状态切换（真实弹窗逻辑已有 HomePage 集成覆盖）。
vi.mock("@/components/ReflectionList", () => ({
  default: () => <div data-testid="reflection-list" />,
}));

vi.mock("@/components/ReflectionUpload", () => ({
  default: ({ open }: { open: boolean }) =>
    open ? <div data-testid="reflection-upload" /> : null,
}));

describe("ReflectionReportsPage", () => {
  it("renders the reflection list and exposes the submit entry", () => {
    render(<ReflectionReportsPage />);
    expect(screen.getByTestId("reflection-list")).toBeInTheDocument();
    expect(screen.getByRole("button")).toBeInTheDocument();
  });

  it("opens the upload modal when the submit button is clicked", async () => {
    const user = userEvent.setup();
    render(<ReflectionReportsPage />);
    expect(screen.queryByTestId("reflection-upload")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button"));

    expect(screen.getByTestId("reflection-upload")).toBeInTheDocument();
  });
});
