import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { message, Modal } from "antd";
import { fetchDuplicateGroups, mergePapers } from "@/api/papers";
import type { DuplicateGroup } from "@/api/types";

export type FieldKey = "title" | "authors" | "year" | "abstract" | "journal" | "pdfUrl" | "tags";

export interface GroupSelection {
  target: string;
  fields: Record<FieldKey, string>;
}

export const FIELD_KEYS: FieldKey[] = [
  "title",
  "authors",
  "year",
  "abstract",
  "journal",
  "pdfUrl",
  "tags",
];

export function makeDefaultSelection(group: DuplicateGroup): GroupSelection {
  const first = group.papers[0]?.id ?? "";
  return {
    target: first,
    fields: {
      title: first,
      authors: first,
      year: first,
      abstract: first,
      journal: first,
      pdfUrl: first,
      tags: first,
    },
  };
}

export function groupKey(group: DuplicateGroup): string {
  return group.papers.map((p) => p.id).join("-");
}

export interface UseDuplicateGroupsReturn {
  groups: DuplicateGroup[];
  loading: boolean;
  mergingKey: string | null;
  selections: Record<string, GroupSelection>;
  handleMerge: (group: DuplicateGroup, key: string) => void;
  setTarget: (key: string, paperId: string) => void;
  setFieldSource: (key: string, field: FieldKey, paperId: string) => void;
  setAllFieldsFromPaper: (key: string, paperId: string) => void;
}

export function useDuplicateGroups(): UseDuplicateGroupsReturn {
  const { t } = useTranslation();
  const [groups, setGroups] = useState<DuplicateGroup[]>([]);
  const [loading, setLoading] = useState(false);
  const [mergingKey, setMergingKey] = useState<string | null>(null);
  const [selections, setSelections] = useState<Record<string, GroupSelection>>({});

  const loadGroups = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetchDuplicateGroups();
      const init: Record<string, GroupSelection> = {};
      res.groups.forEach((g) => {
        init[groupKey(g)] = makeDefaultSelection(g);
      });
      setGroups(res.groups);
      setSelections(init);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      message.error(msg || t("duplicates.loadError"));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    void loadGroups();
  }, [loadGroups]);

  const handleMerge = useCallback(
    (group: DuplicateGroup, key: string) => {
      const sel = selections[key];
      if (!sel) return;

      const sourceIds = group.papers.map((p) => p.id).filter((id) => id !== sel.target);

      if (sourceIds.length === 0) {
        message.warning(t("duplicates.noSourceToMerge"));
        return;
      }

      const targetPaper = group.papers.find((p) => p.id === sel.target);

      Modal.confirm({
        title: t("duplicates.confirmMergeTitle"),
        content: t("duplicates.confirmMergeContent", {
          target: targetPaper?.title || sel.target,
          count: sourceIds.length,
        }),
        okText: t("duplicates.merge"),
        okButtonProps: { danger: true },
        onOk: async () => {
          setMergingKey(key);
          try {
            const fieldSources: Record<string, string> = {};
            Object.entries(sel.fields).forEach(([field, paperId]) => {
              const backendField = field === "pdfUrl" ? "pdf_url" : field;
              fieldSources[backendField] = paperId;
            });
            await mergePapers({
              target_id: sel.target,
              source_ids: sourceIds,
              field_sources: fieldSources,
            });
            message.success(t("duplicates.mergeSuccess"));
            await loadGroups();
          } catch (err) {
            const msg = err instanceof Error ? err.message : String(err);
            message.error(msg || t("duplicates.mergeError"));
          } finally {
            setMergingKey(null);
          }
        },
      });
    },
    [loadGroups, selections, t],
  );

  const setTarget = useCallback((key: string, paperId: string) => {
    setSelections((prev) => ({
      ...prev,
      [key]: {
        ...prev[key],
        target: paperId,
      },
    }));
  }, []);

  const setFieldSource = useCallback((key: string, field: FieldKey, paperId: string) => {
    setSelections((prev) => ({
      ...prev,
      [key]: {
        ...prev[key],
        fields: { ...prev[key].fields, [field]: paperId },
      },
    }));
  }, []);

  const setAllFieldsFromPaper = useCallback((key: string, paperId: string) => {
    setSelections((prev) => ({
      ...prev,
      [key]: {
        ...prev[key],
        fields: FIELD_KEYS.reduce(
          (acc, field) => {
            acc[field] = paperId;
            return acc;
          },
          {} as Record<FieldKey, string>,
        ),
      },
    }));
  }, []);

  return {
    groups,
    loading,
    mergingKey,
    selections,
    handleMerge,
    setTarget,
    setFieldSource,
    setAllFieldsFromPaper,
  };
}
