import { Progress } from "antd";
import type { TaskInfo } from "@/store/useTaskStore";

interface V4ReviewProgressProps {
  task: TaskInfo | null;
}

export default function V4ReviewProgress({ task }: V4ReviewProgressProps) {
  if (!task) return null;

  if (task.status === "pending" || task.status === "running") {
    return (
      <div
        style={{
          marginBottom: 16,
          padding: "12px 16px",
          background: "var(--pf-primary-soft)",
          borderRadius: 8,
          border: "1px solid var(--pf-primary)",
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            marginBottom: 8,
          }}
        >
          <span style={{ fontSize: 13, fontWeight: 600, color: "var(--pf-primary)" }}>
            {task.status === "pending" ? "⏳ 批量审稿排队中…" : "⚡ 批量审稿进行中"}
          </span>
          <span style={{ fontSize: 12, color: "var(--pf-text-muted)" }}>
            {task.progressMessage || `${task.progress}%`}
          </span>
        </div>
        <Progress
          percent={task.progress}
          status="active"
          strokeColor="var(--pf-primary)"
          size="small"
        />
      </div>
    );
  }

  if (task.status === "completed") {
    return (
      <div
        style={{
          marginBottom: 16,
          padding: "8px 16px",
          background: "#f0fdf4",
          borderRadius: 8,
          border: "1px solid #bbf7d0",
        }}
      >
        <span style={{ fontSize: 13, fontWeight: 600, color: "#166534" }}>✅ 批量审稿已完成</span>
      </div>
    );
  }

  if (task.status === "failed") {
    return (
      <div
        style={{
          marginBottom: 16,
          padding: "8px 16px",
          background: "#fef2f2",
          borderRadius: 8,
          border: "1px solid #fecaca",
        }}
      >
        <span style={{ fontSize: 13, fontWeight: 600, color: "#991b1b" }}>
          ❌ 批量审稿失败: {task.error || "未知错误"}
        </span>
      </div>
    );
  }

  return null;
}
