import { Search } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { AutoComplete, Select, Space, Input, Switch, Tooltip } from "antd";

import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { fetchSuggest } from "@/api/papers";
import type { SuggestItem } from "@/api/types";
import { SORT_OPTIONS } from "@/utils/constants";

/** 将含 <mark> 标签的高亮文本安全渲染为 React 节点（无 dangerouslySetInnerHTML） */
function renderHighlight(html: string): React.ReactNode {
  const parts = html.split(/(<mark>|<\/mark>)/g);
  const nodes: React.ReactNode[] = [];
  let inMark = false;
  for (const part of parts) {
    if (part === "<mark>") {
      inMark = true;
    } else if (part === "</mark>") {
      inMark = false;
    } else if (part) {
      nodes.push(
        inMark ? (
          <mark
            key={nodes.length}
            style={{
              background: "#fef08a",
              color: "var(--pf-text-secondary)",
              borderRadius: 2,
              padding: "0 2px",
            }}
          >
            {part}
          </mark>
        ) : (
          part
        ),
      );
    }
  }
  return nodes;
}

interface Props {
  keyword: string;
  sort: string;
  semantic: boolean;
  onKeyword: (v: string) => void;
  onSort: (v: string) => void;
  onSemantic: (v: boolean) => void;
}

export default function SearchBar({
  keyword,
  sort,
  semantic,
  onKeyword,
  onSort,
  onSemantic,
}: Props) {
  const navigate = useNavigate();
  const { t } = useTranslation();
  const [options, setOptions] = useState<{ value: string; label: React.ReactNode }[]>([]);
  const [inputValue, setInputValue] = useState(keyword);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const keywordDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const requestIdRef = useRef(0);
  const latestValueRef = useRef(keyword);
  const internalCommitRef = useRef(false);
  const previousKeywordRef = useRef(keyword);

  useEffect(() => {
    if (keyword === previousKeywordRef.current) return;
    previousKeywordRef.current = keyword;
    // 内部防抖提交后，keyword 变化是组件自己触发的，不要覆盖用户刚输入的本地值。
    if (internalCommitRef.current) {
      internalCommitRef.current = false;
      latestValueRef.current = keyword;
      return;
    }
    // 外部筛选重置（例如点击侧边栏标签）才同步输入框。
    latestValueRef.current = keyword;
    setInputValue(keyword);
  }, [keyword]);

  const scheduleKeywordCommit = (value: string) => {
    if (keywordDebounceRef.current) clearTimeout(keywordDebounceRef.current);
    keywordDebounceRef.current = setTimeout(() => {
      internalCommitRef.current = true;
      onKeyword(value);
      keywordDebounceRef.current = null;
    }, 280);
  };

  // 输入变化时触发列表过滤和搜索建议；列表查询与建议请求都做防抖。
  const handleSearch = (value: string) => {
    latestValueRef.current = value;
    setInputValue(value);
    scheduleKeywordCommit(value);
    if (debounceRef.current) clearTimeout(debounceRef.current);

    if (value.trim().length < 2) {
      requestIdRef.current += 1;
      setOptions([]);
      return;
    }

    const requestId = ++requestIdRef.current;
    debounceRef.current = setTimeout(async () => {
      try {
        const items: SuggestItem[] = await fetchSuggest(value);
        // 较早的响应不能覆盖用户最新输入对应的建议。
        if (
          requestId !== requestIdRef.current ||
          latestValueRef.current.trim() !== value.trim()
        ) {
          return;
        }
        setOptions(
          items.map((it) => ({
            value: it.id,
            label: (
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  padding: "2px 0",
                }}
              >
                <span
                  style={{
                    fontSize: 11,
                    color: "var(--pf-text-placeholder)",
                    fontFamily: "monospace",
                    flexShrink: 0,
                  }}
                >
                  {it.id}
                </span>
                {/* 安全渲染高亮：解析 <mark> 标签为 React 节点，杜绝 XSS */}
                <span style={{ color: "var(--pf-text-secondary)", fontSize: 13, lineHeight: 1.4 }}>
                  {renderHighlight(it.highlight || it.title)}
                </span>
              </div>
            ),
          })),
        );
      } catch {
        if (
          requestId === requestIdRef.current &&
          latestValueRef.current.trim() === value.trim()
        ) {
          setOptions([]);
        }
      }
    }, 300);
  };

  // 选中下拉项：跳转到详情页
  const handleSelect = (paperId: string) => {
    navigate(`/paper/${paperId}`);
  };

  // 组件卸载时清理定时器
  useEffect(() => {
    return () => {
      requestIdRef.current += 1;
      if (debounceRef.current) clearTimeout(debounceRef.current);
      if (keywordDebounceRef.current) clearTimeout(keywordDebounceRef.current);
    };
  }, []);

  return (
    <Space className="pf-search-bar" style={{ width: "100%" }} size={12}>
      <AutoComplete
        value={inputValue}
        options={options}
        onSearch={handleSearch}
        onSelect={handleSelect}
        style={{ width: 420 }}
        allowClear
        onChange={(v: string) => {
          latestValueRef.current = v;
          setInputValue(v);
          if (v.trim().length === 0) {
            if (keywordDebounceRef.current) clearTimeout(keywordDebounceRef.current);
            internalCommitRef.current = true;
            onKeyword("");
          } else {
            scheduleKeywordCommit(v);
          }
          if (v.trim().length < 2) {
            requestIdRef.current += 1;
            if (debounceRef.current) clearTimeout(debounceRef.current);
            setOptions([]);
          }
        }}
      >
        <Input
          size="large"
          allowClear
          prefix={<Search style={{ color: "var(--pf-text-placeholder)" }} />}
          placeholder={t("search.placeholder")}
          style={{ borderRadius: 8 }}
          // 回车立即提交，避免用户输入后还要等待防抖窗口。
          onPressEnter={() => {
            if (keywordDebounceRef.current) clearTimeout(keywordDebounceRef.current);
            internalCommitRef.current = true;
            onKeyword(inputValue);
          }}
        />
      </AutoComplete>
      <Select
        value={sort}
        onChange={onSort}
        size="large"
        style={{ width: 150 }}
        options={SORT_OPTIONS.map((o) => ({ label: o.label, value: o.value }))}
      />
      <Tooltip title={semantic ? t("search.semanticMode") : t("search.keywordMode")}>
        <Switch
          checkedChildren={t("search.semanticLabel")}
          unCheckedChildren={t("search.keywordLabel")}
          checked={semantic}
          onChange={onSemantic}
        />
      </Tooltip>
    </Space>
  );
}
