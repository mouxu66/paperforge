import { Radar } from "lucide-react";
import { Modal } from "antd";

import type { Paper } from "@/api/types";

interface DepthEvalModalProps {
  open: boolean;
  onCancel: () => void;
  onOk: () => void;
  confirmLoading: boolean;
  selectedPapers: Paper[];
  estimatedMinutes: number;
}

export default function DepthEvalModal({
  open,
  onCancel,
  onOk,
  confirmLoading,
  selectedPapers,
  estimatedMinutes,
}: DepthEvalModalProps) {
  return (
    <Modal
      title={
        <span>
          <Radar style={{ color: "var(--pf-primary)", marginRight: 8 }} />
          DEPTH 评估确认
        </span>
      }
      open={open}
      onCancel={onCancel}
      onOk={onOk}
      confirmLoading={confirmLoading}
      okText={`确认提交 ${selectedPapers.length} 篇`}
      cancelText="取消"
      width={560}
      destroyOnHidden
    >
      <div style={{ marginBottom: 12 }}>
        <div style={{ fontSize: 14, color: "var(--pf-text-secondary)", marginBottom: 8 }}>
          即将对以下 <strong>{selectedPapers.length}</strong> 篇论文进行 DEPTH 多维评分：
        </div>
        <div
          style={{
            maxHeight: 200,
            overflow: "auto",
            border: "1px solid var(--pf-border-light)",
            borderRadius: 6,
            padding: "8px 12px",
            background: "var(--pf-bg-tertiary)",
          }}
        >
          {selectedPapers.map((p, i) => (
            <div
              key={p.id}
              style={{
                fontSize: 13,
                padding: "4px 0",
                borderBottom:
                  i < selectedPapers.length - 1 ? "1px solid var(--pf-border-light)" : "none",
                display: "flex",
                alignItems: "baseline",
                gap: 8,
              }}
            >
              <span style={{ color: "var(--pf-text-placeholder)", flexShrink: 0, width: 22 }}>
                {i + 1}.
              </span>
              <span
                style={{
                  flex: 1,
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
              >
                {p.title}
              </span>
              <span style={{ color: "var(--pf-text-placeholder)", fontSize: 12, flexShrink: 0 }}>
                {p.authors?.[0]?.split(" ").pop() || "—"} · {p.year || "—"}
              </span>
            </div>
          ))}
        </div>
      </div>

      <div
        style={{
          fontSize: 13,
          color: "var(--pf-text-muted)",
          background: "var(--pf-bg-tertiary)",
          borderRadius: 6,
          padding: "10px 14px",
          lineHeight: 1.7,
        }}
      >
        <div>
          ⏱️ 预计耗时：<strong>约 {estimatedMinutes} 分钟</strong>
          <span style={{ fontSize: 12, color: "var(--pf-text-placeholder)", marginLeft: 8 }}>
            （每篇 ~30 秒，共 5 轮 LLM 评估）
          </span>
        </div>
        <div style={{ marginTop: 4, fontSize: 12, color: "var(--pf-text-placeholder)" }}>
          提交后可在 DEPTH 页面查看实时进度，也可离开页面稍后回来查看。
        </div>
      </div>
    </Modal>
  );
}
