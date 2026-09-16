import { useState } from "react";
import { Layout } from "antd";
import { Outlet } from "react-router-dom";
import TopNav from "@/components/TopNav";
import SideBar from "@/components/SideBar";

const { Content } = Layout;

export default function MainLayout() {
  const [mobileNavOpen, setMobileNavOpen] = useState(false);

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
    </Layout>
  );
}
