import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useTaskStore, type TaskInfo } from "@/store/useTaskStore";

class MockEventSource {
  static instances: MockEventSource[] = [];
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;

  constructor(public readonly url: string) {
    MockEventSource.instances.push(this);
  }

  close() {
    this.closed = true;
  }
}

describe("useTaskStore", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    MockEventSource.instances = [];
    vi.stubGlobal("EventSource", MockEventSource);
    useTaskStore.setState({ tasks: [], recent: [] });
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("restores pending and running tasks while ignoring terminal tasks", async () => {
    const items: TaskInfo[] = [
      {
        id: "pending-1",
        type: "reflection_review",
        status: "pending",
        progress: 0,
        progressMessage: "等待执行",
        createdAt: "2026-08-02T00:00:00Z",
      },
      {
        id: "running-1",
        type: "depth_review",
        status: "running",
        progress: 20,
        progressMessage: "处理中",
        createdAt: "2026-08-02T00:00:01Z",
      },
      {
        id: "completed-1",
        type: "depth_review",
        status: "completed",
        progress: 100,
        progressMessage: "完成",
        createdAt: "2026-08-02T00:00:02Z",
      },
    ];
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) =>
        Promise.resolve({
          ok: true,
          json: async () => ({
            items: url.includes("status=pending")
              ? [items[0]]
              : [items[1], items[2]],
            total: 1,
          }),
        }),
      ),
    );

    await useTaskStore.getState().loadActiveTasks();

    expect(fetch).toHaveBeenCalledTimes(2);
    expect(fetch).toHaveBeenNthCalledWith(
      1,
      "/api/tasks?status=pending&limit=100",
      expect.objectContaining({ headers: {} }),
    );
    expect(fetch).toHaveBeenNthCalledWith(
      2,
      "/api/tasks?status=running&limit=100",
      expect.objectContaining({ headers: {} }),
    );
    expect(useTaskStore.getState().tasks.map((task) => task.id)).toEqual(["pending-1", "running-1"]);
  });

  it("preserves existing active tasks when one restoration query fails", async () => {
    const existingRunning: TaskInfo = {
      id: "existing-running",
      type: "depth_review",
      status: "running",
      progress: 30,
      progressMessage: "处理中",
      createdAt: "2026-08-02T00:00:00Z",
    };
    useTaskStore.setState({ tasks: [existingRunning], recent: [] });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) =>
        Promise.resolve(
          url.includes("status=pending")
            ? { ok: false, json: async () => ({}) }
            : {
                ok: true,
                json: async () => ({
                  items: [
                    {
                      ...existingRunning,
                      progress: 55,
                      progressMessage: "仍在处理",
                    },
                  ],
                }),
              },
        ),
      ),
    );

    await useTaskStore.getState().loadActiveTasks();

    expect(useTaskStore.getState().tasks).toEqual([
      expect.objectContaining({ id: "existing-running", progress: 55 }),
    ]);
  });

  it("does not report a client polling timeout as a backend failure", () => {
    const onUpdate = vi.fn();
    const onError = vi.fn();

    useTaskStore.setState({
      tasks: [
        {
          id: "task-1",
          type: "depth_review",
          status: "running",
          progress: 42,
          progressMessage: "处理中",
          params: { paperId: "paper-1" },
          createdAt: "2026-08-02T00:00:00Z",
        },
      ],
      recent: [],
    });
    const unsubscribe = useTaskStore
      .getState()
      .subscribeSSE("task-1", onUpdate, undefined, onError);
    const eventSource = MockEventSource.instances[0];

    eventSource.onerror?.();
    vi.advanceTimersByTime(30_000);

    expect(onError).not.toHaveBeenCalled();
    expect(onUpdate).toHaveBeenCalledWith(
      expect.objectContaining({
        id: "task-1",
        type: "depth_review",
        status: "running",
        progress: 42,
        params: { paperId: "paper-1" },
        error: "状态查询暂时无响应，请稍后重试",
      }),
    );
    expect(useTaskStore.getState().tasks[0]).toMatchObject({
      id: "task-1",
      status: "running",
      progress: 42,
      progressMessage: "状态查询暂时无响应，请稍后重试",
      error: "状态查询暂时无响应，请稍后重试",
    });

    unsubscribe();
  });

  it("reports backend timeout through onError and moves it to recent", () => {
    const onError = vi.fn();
    useTaskStore.setState({
      tasks: [
        {
          id: "task-timeout",
          type: "depth_review",
          status: "running",
          progress: 60,
          progressMessage: "处理中",
          createdAt: "2026-08-02T00:00:00Z",
        },
      ],
      recent: [],
    });
    const unsubscribe = useTaskStore
      .getState()
      .subscribeSSE("task-timeout", undefined, undefined, onError);
    const eventSource = MockEventSource.instances[0];

    eventSource.onmessage?.({
      data: JSON.stringify({
        taskId: "task-timeout",
        status: "timed_out",
        progress: 60,
        progressMessage: "任务超时",
        error: "超过最大执行时长",
      }),
    } as MessageEvent);

    expect(onError).toHaveBeenCalledWith(
      expect.objectContaining({
        id: "task-timeout",
        status: "timed_out",
        error: "超过最大执行时长",
      }),
    );
    expect(useTaskStore.getState().tasks).toEqual([]);
    expect(useTaskStore.getState().recent[0]).toMatchObject({
      id: "task-timeout",
      status: "timed_out",
    });
    unsubscribe();
  });

  it("does not apply a late poll response after unsubscribe", async () => {
    let resolvePoll: ((value: Response) => void) | undefined;
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(
        () =>
          new Promise<Response>((resolve) => {
            resolvePoll = resolve;
          }),
      ),
    );
    const onUpdate = vi.fn();
    const onDone = vi.fn();
    const onError = vi.fn();
    useTaskStore.setState({
      tasks: [
        {
          id: "late-task",
          type: "depth_review",
          status: "running",
          progress: 20,
          progressMessage: "处理中",
          createdAt: "2026-08-02T00:00:00Z",
        },
      ],
      recent: [],
    });
    const unsubscribe = useTaskStore
      .getState()
      .subscribeSSE("late-task", onUpdate, onDone, onError);
    MockEventSource.instances[0].onerror?.();
    await vi.advanceTimersByTimeAsync(2000);
    unsubscribe();

    resolvePoll?.(
      new Response(
        JSON.stringify({
          id: "late-task",
          type: "depth_review",
          status: "completed",
          progress: 100,
          progressMessage: "完成",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    await Promise.resolve();

    expect(onUpdate).not.toHaveBeenCalled();
    expect(onDone).not.toHaveBeenCalled();
    expect(onError).not.toHaveBeenCalled();
    expect(useTaskStore.getState().tasks[0]).toMatchObject({
      id: "late-task",
      status: "running",
      progress: 20,
    });
    expect(useTaskStore.getState().recent).toEqual([]);
  });

  it("moves a terminal polling response to recent", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          id: "poll-terminal",
          type: "depth_review",
          status: "completed",
          progress: 100,
          progressMessage: "完成",
          result: { ok: true },
        }),
      }),
    );
    const onDone = vi.fn();
    useTaskStore.setState({
      tasks: [
        {
          id: "poll-terminal",
          type: "depth_review",
          status: "running",
          progress: 10,
          progressMessage: "处理中",
          createdAt: "2026-08-02T00:00:00Z",
        },
      ],
      recent: [],
    });
    const unsubscribe = useTaskStore
      .getState()
      .subscribeSSE("poll-terminal", undefined, onDone);
    MockEventSource.instances[0].onerror?.();
    await vi.advanceTimersByTimeAsync(2000);
    await Promise.resolve();

    expect(onDone).toHaveBeenCalledWith(expect.objectContaining({ status: "completed" }));
    expect(useTaskStore.getState().tasks).toEqual([]);
    expect(useTaskStore.getState().recent[0]).toMatchObject({
      id: "poll-terminal",
      status: "completed",
    });
    unsubscribe();
  });

  it("ignores a late SSE error after a terminal event", () => {
    const onDone = vi.fn();
    const onError = vi.fn();
    useTaskStore.setState({
      tasks: [
        {
          id: "terminal-task",
          type: "depth_review",
          status: "running",
          progress: 100,
          progressMessage: "完成",
          createdAt: "2026-08-02T00:00:00Z",
        },
      ],
      recent: [],
    });
    const unsubscribe = useTaskStore
      .getState()
      .subscribeSSE("terminal-task", undefined, onDone, onError);
    const eventSource = MockEventSource.instances[0];

    eventSource.onmessage?.({
      data: JSON.stringify({
        taskId: "terminal-task",
        status: "completed",
        progress: 100,
        progressMessage: "完成",
      }),
    } as MessageEvent);
    eventSource.onerror?.();

    expect(onDone).toHaveBeenCalledTimes(1);
    expect(onError).not.toHaveBeenCalled();
    expect(useTaskStore.getState().recent[0]).toMatchObject({
      id: "terminal-task",
      status: "completed",
    });
    expect(useTaskStore.getState().tasks).toEqual([]);
    unsubscribe();
  });

  it("reports backend terminal failure through onError", () => {
    const onUpdate = vi.fn();
    const onError = vi.fn();

    useTaskStore.setState({
      tasks: [
        {
          id: "task-2",
          type: "reflection_review",
          status: "running",
          progress: 80,
          progressMessage: "评审中",
          createdAt: "2026-08-02T00:00:00Z",
        },
      ],
      recent: [],
    });
    const unsubscribe = useTaskStore
      .getState()
      .subscribeSSE("task-2", onUpdate, undefined, onError);
    const eventSource = MockEventSource.instances[0];

    eventSource.onmessage?.({
      data: JSON.stringify({
        taskId: "task-2",
        status: "failed",
        progress: 80,
        progressMessage: "评审失败",
        error: "模型不可用",
      }),
    } as MessageEvent);

    expect(onUpdate).not.toHaveBeenCalled();
    expect(onError).toHaveBeenCalledWith(
      expect.objectContaining({ id: "task-2", status: "failed", error: "模型不可用" }),
    );
    expect(useTaskStore.getState().tasks).toEqual([]);
    expect(useTaskStore.getState().recent[0]).toMatchObject({
      id: "task-2",
      status: "failed",
    });

    unsubscribe();
  });
});
