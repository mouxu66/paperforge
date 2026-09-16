import { describe, it, expect, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { Routes, Route } from "react-router-dom";
import { MemoryRouter } from "@/test-utils";
import HelpCenterPage from "../HelpCenterPage";
import { useOnboardingStore } from "@/store/useOnboardingStore";
import zhLocale from "@/locales/zh.json";

function renderHelp() {
  return render(
    <MemoryRouter initialEntries={["/help"]}>
      <Routes>
        <Route path="/help" element={<HelpCenterPage />} />
        <Route path="/" element={<div>HOME_PAGE</div>} />
        <Route path="/ask" element={<div>ASK_PAGE</div>} />
        <Route path="/write" element={<div>WRITE_PAGE</div>} />
        <Route path="/depth" element={<div>DEPTH_PAGE</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("HelpCenterPage", () => {
  beforeEach(() => {
    useOnboardingStore.setState({ running: false, stepIndex: 0, completed: false });
  });

  it("渲染页头与五个核心功能章节", () => {
    renderHelp();
    expect(screen.getByText("help.title")).toBeInTheDocument();
    for (const key of [
      "help.import.title",
      "help.search.title",
      "help.ask.title",
      "help.write.title",
      "help.depth.title",
    ]) {
      // 目录项 + 章节标题各一处
      expect(screen.getAllByText(key)).toHaveLength(2);
    }
  });

  it("每个章节都给出「你会看到」与「常见误区」", () => {
    renderHelp();
    // 5 个章节 + 1 个「还想了解更多」都不带误区；断言 5 组观察点
    expect(screen.getAllByText("help.seeLabel")).toHaveLength(5);
    expect(screen.getAllByText("help.pitfallLabel")).toHaveLength(5);
  });

  it("渲染 3 分钟上手与目录", () => {
    renderHelp();
    expect(screen.getByText("help.quickstart.title")).toBeInTheDocument();
    expect(screen.getByText("help.toc")).toBeInTheDocument();
    expect(screen.getByText("help.quickstart.s1.title")).toBeInTheDocument();
    expect(screen.getByText("help.quickstart.s3.title")).toBeInTheDocument();
  });

  it("「重看新手引导」回到首页并启动引导（锚点都在首页）", () => {
    renderHelp();
    fireEvent.click(screen.getByText("help.replayOnboarding"));

    expect(screen.getByText("HOME_PAGE")).toBeInTheDocument();
    expect(useOnboardingStore.getState().running).toBe(true);
    expect(useOnboardingStore.getState().stepIndex).toBe(0);
  });

  it("章节 CTA 跳转到对应功能页", () => {
    renderHelp();
    fireEvent.click(screen.getByText("help.ask.cta"));
    expect(screen.getByText("ASK_PAGE")).toBeInTheDocument();
  });

  it("图检索说明写明「匹配图中文字、非图意理解」这一限制", () => {
    // mock 的 t 只返回 key，因此这里直接核对真实文案：能力不能说过头
    const desc = (zhLocale as { help: { search: { s3: { desc: string } } } }).help.search.s3.desc;
    expect(desc).toContain("OCR");
    expect(desc).toContain("不是对图内容的语义理解");
  });
});
