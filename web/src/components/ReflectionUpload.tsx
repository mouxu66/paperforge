import { Send, BookOpen, FileText, Inbox, XCircle, CheckCircle, Loader, Trash2 } from "lucide-react";
/**
 * ReflectionUpload 组件：弹出 Modal 让用户提交感悟/读后/复现报告。
 *
 * 支持两种提交方式（Tabs 切换）：
 * - 「粘贴文本」：单篇文本（POST /api/depth/reflection/text，JSON body）
 * - 「上传文件」：多文件批量（POST /api/depth/reflection/files，multipart）
 *   - 多文件 picker (antd Dragger multiple=true)，单次最多 20 个
 *   - 每个文件独立处理：抽取 → 去重 → 入库 → reflection 评审
 *   - 逐项返回结果，per-file 状态（pending/uploading/done/failed）
 *   - 每个 taskId 独立订阅 SSE
 *
 * 流程（多文件版）：
 * 1. 用户在文件 tab 拖拽/选择 N 个文件（PDF / DOCX / TXT / MD）
 * 2. 文件列表显示在 Dragger 下方，每项带文件名/大小/移除按钮
 * 3. 点击「批量提交」→ 走 /api/depth/reflection/files（202 + items[]）
 * 4. 对每个 accepted 的 taskId 独立订阅 SSE
 * 5. 文件列表中每项实时显示状态（pending → uploading → done/failed）
 * 6. 全部完成后关闭 modal 并调用 onSuccess 回调
 *
 * 【为什么用 Modal 而不是独立页面】
 * - 报告长度通常 500~3000 字，独立页面太重
 * - Modal 可以在 HomePage / DepthReview 报告 Tab 顶部复用
 * - 提交中状态在 modal 内展示，避免页面跳转打断上下文
 */
import { useState, useRef, useEffect } from "react";
import {
  Modal,
  Form,
  Input,
  Button,
  App,
  Space,
  Typography,
  Alert,
  Progress,
  Select,
  InputNumber,
  Tabs,
  Upload,
  Tag,
  List,
  type UploadProps,
} from "antd";

import { useTranslation } from "react-i18next";
import {
  createReflectionReport,
  uploadReflectionReports,
  type ReflectionFileBatchItem,
  type ReflectionFileBatchResponse,
  type ReflectionUploadRequest,
  type SourcePaperInfo,
} from "@/api/reflection";
import { useTaskStore } from "@/store/useTaskStore";
import { useReflectionStore } from "@/store/useReflectionStore";

const { TextArea } = Input;
const { Text } = Typography;
const { Dragger } = Upload;

interface ReflectionUploadProps {
  /** Modal 打开状态 */
  open: boolean;
  /** 关闭回调（成功或取消都触发） */
  onClose: () => void;
  /** 成功提交后回调（让父组件刷新列表 / 切换 Tab） */
  onSuccess?: (paperId: string) => void;
  /** 关联的源论文 ID（仅在「对某篇论文的读后感」场景使用） */
  sourcePaperId?: string;
  /** 关联的源论文标题（仅展示） */
  sourcePaperTitle?: string;
}

interface FormValues {
  title: string;
  content: string;
  authors: string[];
  year?: number | null;
}

interface FileFormValues {
  title: string;
  authorsCsv: string;
  year?: number | null;
}

/** 单文件在批量上传中的状态 */
type FileItemStatus = "pending" | "uploading" | "done" | "failed";
interface FileItem {
  /** 文件对象（用户选择的原始 File） */
  file: File;
  /** 唯一 key（filename + size + index 避免重复） */
  key: string;
  /** 状态 */
  status: FileItemStatus;
  /** 后端返回的 paperId（成功后填充） */
  paperId?: string;
  /** 后端返回的 taskId（订阅 SSE 用） */
  taskId?: string | null;
  /** 错误信息（失败时填充） */
  error?: string;
  /** 原论文自动识别/导入结果 */
  sourcePaper?: SourcePaperInfo | null;
}

const CURRENT_YEAR = new Date().getFullYear();
const ALLOWED_FILE_EXTS = [".pdf", ".docx", ".txt", ".md"];
const ALLOWED_FILE_TYPES = ".pdf,.docx,.txt,.md";
const MAX_FILE_SIZE_MB = 10;
/** 单次批量最多文件数（与后端 MAX_BATCH_SIZE 一致） */
const MAX_BATCH_FILES = 20;

let _fileItemKeySeq = 0;
const nextFileItemKey = () => `fi_${Date.now()}_${++_fileItemKeySeq}`;

/** 将 sourcePaper 状态渲染为用户友好的标签与提示 */
function formatSourcePaper(sp?: SourcePaperInfo | null): { color: string; text: string } | null {
  if (!sp) return null;
  const sourceLabel =
    sp.source === "semantic_scholar" ? "Semantic Scholar" : sp.source === "arxiv" ? "arXiv" : "";
  const sourceSuffix = sourceLabel ? `（${sourceLabel}）` : "";
  switch (sp.status) {
    case "imported":
      return {
        color: "green",
        text: `已自动导入《${sp.title || "未知论文"}》${sourceSuffix}`,
      };
    case "exists":
      return {
        color: "blue",
        text: `已关联库中《${sp.title || "原论文"}》${sourceSuffix}`,
      };
    case "manual":
      return { color: "blue", text: "已手动关联源论文" };
    case "no_title":
      return { color: "orange", text: "未识别到原论文题目" };
    case "rate_limited":
      return { color: "orange", text: "arXiv 限流，稍后自动重试" };
    case "no_match":
      return { color: "red", text: "未在 arXiv 找到匹配" };
    case "import_failed":
      return { color: "red", text: "原论文导入失败" };
    default:
      return null;
  }
}

export default function ReflectionUpload({
  open,
  onClose,
  onSuccess,
  sourcePaperId,
  sourcePaperTitle,
}: ReflectionUploadProps) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [textForm] = Form.useForm<FormValues>();
  const [fileForm] = Form.useForm<FileFormValues>();
  const { subscribeSSE } = useTaskStore();
  const { addUpload, updateByTaskId } = useReflectionStore();

  const [activeTab, setActiveTab] = useState<"text" | "file">("text");
  const [submitting, setSubmitting] = useState(false);
  const [progress, setProgress] = useState(0);
  const [progressMsg, setProgressMsg] = useState("");
  const [error, setError] = useState<string | null>(null);
  // 多文件列表（替代单文件版的 pickedFile）
  const [fileItems, setFileItems] = useState<FileItem[]>([]);
  const unsubscribeRef = useRef<(() => void) | null>(null);
  // 【修复 SSE 泄漏】批量模式下每个文件订阅一个 SSE，需要逐个清理。
  // 文本模式只用 unsubscribeRef（保留单 ref 避免不必要改动）。
  const batchUnsubsRef = useRef<Set<() => void>>(new Set());
  // 【修复 onSuccess 重复】记录批量提交时的总文件数，仅在最后一个文件完成时
  // 触发一次 onSuccess（避免 20-file 批量触发 20 次父组件列表刷新）。
  const batchTotalRef = useRef<number>(0);
  // 文件上传完成后置 true，防卫 axios onUploadProgress 在 promise resolve 后
  // 仍然偶然跳出事件（如 keep-alive / HTTP/2 场景）导致进度条倒退。
  const uploadDoneRef = useRef<boolean>(false);

  // Modal 关闭时清理 SSE 订阅 + 重置表单
  useEffect(() => {
    if (!open) {
      unsubscribeRef.current?.();
      unsubscribeRef.current = null;
      // 清理所有批量模式 SSE 订阅（避免 EventSource 连接泄漏）
      batchUnsubsRef.current.forEach((u) => u());
      batchUnsubsRef.current.clear();
      batchTotalRef.current = 0;
      setProgress(0);
      setProgressMsg("");
      setError(null);
      setSubmitting(false);
      setFileItems([]);
      setActiveTab("text");
      textForm.resetFields();
      fileForm.resetFields();
    }
    const batchUnsubs = batchUnsubsRef.current;
    return () => {
      unsubscribeRef.current?.();
      unsubscribeRef.current = null;
      batchUnsubs.forEach((u) => u());
      batchUnsubs.clear();
    };
  }, [open, textForm, fileForm]);

  /** 文本模式：单报告完成回调 */
  const handleTaskComplete = (paperId: string, taskId: string | null) => {
    setProgress(100);
    setProgressMsg("评审完成");
    if (taskId) updateByTaskId(taskId, "completed");
    message.success("报告评审完成");
    setTimeout(() => {
      onSuccess?.(paperId);
      onClose();
    }, 800);
  };

  const handleTaskFail = (taskId: string | null, errMsg: string) => {
    setError(errMsg);
    if (taskId) updateByTaskId(taskId, "failed", errMsg);
    setSubmitting(false);
  };

  /** 订阅单 task 的 SSE 进度 */
  const subscribeToTask = (taskId: string, paperId: string) => {
    setProgress((prev) => Math.max(prev, 10));
    setProgressMsg("已提交，等待评审...");
    const unsub = subscribeSSE(
      taskId,
      (info) => {
        setProgress((prev) => Math.max(prev, info.progress));
        setProgressMsg(info.progressMessage || "评审中...");
        updateByTaskId(taskId, info.status);
      },
      () => handleTaskComplete(paperId, taskId),
      (info) =>
        handleTaskFail(
          taskId,
          info.status === "timed_out" ? "评审超时" : info.error || "评审失败",
        ),
    );
    unsubscribeRef.current = unsub;
  };

  // 文本模式提交
  const handleTextSubmit = async (values: FormValues) => {
    setError(null);
    setSubmitting(true);
    setProgress(5);
    setProgressMsg("提交中...");

    const payload: ReflectionUploadRequest = {
      title: values.title.trim(),
      content: values.content.trim(),
      authors: values.authors,
      year: values.year,
      sourcePaperId: sourcePaperId ?? null,
    };

    try {
      const resp = await createReflectionReport(payload);
      addUpload(resp.paperId, values.title, resp.taskId);
      message.success(`已提交（task_id=${resp.taskId ?? "pending"}）`);
      if (resp.taskId) {
        subscribeToTask(resp.taskId, resp.paperId);
      } else {
        // 后端未返回 taskId（理论上不会发生）—— 直接关闭
        handleTaskComplete(resp.paperId, null);
      }
    } catch (err0: unknown) {
      const e = err0 as Error & { response?: { status?: number; data?: { detail?: string } } };
      const errMsg = e?.response?.data?.detail || e?.message || "提交失败";
      handleTaskFail(null, errMsg);
      setProgress(0);
    }
  };

  /**
   * 多文件模式提交：
   * 1. 设置所有文件状态为 uploading
   * 2. POST /api/depth/reflection/files（含全部 files）
   * 3. 根据 response.items 逐项更新状态
   * 4. 对 accepted 的 taskId 独立订阅 SSE
   * 5. 聚合进度：成功数 / 总数
   */
  const handleBatchFileSubmit = async (values: FileFormValues) => {
    if (fileItems.length === 0) {
      setError(t("reflection.upload.fileRequired", "请先选择要上传的文件"));
      return;
    }
    // 二次校验：单文件大小（前端先过滤一次，后端再校验）
    const oversize = fileItems.filter((it) => it.file.size > MAX_FILE_SIZE_MB * 1024 * 1024);
    if (oversize.length > 0) {
      setError(
        t(
          "reflection.upload.fileTooLarge",
          `${oversize.length} 个文件超过 ${MAX_FILE_SIZE_MB}MB 上限：${oversize
            .map((it) => it.file.name)
            .join(", ")}`,
        ),
      );
      return;
    }

    setError(null);
    setSubmitting(true);
    setProgress(0);
    setProgressMsg("准备上传...");
    uploadDoneRef.current = false;

    // 全部标记为 uploading
    setFileItems((prev) => prev.map((it) => ({ ...it, status: "uploading" as FileItemStatus })));

    const meta = {
      title: values.title?.trim() || undefined,
      authorsCsv: values.authorsCsv?.trim() || undefined,
      year: values.year || undefined,
      sourcePaperId: sourcePaperId || undefined,
    };
    const files = fileItems.map((it) => it.file);
    try {
      const resp: ReflectionFileBatchResponse = await uploadReflectionReports(
        files,
        meta,
        (percent) => {
          if (uploadDoneRef.current) return;
          // 上传阶段只走到 50%，剩余留给 SSE
          setProgress(Math.min(percent / 2, 50));
          setProgressMsg(`上传中 ${percent}%...（${fileItems.length} 个文件）`);
        },
      );
      uploadDoneRef.current = true;
      // 后端开始处理：固定在 50% 等待 SSE 接管
      setProgress(50);
      setProgressMsg(`已提交 ${resp.items.length} 个文件，等待评审...`);

      // 记录本次批量提交的总文件数（用于 onSuccess 防抖）
      const acceptedCount = resp.items.filter((it) => it.status === "accepted" && it.taskId).length;
      batchTotalRef.current = acceptedCount;

      // 根据后端返回的 items 逐项更新状态 + 订阅 SSE
      const byFilename: Record<string, ReflectionFileBatchItem> = {};
      for (const it of resp.items) byFilename[it.filename] = it;
      setFileItems((prev) =>
        prev.map((local) => {
          const remote = byFilename[local.file.name];
          if (!remote) return local;
          if (remote.status === "accepted" && remote.taskId) {
            addUpload(remote.paperId || "", local.file.name, remote.taskId);
            // 订阅 SSE
            subscribeToTaskPerFile(remote.taskId, remote.paperId || "", local.key);
            return {
              ...local,
              // 保持 uploading 状态；SSE 完成回调会翻转为 done/failed。
              // 之前误设为 'done' 会让文件在评审完成前就显示绿色勾（30s 误判）。
              status: "uploading",
              paperId: remote.paperId || undefined,
              taskId: remote.taskId,
              sourcePaper: remote.sourcePaper,
            };
          }
          if (remote.status === "failed") {
            return { ...local, status: "failed", error: remote.error };
          }
          return local;
        }),
      );
      message.success(
        resp.failed > 0
          ? `已处理 ${resp.total} 个文件：${resp.submitted} 成功 / ${resp.failed} 失败`
          : `已提交 ${resp.submitted} 份报告`,
      );
    } catch (err0: unknown) {
      const e = err0 as Error & { response?: { status?: number; data?: { detail?: string } } };
      uploadDoneRef.current = true;
      const errMsg = e?.response?.data?.detail || e?.message || "上传失败";
      handleTaskFail(null, errMsg);
      setFileItems((prev) =>
        prev.map((it) =>
          it.status === "uploading" ? { ...it, status: "failed", error: errMsg } : it,
        ),
      );
      setProgress(0);
    }
  };

  /**
   * 单文件 SSE 订阅（不更新顶层 progress —— 多文件版进度由聚合统计驱动）
   * 通过闭包持有 fileKey 用于更新该文件的最终状态
   *
   * 【修复 SSE 泄漏】将 unsubscribe 函数存入 batchUnsubsRef，
   * modal 关闭时统一清理，避免 EventSource 连接泄漏。
   */
  const subscribeToTaskPerFile = (taskId: string, paperId: string, fileKey: string) => {
    const unsub = subscribeSSE(
      taskId,
      (_info) => {
        // 不更新顶层 progress，由聚合统计驱动
        updateByTaskId(taskId, _info.status);
      },
      () => {
        // completed
        setFileItems((prev) =>
          prev.map((it) => (it.key === fileKey ? { ...it, status: "done" } : it)),
        );
        // 仅在最后一个 accepted 文件完成时触发一次 onSuccess
        // （失败的文件不计入，避免因某个失败一直卡住不回调）
        batchTotalRef.current = Math.max(0, batchTotalRef.current - 1);
        if (batchTotalRef.current === 0 && paperId) {
          onSuccess?.(paperId);
        }
      },
      (info) => {
        // failed / timed_out
        setFileItems((prev) =>
          prev.map((it) =>
            it.key === fileKey
              ? {
                  ...it,
                  status: "failed",
                  error: info.status === "timed_out" ? "评审超时" : info.error || "评审失败",
                }
              : it,
          ),
        );
        // 失败也递减，避免 accepted 全完成但某文件卡 running 时 onSuccess 永不触发
        batchTotalRef.current = Math.max(0, batchTotalRef.current - 1);
      },
    );
    batchUnsubsRef.current.add(unsub);
  };

  /**
   * 聚合进度 = 已完成 (done) 数量 / 总数 × 100
   * 用于驱动顶层 Progress 条
   */
  const aggregateProgress =
    fileItems.length === 0
      ? 0
      : Math.round(
          (fileItems.filter((it) => it.status === "done" || it.status === "failed").length /
            fileItems.length) *
            100,
        );
  const completedCount = fileItems.filter((it) => it.status === "done").length;
  const failedCount = fileItems.filter((it) => it.status === "failed").length;

  /**
   * 切换 tab 时清空 file 模式选择的文件
   */
  const handleTabChange = (k: string) => {
    if (submitting) return;
    const next = k as "text" | "file";
    if (next !== activeTab) {
      setFileItems([]);
      fileForm.resetFields();
    }
    setActiveTab(next);
  };

  /** 移除单个文件（提交前） */
  const removeFileItem = (key: string) => {
    if (submitting) return;
    setFileItems((prev) => prev.filter((it) => it.key !== key));
  };

  /** 清空所有文件 */
  const clearAllFileItems = () => {
    if (submitting) return;
    setFileItems([]);
  };

  // Dragger beforeUpload：累积多文件到 fileItems
  const draggerProps: UploadProps = {
    multiple: true,
    showUploadList: false,
    accept: ALLOWED_FILE_TYPES,
    beforeUpload: (file) => {
      // antd 在多选模式下 beforeUpload 会被每个文件分别调用；fileList 是当前批次的全部文件
      const ext = "." + (file.name.split(".").pop() || "").toLowerCase();
      if (!ALLOWED_FILE_EXTS.includes(ext)) {
        message.error(
          t("reflection.upload.fileTypeError", `不支持的文件类型 ${ext}：${file.name}`),
        );
        return Upload.LIST_IGNORE;
      }
      if (file.size > MAX_FILE_SIZE_MB * 1024 * 1024) {
        message.error(
          t("reflection.upload.fileTooLarge", `文件超过 ${MAX_FILE_SIZE_MB}MB：${file.name}`),
        );
        return Upload.LIST_IGNORE;
      }
      // 累积到 fileItems（去重：filename + size 相同的不重复添加）
      setFileItems((prev) => {
        if (prev.some((it) => it.file.name === file.name && it.file.size === file.size)) {
          return prev;
        }
        return [
          ...prev,
          {
            file,
            key: nextFileItemKey(),
            status: "pending",
          },
        ];
      });
      // 自动用第一个文件名（去扩展名）作为默认标题候选
      if (!fileForm.getFieldValue("title")) {
        const stem = file.name.replace(/\.[^.]+$/, "");
        fileForm.setFieldValue("title", stem);
      }
      return false; // 阻止 antd 默认上传
    },
  };

  return (
    <Modal
      title={
        <Space>
          <BookOpen style={{ color: "var(--pf-primary)" }} />
          {t("reflection.upload.title", "提交感悟报告")}
        </Space>
      }
      open={open}
      onCancel={onClose}
      footer={null}
      width={680}
      destroyOnHidden
      mask={{ closable: !submitting }}
      closable={!submitting}
    >
      {error && (
        <Alert
          type="error"
          message={error}
          showIcon
          closable
          onClose={() => setError(null)}
          style={{ marginBottom: 16 }}
        />
      )}

      {submitting && (
        <div style={{ marginBottom: 16 }}>
          <Progress
            percent={Math.max(progress, aggregateProgress)}
            status={progress >= 100 || aggregateProgress >= 100 ? "success" : "active"}
            strokeColor="#6366f1"
          />
          <Text type="secondary" style={{ fontSize: 12 }}>
            {progressMsg}
            {fileItems.length > 1 &&
              `（${completedCount}/${fileItems.length} 完成，${failedCount} 失败）`}
          </Text>
        </div>
      )}

      {sourcePaperId && (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
          message={
            <span>
              关联论文：<Text strong>{sourcePaperTitle || sourcePaperId}</Text>
            </span>
          }
        />
      )}

      <Tabs
        activeKey={activeTab}
        onChange={handleTabChange}
        items={[
          {
            key: "text",
            label: (
              <span>
                <FileText /> {t("reflection.upload.tabText", "粘贴文本")}
              </span>
            ),
            children: (
              <Form
                form={textForm}
                layout="vertical"
                onFinish={handleTextSubmit}
                disabled={submitting}
                initialValues={{ authors: [], year: CURRENT_YEAR }}
              >
                <Form.Item
                  label="报告标题"
                  name="title"
                  rules={[
                    { required: true, message: "请输入报告标题" },
                    { max: 200, message: "标题不能超过 200 字" },
                  ]}
                >
                  <Input placeholder="如：读《Attention Is All You Need》有感" />
                </Form.Item>

                <Form.Item label="作者（可选）" name="authors" tooltip="回车添加多个作者">
                  <Select
                    mode="tags"
                    placeholder="输入作者后回车"
                    tokenSeparators={[",", "，"]}
                    maxTagCount={5}
                  />
                </Form.Item>

                <Form.Item label="年份（可选）" name="year">
                  <InputNumber
                    min={1900}
                    max={CURRENT_YEAR + 1}
                    placeholder={String(CURRENT_YEAR)}
                    style={{ width: 120 }}
                  />
                </Form.Item>

                <Form.Item
                  label="报告内容"
                  name="content"
                  rules={[
                    { required: true, message: "请输入报告内容" },
                    { min: 50, message: "内容至少 50 字（评审需要充分证据）" },
                  ]}
                  extra="建议至少 200 字，包含具体观点、引用、和自己的思考。LLM 将从中提取证据并打分。"
                >
                  <TextArea
                    rows={10}
                    placeholder={
                      "请撰写你的感悟/读后/复现报告。建议结构：\n" +
                      "1. 简述原论文的核心方法\n" +
                      "2. 你的理解与思考（理解准确性）\n" +
                      "3. 对细节的深入分析（分析深度）\n" +
                      "4. 你的创新观点或批判（创新性见解）\n" +
                      "5. 引用支撑（观点必须有逻辑或引用支撑）"
                    }
                    showCount
                    maxLength={20000}
                  />
                </Form.Item>

                <Form.Item style={{ marginBottom: 0, textAlign: "right" }}>
                  <Space>
                    <Button onClick={onClose} disabled={submitting}>
                      取消
                    </Button>
                    <Button
                      type="primary"
                      htmlType="submit"
                      icon={<Send />}
                      loading={submitting}
                    >
                      {submitting ? "评审中..." : "提交评审"}
                    </Button>
                  </Space>
                </Form.Item>
              </Form>
            ),
          },
          {
            key: "file",
            label: (
              <span>
                <Inbox /> {t("reflection.upload.tabFile", "上传文件")}
                {fileItems.length > 0 && (
                  <Tag color="blue" style={{ marginLeft: 6 }}>
                    {fileItems.length}
                  </Tag>
                )}
              </span>
            ),
            children: (
              <Form
                form={fileForm}
                layout="vertical"
                onFinish={handleBatchFileSubmit}
                disabled={submitting}
                initialValues={{ title: "", authorsCsv: "", year: CURRENT_YEAR }}
              >
                <Form.Item>
                  <Dragger {...draggerProps} style={{ padding: "8px 0" }}>
                    <p className="ant-upload-drag-icon">
                      <Inbox style={{ color: "#6366f1" }} />
                    </p>
                    <p className="ant-upload-text">
                      {t(
                        "reflection.upload.fileDropHint",
                        `点击或拖拽文件到此区域上传（支持 PDF / DOCX / TXT / MD，单次最多 ${MAX_BATCH_FILES} 个，每个最大 ${MAX_FILE_SIZE_MB}MB）`,
                      )}
                    </p>
                    <p className="ant-upload-hint" style={{ fontSize: 12 }}>
                      {t(
                        "reflection.upload.fileTypes",
                        "后端会按扩展名分发抽取：PDF → pypdf，DOCX → python-docx，TXT/MD → UTF-8 文本",
                      )}
                    </p>
                  </Dragger>
                </Form.Item>

                {fileItems.length > 0 && (
                  <div style={{ marginBottom: 16 }}>
                    <div
                      style={{
                        display: "flex",
                        justifyContent: "space-between",
                        alignItems: "center",
                        marginBottom: 8,
                      }}
                    >
                      <Text strong>已选择 {fileItems.length} 个文件：</Text>
                      {!submitting && fileItems.length > 1 && (
                        <Button
                          size="small"
                          type="text"
                          icon={<Trash2 />}
                          onClick={clearAllFileItems}
                        >
                          清空
                        </Button>
                      )}
                    </div>
                    <List
                      size="small"
                      bordered
                      dataSource={fileItems}
                      renderItem={(it) => (
                        <List.Item
                          actions={
                            !submitting && it.status === "pending"
                              ? [
                                  <Button
                                    key="del"
                                    type="text"
                                    size="small"
                                    icon={<Trash2 />}
                                    onClick={() => removeFileItem(it.key)}
                                  />,
                                ]
                              : undefined
                          }
                        >
                          <Space>
                            {it.status === "pending" && <FileText />}
                            {it.status === "uploading" && (
                              <Loader style={{ color: "#6366f1" }} />
                            )}
                            {it.status === "done" && (
                              <CheckCircle style={{ color: "#52c41a" }} />
                            )}
                            {it.status === "failed" && (
                              <XCircle style={{ color: "#ff4d4f" }} />
                            )}
                            <Text style={{ maxWidth: 280 }} ellipsis>
                              {it.file.name}
                            </Text>
                            <Text type="secondary" style={{ fontSize: 12 }}>
                              ({(it.file.size / 1024).toFixed(1)} KB)
                            </Text>
                            {(() => {
                              const spLabel = formatSourcePaper(it.sourcePaper);
                              return spLabel ? (
                                <Tag color={spLabel.color} style={{ fontSize: 12 }}>
                                  {spLabel.text}
                                </Tag>
                              ) : null;
                            })()}
                            {it.status === "failed" && it.error && (
                              <Text type="danger" style={{ fontSize: 12 }}>
                                {it.error}
                              </Text>
                            )}
                          </Space>
                        </List.Item>
                      )}
                    />
                  </div>
                )}

                <Form.Item
                  label={t(
                    "reflection.upload.fileTitleLabel",
                    "报告标题（可选，所有文件共用；留空使用文件元数据/文件名）",
                  )}
                  name="title"
                  tooltip="如文件 PDF 内嵌标题，后端会自动抽取"
                >
                  <Input placeholder="默认使用第一个文件名（去扩展名）" />
                </Form.Item>

                <Form.Item
                  label={t(
                    "reflection.upload.fileAuthorsLabel",
                    "作者（可选，逗号分隔，所有文件共用）",
                  )}
                  name="authorsCsv"
                >
                  <Input placeholder="如：张三, 李四" />
                </Form.Item>

                <Form.Item label={t("reflection.upload.fileYearLabel", "年份（可选）")} name="year">
                  <InputNumber
                    min={1900}
                    max={CURRENT_YEAR + 1}
                    placeholder={String(CURRENT_YEAR)}
                    style={{ width: 120 }}
                  />
                </Form.Item>

                <Form.Item style={{ marginBottom: 0, textAlign: "right" }}>
                  <Space>
                    <Button onClick={onClose} disabled={submitting}>
                      取消
                    </Button>
                    <Button
                      type="primary"
                      htmlType="submit"
                      icon={<Send />}
                      loading={submitting}
                      disabled={fileItems.length === 0}
                    >
                      {submitting
                        ? `批量评审中...（${completedCount}/${fileItems.length}）`
                        : t(
                            "reflection.upload.batchFileSubmit",
                            `批量提交评审（${fileItems.length} 个）`,
                          )}
                    </Button>
                  </Space>
                </Form.Item>
              </Form>
            ),
          },
        ]}
      />
    </Modal>
  );
}
