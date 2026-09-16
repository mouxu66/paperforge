import { useEffect, useState } from "react";
import { Layout } from "antd";
import { Outlet, useLocation } from "react-router-dom";
import TopNav from "@/components/TopNav";
import SideBar from "@/components/SideBar";
import { OnboardingTour } from "@/components/onboarding";
import { ONBOARDING_AUTO_START_PATH } from "@/components/onboarding/steps";
import { useOnboardingStore } from "@/store/useOnboardingStore";

const { Content } = Layout;

/** 首屏锚点（搜索栏/统计卡/上传区）渲染完成后再弹引导，避免开局先落到降级态 */
const AUTO_START_DELAY_MS = 600;

export default function MainLayout() {
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const { pathname } = useLocation();

  const completed = useOnboardingStore((s) => s.completed);
  const running = useOnboardingStore((s) => s.running);
  const resume = useOnboardingStore((s) => s.resume);
  const stop = useOnboardingStore((s) => s.stop);

  // 新手引导：只在首页自动启动，且仅当当前引导版本尚未完成。
  // 中途导航离开首页时收起引导但**不写完成标记**，回到首页继续——否则用户
  // 随手点了个菜单就等于「看过了」，再也见不到引导。
  useEffect(() => {
    if (completed) return;
    if (pathname === ONBOARDING_AUTO_START_PATH) {
      const timer = window.setTimeout(() => resume(), AUTO_START_DELAY_MS);
      return () => window.clearTimeout(timer);
    }
    if (running) stop();
  }, [completed, running, pathname, resume, stop]);

  return (
    <Layout style={{ minHeight: "100vh" }}>
      <TopNav onToggleNav={() => setMobileNavOpen(true)} />
      <div className="pf-app-body">
        <SideBar mobileOpen={mobileNavOpen} onClose={() => setMobileNavOpen(false)} />
        <Content className="pf-content">
          <div className="pf-page-shell pf-route-view">
            {/* 此前这里挂着一张静态 banner 图（硬编码「论文库」），
                导致每个路由顶部都印同一张图并占掉约 166px 首屏。
                页面标题改由各页自己的页头承担。 */}
            <Outlet />
          </div>
        </Content>
      </div>
      <OnboardingTour />
    </Layout>
  );
}
