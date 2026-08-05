import { AlertCircle } from "lucide-react";
import { Modal } from "antd";


interface DeleteConfirmModalProps {
  open: boolean;
  onCancel: () => void;
  onOk: () => void;
  confirmLoading: boolean;
  selectedCount: number;
}

export default function DeleteConfirmModal({
  open,
  onCancel,
  onOk,
  confirmLoading,
  selectedCount,
}: DeleteConfirmModalProps) {
  return (
    <Modal
      title={
        <span>
          <AlertCircle style={{ color: "var(--pf-warning)", marginRight: 8 }} />
          确认删除
        </span>
      }
      open={open}
      onCancel={onCancel}
      onOk={onOk}
      confirmLoading={confirmLoading}
      okText="确认删除"
      cancelText="取消"
      okButtonProps={{ danger: true }}
    >
      <p style={{ fontSize: 14, color: "var(--pf-text-secondary)" }}>
        确认删除选中的 <strong>{selectedCount}</strong> 篇论文？
      </p>
      <p style={{ fontSize: 13, color: "var(--pf-text-placeholder)" }}>
        此操作将同时删除论文的收藏、笔记、批注等关联数据，以及本地上传的 PDF 文件，不可恢复。
      </p>
    </Modal>
  );
}
