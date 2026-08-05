import { useState } from "react";
import { Layout } from "antd";
import { Outlet } from "react-router-dom";
import TopNav from "@/components/TopNav";
import SideBar from "@/components/SideBar";
import PageBanner from "@/components/PageBanner";

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
            <PageBanner />
            <Outlet />
          </div>
        </Content>
      </div>
    </Layout>
  );
}
