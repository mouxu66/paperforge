import { Layout } from 'antd'
import { Outlet } from 'react-router-dom'
import TopNav from '@/components/TopNav'
import SideBar from '@/components/SideBar'

const { Content } = Layout

export default function MainLayout() {
  return (
    <Layout style={{ minHeight: '100vh' }}>
      <TopNav />
      <Layout>
        <SideBar />
        <Content style={{ padding: 24, overflow: 'auto' }}>
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  )
}
