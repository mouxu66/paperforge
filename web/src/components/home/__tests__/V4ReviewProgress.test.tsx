import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import V4ReviewProgress from "../V4ReviewProgress";
import type { TaskInfo } from "@/store/useTaskStore";

const baseTask: TaskInfo = {
  id: "task-1",
  type: "batch_v4_review",
  status: "pending",
  progress: 0,
  progressMessage: "等待执行...",
  createdAt: "2024-01-01T00:00:00Z",
};

describe("V4ReviewProgress", () => {
  it("renders nothing when task is null", () => {
    const { container } = render(<V4ReviewProgress task={null} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders pending state", () => {
    render(<V4ReviewProgress task={{ ...baseTask, status: "pending" }} />);
    expect(screen.getByText("⏳ 批量审稿排队中…")).toBeInTheDocument();
  });

  it("renders running state with progress message", () => {
    render(
      <V4ReviewProgress
        task={{
          ...baseTask,
          status: "running",
          progress: 42,
          progressMessage: "正在处理第 3/10 篇",
        }}
      />,
    );
    expect(screen.getByText("⚡ 批量审稿进行中")).toBeInTheDocument();
    expect(screen.getByText("正在处理第 3/10 篇")).toBeInTheDocument();
  });

  it("renders completed state", () => {
    render(<V4ReviewProgress task={{ ...baseTask, status: "completed" }} />);
    expect(screen.getByText("✅ 批量审稿已完成")).toBeInTheDocument();
  });

  it("renders failed state with error message", () => {
    render(<V4ReviewProgress task={{ ...baseTask, status: "failed", error: "网络超时" }} />);
    expect(screen.getByText("❌ 批量审稿失败: 网络超时")).toBeInTheDocument();
  });
});
