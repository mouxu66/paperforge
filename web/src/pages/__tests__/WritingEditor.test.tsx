import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import WritingEditor from "../WritingEditor";
import { fetchChapterTree, fetchWritingProject } from "@/api/writing";

vi.mock("@/api/writing", async () => {
  const actual = await vi.importActual<typeof import("@/api/writing")>("@/api/writing");
  return {
    ...actual,
    fetchChapterTree: vi.fn(),
    fetchWritingProject: vi.fn(),
  };
});

const mockNavigate = vi.hoisted(() => vi.fn());
const mockedFetchChapterTree = vi.mocked(fetchChapterTree);
const mockedFetchWritingProject = vi.mocked(fetchWritingProject);

vi.mock("react-router-dom", () => ({
  useNavigate: () => mockNavigate,
  useParams: () => ({ projectId: "not-a-number" }),
}));

describe("WritingEditor — invalid project route", () => {
  it("renders the not-found state without requesting project data", async () => {
    render(<WritingEditor />);

    expect(await screen.findByText("editor.notFoundTitle")).toBeInTheDocument();
    expect(screen.getByText("editor.notFoundSubtitle")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "editor.backToWrite" })).toBeInTheDocument();
    expect(mockedFetchWritingProject).not.toHaveBeenCalled();
    expect(mockedFetchChapterTree).not.toHaveBeenCalled();
  });

  it("returns to the writing dashboard from the not-found state", async () => {
    render(<WritingEditor />);

    const backButton = await screen.findByRole("button", { name: "editor.backToWrite" });
    backButton.click();

    await waitFor(() => expect(mockNavigate).toHaveBeenCalledWith("/write"));
  });
});
