import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { useDepthStore } from "@/store/useDepthStore";

const summary = {
  total: 2,
  completed: 2,
  failed: 0,
  highest_score: 91,
  average_score: 85,
};

beforeEach(async () => {
  await useDepthStore.persist.clearStorage();
  useDepthStore.setState({ tasks: [] });
});

afterEach(async () => {
  useDepthStore.setState({ tasks: [] });
  await useDepthStore.persist.clearStorage();
});

describe("useDepthStore task lifecycle", () => {
  it("ignores late progress, completion, and failure after cancellation", () => {
    const store = useDepthStore.getState();
    store.addTask("task-1", 2, ["paper-1", "paper-2"]);
    store.cancelTask("task-1");

    store.updateProgress("task-1", 2, 2);
    store.completeTask("task-1", [], summary);
    store.failTask("task-1", ["late failure"]);

    expect(useDepthStore.getState().tasks[0]).toMatchObject({
      taskId: "task-1",
      status: "canceled",
      progress: { current: 0, total: 2 },
      results: null,
      summary: null,
      errors: [],
    });
  });

  it("clears inactive tasks while preserving running tasks and retry data", () => {
    const store = useDepthStore.getState();
    store.addTask("running", 1, ["paper-running"]);
    store.addTask("finished", 1, ["paper-finished"]);
    store.completeTask("finished", [], summary);

    store.clearCompleted();

    expect(useDepthStore.getState().tasks).toHaveLength(1);
    expect(useDepthStore.getState().tasks[0]).toMatchObject({
      taskId: "running",
      status: "running",
      paperIds: ["paper-running"],
    });
  });

  it("migrates legacy persisted tasks with safe defaults", async () => {
    localStorage.setItem(
      "depth-tasks",
      JSON.stringify({
        state: {
          tasks: [
            {
              taskId: "legacy-task",
              status: "failed",
              progress: { current: "bad", total: -4 },
              summary: { total: "bad" },
              results: "bad",
              errors: ["legacy error", 42],
              submittedAt: 123,
            },
          ],
        },
        version: 0,
      }),
    );

    await useDepthStore.persist.rehydrate();

    expect(useDepthStore.getState().tasks[0]).toMatchObject({
      taskId: "legacy-task",
      paperIds: [],
      progress: { current: 0, total: 0 },
      summary: null,
      results: null,
      errors: ["legacy error"],
    });
  });
});
