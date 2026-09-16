import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

/**
 * DepthReviewTab 的链路边界回归测试。
 *
 * 背景：感悟/读后报告（paper.category === 'report'）曾被打进 DEPTH v4.2 论文审稿
 * 链路，被 Q3/Q5a 的"无实验/无消融/无基线"缺陷判成 reject。后端已加边界守卫
 * （单篇/选中/批量端点 + run_depth_review_sync 入口全部拒绝 report），前端这里
 * 必须同步：报告不再请求论文审稿接口，而是引导到 /reflection/result/:paperId。
 *
 * 覆盖三条路径：
 * 1. category='report' → 不发起请求，直接展示引导提示 + 跳转按钮
 * 2. 普通论文 → 正常请求论文审稿接口（避免"一刀切"回归）
 * 3. category 缺失但后端 400（detail 含 reflection）→ 兜底展示同一引导
 */

const mockNavigate = vi.fn();
vi.mock("react-router-dom", () => ({
  useNavigate: () => mockNavigate,
}));

const getDepthV4Result = vi.fn();
const startDepthV4Review = vi.fn();
vi.mock("@/api/depth", () => ({
  getDepthV4Result: (...args: unknown[]) => getDepthV4Result(...args),
  startDepthV4Review: (...args: unknown[]) => startDepthV4Review(...args),
}));

// antd 6：组件经 App.useApp() 取 message 实例，无 <App> context 时返回空对象。
vi.mock("antd", async () => {
  const actual = await vi.importActual<typeof import("antd")>("antd");
  const message = { ...actual.message, success: vi.fn(), error: vi.fn() };
  return {
    ...actual,
    App: { ...actual.App, useApp: () => ({ message, notification: {}, modal: {} }) },
  };
});

import DepthReviewTab from "../DepthReviewTab";

/** 构造 axios 风格的 HTTP 错误。 */
function httpError(status: number, detail: string) {
  return Object.assign(new Error(detail), {
    response: { status, data: { detail } },
  });
}

beforeEach(() => {
  mockNavigate.mockReset();
  getDepthV4Result.mockReset();
  startDepthV4Review.mockReset();
});

describe("DepthReviewTab 链路边界", () => {
  it("category='report' 时不请求论文审稿接口，改为引导到感悟报告结果页", async () => {
    const user = userEvent.setup();
    render(<DepthReviewTab paperId="p-report" category="report" />);

    // 引导提示出现（i18n 在测试环境被 mock 成返回 key）
    expect(await screen.findByText("depth.reportPipelineTitle")).toBeInTheDocument();
    expect(screen.getByText("depth.reportPipelineDesc")).toBeInTheDocument();

    // 关键断言：完全没有走论文审稿链路
    expect(getDepthV4Result).not.toHaveBeenCalled();
    expect(startDepthV4Review).not.toHaveBeenCalled();

    // 跳转按钮指向 report 的正确结果页
    await user.click(screen.getByRole("button", { name: /depth\.gotoReflection/ }));
    expect(mockNavigate).toHaveBeenCalledWith("/reflection/result/p-report");
  });

  it("category 大小写/空白差异同样按报告处理", async () => {
    render(<DepthReviewTab paperId="p-report" category="  Report  " />);
    expect(await screen.findByText("depth.reportPipelineTitle")).toBeInTheDocument();
    expect(getDepthV4Result).not.toHaveBeenCalled();
  });

  it("普通论文照常请求论文审稿接口（无评审时给出启动入口）", async () => {
    getDepthV4Result.mockRejectedValue(httpError(404, "not found"));
    render(<DepthReviewTab paperId="p-paper" category="arxiv" />);

    expect(await screen.findByText("depth.noReview")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /depth\.startReview/ })).toBeInTheDocument();
    expect(getDepthV4Result).toHaveBeenCalledTimes(1);
    expect(getDepthV4Result.mock.calls[0][0]).toBe("p-paper");
  });

  it("category 缺失但后端 400（report 边界守卫）时兜底展示引导提示", async () => {
    getDepthV4Result.mockRejectedValue(
      httpError(
        400,
        "p-report 是感悟/读后报告（category='report'），请改用 reflection 链路：POST /api/depth/reflection/run/p-report",
      ),
    );
    render(<DepthReviewTab paperId="p-report" />);

    expect(await screen.findByText("depth.reportPipelineTitle")).toBeInTheDocument();
    // 不应把后端 detail 当普通错误直接铺给用户
    await waitFor(() => expect(screen.queryByText("depth.errorTitle")).not.toBeInTheDocument());
  });

  it("普通 400（非报告语义）仍按错误提示展示，不被误判为报告", async () => {
    getDepthV4Result.mockRejectedValue(httpError(400, "论文正文为空，无法审稿"));
    render(<DepthReviewTab paperId="p-paper" category="arxiv" />);

    expect(await screen.findByText("论文正文为空，无法审稿")).toBeInTheDocument();
    expect(screen.queryByText("depth.reportPipelineTitle")).not.toBeInTheDocument();
  });
});
