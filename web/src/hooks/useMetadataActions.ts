import { Pencil, FileText, Tag, Zap } from "lucide-react";
import { useState, useCallback } from "react";
import { useTranslation } from "react-i18next";
import { message } from "antd";

import {
  enrichPaperMetadata,
  extractPaperDoi,
  renamePaperPdf,
  extractPaperAnnotations,
} from "@/api/papers";
import type { Paper } from "@/api/types";

export interface MetadataActionItem {
  key: string;
  icon: React.ComponentType<{ className?: string; style?: React.CSSProperties }>;
  label: string;
  onClick: (e?: { domEvent?: React.MouseEvent | React.KeyboardEvent }) => void;
  disabled?: boolean;
}

export interface UseMetadataActionsOptions {
  paper: Paper | null | undefined;
  onUpdate?: (updated: Paper) => void;
  onEnrichOpen?: () => void;
  onAnnotationsExtracted?: () => void;
}

export function useMetadataActions({
  paper,
  onUpdate,
  onEnrichOpen,
  onAnnotationsExtracted,
}: UseMetadataActionsOptions) {
  const { t } = useTranslation();
  const [loading, setLoading] = useState<Record<string, boolean>>({});

  const withLoading = useCallback(async (key: string, fn: () => Promise<void>) => {
    setLoading((prev) => ({ ...prev, [key]: true }));
    try {
      await fn();
    } finally {
      setLoading((prev) => ({ ...prev, [key]: false }));
    }
  }, []);

  const handleEnrich = useCallback(() => {
    if (!paper) return;
    if (onEnrichOpen) {
      onEnrichOpen();
      return;
    }
    withLoading("enrich", async () => {
      const updated = await enrichPaperMetadata(paper.id);
      message.success(t("paper.enrichSuccess", "元数据补全成功"));
      onUpdate?.(updated);
    });
  }, [paper, onEnrichOpen, onUpdate, t, withLoading]);

  const handleExtractDoi = useCallback(() => {
    if (!paper) return;
    withLoading("doi", async () => {
      const updated = await extractPaperDoi(paper.id);
      message.success(t("paper.extractDoiSuccess", "DOI 提取成功"));
      onUpdate?.(updated);
    });
  }, [paper, onUpdate, t, withLoading]);

  const handleRenamePdf = useCallback(() => {
    if (!paper) return;
    withLoading("rename", async () => {
      const updated = await renamePaperPdf(paper.id);
      message.success(t("paper.renamePdfSuccess", "PDF 重命名成功"));
      onUpdate?.(updated);
    });
  }, [paper, onUpdate, t, withLoading]);

  const handleExtractAnnotations = useCallback(() => {
    if (!paper) return;
    withLoading("annotations", async () => {
      const { count } = await extractPaperAnnotations(paper.id);
      message.success(t("paper.extractAnnotationsSuccess", "提取到 {{count}} 条批注", { count }));
      onAnnotationsExtracted?.();
    });
  }, [paper, t, withLoading, onAnnotationsExtracted]);

  const items: MetadataActionItem[] = paper
    ? [
        {
          key: "enrich",
          icon: Zap,
          label: t("paper.enrichMetadata", "补全元数据"),
          onClick: handleEnrich,
        },
        {
          key: "doi",
          icon: Tag,
          label: t("paper.extractDoi", "提取 DOI"),
          onClick: handleExtractDoi,
        },
        {
          key: "rename",
          icon: Pencil,
          label: t("paper.renamePdf", "重命名 PDF"),
          onClick: handleRenamePdf,
        },
        {
          key: "annotations",
          icon: FileText,
          label: t("paper.extractAnnotations", "提取批注"),
          onClick: handleExtractAnnotations,
        },
      ]
    : [];

  return {
    items,
    loading,
    handleEnrich,
    handleExtractDoi,
    handleRenamePdf,
    handleExtractAnnotations,
  };
}

export default useMetadataActions;
