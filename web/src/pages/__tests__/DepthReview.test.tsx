import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { buildDepthReviewV4Result } from "@/fixtures/depthReview";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------
const mockNavigate = vi.fn();

vi.mock("react-router-dom", () => ({
  useParams: () => ({ paperId: "p1" }),
  useNavigate: () => mockNavigate,
  useSearchParams: () => [new URLSearchParams(), vi.fn()],
}));

vi.mock("@/api/depth", () => ({
  getDepthV4Result: vi.fn(),
  listDepthV4Papers: vi.fn().mockResolvedValue({ items: [], total: 0 }),
  startDepthV4Review: vi.fn(),
  deleteDepthReview: vi.fn(),
  batchDeleteDepthReviews: vi.fn(),
  startSelectedV4Review: vi.fn(),
}));

import { getDepthV4Result } from "@/api/depth";

vi.mock("@/hooks/useQwenStatus", () => ({
  useQwenStatus: () => ({
    status: null,
    showHint: false,
    start: vi.fn(),
    stop: vi.fn(),
  }),
}));

vi.mock("@/components/FigureConsistencyCard", () => ({
  default: ({ activeEvidenceIds }: any) => (
    <div
      data-testid="figure-consistency-card"
      data-active-ids={JSON.stringify(activeEvidenceIds || [])}
    />
  ),
}));

vi.mock("@/components/ScoreBar", () => ({
  default: ({ label }: { label: string }) => <div data-testid={`score-bar-${label}`} />,
}));

vi.mock("antd", async () => {
  const actual = await vi.importActual<typeof import("antd")>("antd");
  return {
    ...actual,
    Select: ({ value, onChange, options, "data-testid": testId }: any) => (
      <select
        value={value}
        onChange={(e) => onChange?.(e.target.value)}
        data-testid={testId}
      >
        {options.map((o: any) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    ),
  };
});

vi.mock("@/components/FigureDetailsList", () => ({
  default: ({ paperId }: { paperId: string }) => (
    <div data-testid="figure-details-list" data-paper-id={paperId} />
  ),
}));

// 路由入口组件必须在 mock 之后导入
import DepthReviewPage from "../DepthReview";


// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------
describe("DepthReviewPage — evidence pool severity column", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getDepthV4Result.mockResolvedValue(
      buildDepthReviewV4Result({
        evidence_pool: [
          {
            id: "E1",
            content: "fatal claim",
            section: "Figures",
            keywords: [],
            severity: "fatal",
          },
          {
            id: "E2",
            content: "minor claim",
            section: "Figures",
            keywords: [],
            severity: "minor",
          },
          {
            id: "E3",
            content: "unknown claim",
            section: "Figures",
            keywords: [],
            // severity 未定义
          },
        ],
        q5a_result: {
          critique_points: [
            {
              point: "The claim in [E1] is suspicious and needs verification.",
              severity: "fatal",
            },
          ],
        },
      }),
    );
  });

  it("renders fatal/minor/undefined severity labels", async () => {
    render(<DepthReviewPage />);

    await waitFor(() => expect(getDepthV4Result).toHaveBeenCalledWith("p1"));

    // 展开证据池 Collapse（antd 把 header 文本拆成多个元素，按 role 查找更稳）
    const collapseHeader = await screen.findByRole("button", { name: /证据池/ });
    fireEvent.click(collapseHeader);

    // antd Table 异步渲染行，等待内容出现
    const table = await screen.findByTestId("evidence-table");
    await within(table).findByText("fatal claim");
    await within(table).findByText("minor claim");
    await within(table).findByText("unknown claim");

    // fatal -> "致命"
    const fatalTag = within(table).getByTestId("severity-tag-E1");
    expect(fatalTag).toHaveTextContent("致命");

    // minor -> "轻微"
    const minorTag = within(table).getByTestId("severity-tag-E2");
    expect(minorTag).toHaveTextContent("轻微");

    // undefined -> "未知"
    const unknownTag = within(table).getByTestId("severity-tag-E3");
    expect(unknownTag).toHaveTextContent("未知");
  });

  it("filters evidence pool by severity", async () => {
    const user = userEvent.setup();
    render(<DepthReviewPage />);

    const collapseHeader = await screen.findByRole("button", { name: /证据池/ });
    fireEvent.click(collapseHeader);

    const table = await screen.findByTestId("evidence-table");
    await within(table).findByText("fatal claim");
    await within(table).findByText("minor claim");

    const filterSelect = screen.getByTestId("evidence-filter");
    await user.selectOptions(filterSelect, "fatal");

    await waitFor(() => {
      // 状态更新后 Table 可能重新渲染，每次从 DOM 重新获取最稳
      const currentTable = screen.getByTestId("evidence-table");
      expect(within(currentTable).queryByText("fatal claim")).toBeInTheDocument();
      expect(within(currentTable).queryByText("minor claim")).not.toBeInTheDocument();
      expect(within(currentTable).queryByText("unknown claim")).not.toBeInTheDocument();
    });
  });

  it("sorts evidence pool by severity with fatal on top", async () => {
    const user = userEvent.setup();
    getDepthV4Result.mockResolvedValue(
      buildDepthReviewV4Result({
        evidence_pool: [
          {
            id: "E2",
            content: "minor claim first",
            section: "Figures",
            keywords: [],
            severity: "minor",
          },
          {
            id: "E3",
            content: "unknown claim second",
            section: "Figures",
            keywords: [],
            severity: undefined,
          },
          {
            id: "E1",
            content: "fatal claim last",
            section: "Figures",
            keywords: [],
            severity: "fatal",
          },
        ],
      }),
    );

    render(<DepthReviewPage />);

    const collapseHeader = await screen.findByRole("button", { name: /证据池/ });
    fireEvent.click(collapseHeader);

    const table = await screen.findByTestId("evidence-table");
    await within(table).findByText("minor claim first");

    const sortSelect = screen.getByTestId("evidence-sort");
    await user.selectOptions(sortSelect, "severity");

    // 等待 fatal 行置顶（在表格范围内断言，避免命中其它 row）
    await waitFor(() => {
      expect(table).toHaveTextContent("fatal claim last");
      expect(table).toHaveTextContent("minor claim first");
      // fatal 出现在 minor 之前
      expect(table.textContent.indexOf("fatal claim last")).toBeLessThan(
        table.textContent.indexOf("minor claim first"),
      );
    });
  });

  it("highlights related evidence row and FigureConsistencyCard when clicking severity tag", async () => {
    render(<DepthReviewPage />);

    await waitFor(() => expect(getDepthV4Result).toHaveBeenCalledWith("p1"));

    const collapseHeader = await screen.findByRole("button", { name: /证据池/ });
    fireEvent.click(collapseHeader);

    const table = await screen.findByTestId("evidence-table");
    const fatalTag = within(table).getByTestId("severity-tag-E1");
    fireEvent.click(fatalTag);

    const row = within(table).getByText("fatal claim").closest("tr");
    expect(row).toHaveAttribute("data-active", "true");

    const card = screen.getByTestId("figure-consistency-card");
    expect(card).toHaveAttribute("data-active-ids", JSON.stringify(["E1"]));
  });

  it("highlights related evidence row when clicking Q5a critique point", async () => {
    render(<DepthReviewPage />);

    await waitFor(() => expect(getDepthV4Result).toHaveBeenCalledWith("p1"));

    const q5aCard = screen.getByText(
      "The claim in [E1] is suspicious and needs verification.",
    ).closest(".ant-card") as HTMLElement;
    fireEvent.click(q5aCard);

    const table = await screen.findByTestId("evidence-table");
    const row = within(table).getByText("fatal claim").closest("tr");
    expect(row).toHaveAttribute("data-active", "true");
  });

  it("shows placeholder when evidence pool is empty", async () => {
    getDepthV4Result.mockResolvedValue(buildDepthReviewV4Result({ evidence_pool: [] }));
    render(<DepthReviewPage />);

    await waitFor(() => expect(getDepthV4Result).toHaveBeenCalledWith("p1"));

    const collapseHeader = await screen.findByRole("button", { name: /证据池/ });
    fireEvent.click(collapseHeader);

    const table = await screen.findByTestId("evidence-table");
    expect(within(table).getByText("暂无证据")).toBeInTheDocument();
  });

  it("falls back to '未知' for unexpected severity strings", async () => {
    getDepthV4Result.mockResolvedValue(
      buildDepthReviewV4Result({
        evidence_pool: [
          {
            id: "E1",
            content: "unexpected severity",
            section: "Figures",
            keywords: [],
            severity: "foo" as any,
          },
        ],
      }),
    );
    render(<DepthReviewPage />);

    await waitFor(() => expect(getDepthV4Result).toHaveBeenCalledWith("p1"));

    const collapseHeader = await screen.findByRole("button", { name: /证据池/ });
    fireEvent.click(collapseHeader);

    const table = await screen.findByTestId("evidence-table");
    const tag = within(table).getByTestId("severity-tag-E1");
    expect(tag).toHaveTextContent("未知");
  });
});
