import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { MemoryRouter } from "@/test-utils";
import { message } from "antd";
import DuplicatePapersPage from "../DuplicatePapersPage";
import * as papersApi from "@/api/papers";
import type { DuplicateGroup } from "@/api/types";

vi.mock("@/api/papers", () => ({
  fetchDuplicateGroups: vi.fn(),
  mergePapers: vi.fn(),
}));

let lastModalConfig: any = null;

vi.mock("antd", () => {
  const mockButton = ({ children, onClick, loading: _loading, ...props }: any) => (
    <button onClick={onClick} {...props}>
      {children}
    </button>
  );
  return {
    Alert: ({ message, description }: any) => (
      <div role="alert">
        {message}
        {description}
      </div>
    ),
    Breadcrumb: ({ children }: any) => <nav>{children}</nav>,
    Button: mockButton,
    Card: ({ title, children, extra }: any) => (
      <div>
        <div>{title}</div>
        <div>{extra}</div>
        <div>{children}</div>
      </div>
    ),
    Checkbox: ({ checked, onChange }: any) => (
      <input type="checkbox" checked={checked} onChange={onChange} />
    ),
    Empty: ({ description }: any) => <div>{description}</div>,
    Radio: ({ children, checked, onChange }: any) => (
      <label>
        <input type="radio" checked={checked} onChange={onChange} />
        {children}
      </label>
    ),
    Space: ({ children }: any) => <div>{children}</div>,
    Spin: () => <div className="ant-spin" />,
    Table: (props: any) => (
      <div data-testid="mock-table">
        {props.dataSource?.map((row: any) => (
          <div key={row.key} data-row={row.key}>
            {props.columns?.map((col: any) => (
              <span key={col.dataIndex ?? col.key}>{row[col.dataIndex]}</span>
            ))}
          </div>
        ))}
      </div>
    ),
    Tag: ({ children }: any) => <span>{children}</span>,
    Typography: {
      Title: ({ children }: any) => <h3>{children}</h3>,
      Text: ({ children }: any) => <span>{children}</span>,
    },
    Modal: {
      confirm: vi.fn((config) => {
        lastModalConfig = config;
      }),
    },
    message: {
      success: vi.fn(),
      error: vi.fn(),
      warning: vi.fn(),
    },
  };
});

function renderPage() {
  return render(
    <MemoryRouter>
      <DuplicatePapersPage />
    </MemoryRouter>,
  );
}

function makeGroup(): DuplicateGroup {
  return {
    papers: [
      {
        id: "p1",
        title: "Paper One",
        authors: ["Alice"],
        year: 2023,
        abstract: "Abstract one",
        journal: "Journal A",
        pdfUrl: "",
        source: "arxiv",
        citations: 10,
        tags: ["ai"],
      },
      {
        id: "p2",
        title: "Paper Two",
        authors: ["Bob"],
        year: 2024,
        abstract: "Abstract two",
        journal: "Journal B",
        pdfUrl: "",
        source: "arxiv",
        citations: 5,
        tags: ["ml"],
      },
    ],
  };
}

describe("DuplicatePapersPage", () => {
  beforeEach(() => {
    lastModalConfig = null;
    vi.restoreAllMocks();
    vi.spyOn(papersApi, "fetchDuplicateGroups").mockResolvedValue({ groups: [makeGroup()] });
    vi.spyOn(papersApi, "mergePapers").mockResolvedValue({
      success: true,
      target_id: "p1",
      deleted_ids: ["p2"],
      paper: null,
    });
  });

  it("renders loading state initially", async () => {
    renderPage();
    expect(document.querySelector(".ant-spin")).toBeInTheDocument();
    expect(await screen.findByText("Paper One")).toBeInTheDocument();
  });

  it("renders duplicate groups after loading", async () => {
    renderPage();
    expect(await screen.findByText("Paper One")).toBeInTheDocument();
    expect(screen.getByText("Paper Two")).toBeInTheDocument();
  });

  it("renders empty state when no duplicates", async () => {
    vi.spyOn(papersApi, "fetchDuplicateGroups").mockResolvedValue({ groups: [] });
    renderPage();
    expect(await screen.findByText("duplicates.noDuplicates")).toBeInTheDocument();
  });

  it("calls mergePapers with correct payload on confirm", async () => {
    renderPage();
    expect(await screen.findByText("Paper One")).toBeInTheDocument();

    fireEvent.click(screen.getByText("duplicates.merge"));

    await waitFor(() => expect(lastModalConfig).toBeDefined());
    expect(lastModalConfig.title).toBe("duplicates.confirmMergeTitle");

    await act(async () => {
      await lastModalConfig.onOk();
    });
    expect(await screen.findByText("Paper One")).toBeInTheDocument();

    expect(papersApi.mergePapers).toHaveBeenCalledWith({
      target_id: "p1",
      source_ids: ["p2"],
      field_sources: {
        title: "p1",
        authors: "p1",
        year: "p1",
        abstract: "p1",
        journal: "p1",
        pdf_url: "p1",
        tags: "p1",
      },
    });
  });

  it("shows error message when fetchDuplicateGroups fails", async () => {
    vi.spyOn(papersApi, "fetchDuplicateGroups").mockRejectedValue(new Error("Network error"));
    renderPage();
    expect(await screen.findByText("duplicates.title")).toBeInTheDocument();
    expect(message.error).toHaveBeenCalled();
  });

  it("shows error message when mergePapers fails", async () => {
    vi.spyOn(papersApi, "mergePapers").mockRejectedValue(new Error("Merge failed"));
    renderPage();
    expect(await screen.findByText("Paper One")).toBeInTheDocument();

    fireEvent.click(screen.getByText("duplicates.merge"));
    await waitFor(() => expect(lastModalConfig).toBeDefined());

    await act(async () => {
      await lastModalConfig.onOk();
    });
    expect(await screen.findByText("Paper One")).toBeInTheDocument();
    expect(message.error).toHaveBeenCalled();
  });

  it("refreshes groups after successful merge", async () => {
    renderPage();
    expect(await screen.findByText("Paper One")).toBeInTheDocument();

    fireEvent.click(screen.getByText("duplicates.merge"));
    await waitFor(() => expect(lastModalConfig).toBeDefined());

    await act(async () => {
      await lastModalConfig.onOk();
    });
    expect(await screen.findByText("Paper One")).toBeInTheDocument();
    expect(papersApi.fetchDuplicateGroups).toHaveBeenCalledTimes(2);
  });

  it("allows changing target selection and uses it in merge payload", async () => {
    renderPage();
    expect(await screen.findByText("Paper One")).toBeInTheDocument();

    const radios = document.querySelectorAll('input[type="radio"]');
    expect(radios.length).toBeGreaterThanOrEqual(2);

    // Click the second radio to change target to p2
    fireEvent.click(radios[1]);

    fireEvent.click(screen.getByText("duplicates.merge"));
    await waitFor(() => expect(lastModalConfig).toBeDefined());

    await act(async () => {
      await lastModalConfig.onOk();
    });
    expect(await screen.findByText("Paper One")).toBeInTheDocument();
    expect(papersApi.mergePapers).toHaveBeenCalledWith(
      expect.objectContaining({
        target_id: "p2",
        source_ids: ["p1"],
      }),
    );
  });

  it("allows changing field source selection and reflects in merge payload", async () => {
    renderPage();
    expect(await screen.findByText("Paper One")).toBeInTheDocument();

    const checkboxes = document.querySelectorAll('input[type="checkbox"]');
    expect(checkboxes.length).toBeGreaterThanOrEqual(2);

    // Toggle the second checkbox (title, p2) to change field source to p2
    fireEvent.click(checkboxes[1]);

    fireEvent.click(screen.getByText("duplicates.merge"));
    await waitFor(() => expect(lastModalConfig).toBeDefined());

    await act(async () => {
      await lastModalConfig.onOk();
    });
    expect(await screen.findByText("Paper One")).toBeInTheDocument();
    const call = (papersApi.mergePapers as any).mock.calls[0][0];
    expect(call.field_sources.title).toBe("p2");
  });
});
