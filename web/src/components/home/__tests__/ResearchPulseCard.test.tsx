import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useLocation } from "react-router-dom";

import { MemoryRouter } from "@/test-utils";

const { submitDepthBatchMock, messageMock } = vi.hoisted(() => ({
  submitDepthBatchMock: vi.fn(),
  messageMock: {
    success: vi.fn(),
    warning: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
  },
}));

vi.mock("@/api/depth", () => ({ submitDepthBatch: submitDepthBatchMock }));
vi.mock("antd", async () => {
  const actual = await vi.importActual<typeof import("antd")>("antd");
  return {
    ...actual,
    message: messageMock,
    App: {
      ...actual.App,
      useApp: () => ({ message: messageMock, notification: {}, modal: {} }),
    },
  };
});
import { useDepthStore, type DepthTask } from "@/store/useDepthStore";

import ResearchPulseCard from "../ResearchPulseCard";

function LocationProbe() {
  const location = useLocation();
  const selectedIds = (location.state as { selectedIds?: string[] } | null)?.selectedIds ?? [];
  return (
    <output data-testid="location-path">
      {location.pathname}::{selectedIds.join(",")}
    </output>
  );
}

function makeTask(overrides: Partial<DepthTask> = {}): DepthTask {
  return {
    taskId: "task-123456789",
    paperIds: ["paper-a", "paper-b"],
    status: "running",
    progress: { current: 2, total: 10 },
    results: null,
    summary: null,
    errors: [],
    submittedAt: 1_000,
    ...overrides,
  };
}

beforeEach(() => {
  submitDepthBatchMock.mockReset();
  vi.clearAllMocks();
});

afterEach(() => {
  act(() => {
    useDepthStore.setState({ tasks: [] });
  });
});

describe("ResearchPulseCard", () => {
  it("stays hidden when there are no evaluation tasks", () => {
    useDepthStore.setState({ tasks: [] });

    render(
      <MemoryRouter>
        <ResearchPulseCard />
      </MemoryRouter>,
    );

    expect(screen.queryByTestId("research-pulse-card")).not.toBeInTheDocument();
  });

  it("prioritizes running tasks and shows their progress", () => {
    useDepthStore.setState({
      tasks: [
        makeTask({
          taskId: "completed-task",
          status: "completed",
          progress: { current: 10, total: 10 },
          summary: {
            total: 10,
            completed: 10,
            failed: 0,
            highest_score: 91.5,
            average_score: 84.2,
          },
          submittedAt: 3_000,
        }),
        makeTask({ submittedAt: 1_000 }),
      ],
    });

    const { container } = render(
      <MemoryRouter>
        <ResearchPulseCard />
      </MemoryRouter>,
    );

    const rows = container.querySelectorAll(".pf-pulse-row");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("task-123");
    expect(rows[0]).toHaveTextContent("2/10 · 20%");
    expect(rows[1]).toHaveTextContent("completed");
    expect(screen.getByText("pulse.status.running")).toBeInTheDocument();
    expect(screen.getByText("pulse.completedDetail")).toBeInTheDocument();
  });

  it("navigates to the evaluation page from the card action", async () => {
    useDepthStore.setState({ tasks: [makeTask()] });
    const user = userEvent.setup();

    render(
      <MemoryRouter>
        <ResearchPulseCard />
        <LocationProbe />
      </MemoryRouter>,
    );

    await user.click(screen.getByRole("button", { name: "pulse.viewAll" }));

    expect(screen.getByTestId("location-path")).toHaveTextContent("/depth");
  });

  it("shows failed task details and retries with the original paper IDs", async () => {
    submitDepthBatchMock.mockResolvedValue({ task_id: "retry-task", total_papers: 2 });
    useDepthStore.setState({
      tasks: [makeTask({ status: "failed", errors: ["worker stopped"] })],
    });
    const user = userEvent.setup();

    render(
      <MemoryRouter>
        <ResearchPulseCard />
        <LocationProbe />
      </MemoryRouter>,
    );

    expect(screen.getByText("pulse.status.failed")).toBeInTheDocument();
    expect(screen.getByText("pulse.failedDetail")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "pulse.retry" }));

    expect(submitDepthBatchMock).toHaveBeenCalledWith(["paper-a", "paper-b"]);
    await waitFor(() => {
      expect(screen.getByTestId("location-path")).toHaveTextContent(
        "/depth::paper-a,paper-b",
      );
    });
    expect(useDepthStore.getState().tasks).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ taskId: "retry-task", status: "running", paperIds: ["paper-a", "paper-b"] }),
      ]),
    );
    expect(useDepthStore.getState().tasks).not.toEqual(
      expect.arrayContaining([expect.objectContaining({ taskId: "task-123456789" })]),
    );
  });

  it("cancels a running task and keeps it out of the running state", async () => {
    useDepthStore.setState({ tasks: [makeTask()] });
    const user = userEvent.setup();

    render(
      <MemoryRouter>
        <ResearchPulseCard />
      </MemoryRouter>,
    );

    await user.click(screen.getByRole("button", { name: "pulse.cancel" }));
    await user.click(screen.getByRole("button", { name: "common.confirm" }));

    expect(useDepthStore.getState().tasks[0]).toMatchObject({
      taskId: "task-123456789",
      status: "canceled",
    });
    expect(screen.getByText("pulse.status.canceled")).toBeInTheDocument();
  });

  it("clears inactive tasks without removing running tasks", async () => {
    useDepthStore.setState({
      tasks: [
        makeTask({ taskId: "running-task", status: "running" }),
        makeTask({ taskId: "failed-task", status: "failed" }),
        makeTask({ taskId: "canceled-task", status: "canceled" }),
      ],
    });
    const user = userEvent.setup();

    render(
      <MemoryRouter>
        <ResearchPulseCard />
      </MemoryRouter>,
    );

    await user.click(screen.getByRole("button", { name: "pulse.clearInactive" }));

    expect(useDepthStore.getState().tasks.map((task) => task.taskId)).toEqual(["running-task"]);
  });
});
