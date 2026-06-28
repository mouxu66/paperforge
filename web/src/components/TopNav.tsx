import { Layout, Menu, Badge, Tooltip, Button } from 'antd'
import { HomeOutlined, StarOutlined, GithubOutlined, BookOutlined, QuestionCircleOutlined, EditOutlined, ThunderboltOutlined, GlobalOutlined, SettingOutlined } from '@ant-design/icons'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useFavoriteStore } from '@/store/useFavoriteStore'
import { changeLanguage, type SupportedLang } from '@/i18n'
import ModelSelector from './ModelSelector'
import DevTools from './DevTools'

const { Header } = Layout

// 路由前缀 → 菜单 key 映射（替代嵌套三元）
const ROUTE_MAP = [
  { prefix: '/favorites', key: '/favorites' },
  { prefix: '/write', key: '/write' },
  { prefix: '/ask', key: '/ask' },
  { prefix: '/generate', key: '/generate' },
  { prefix: '/models', key: '/models' },
  { prefix: '/settings', key: '/settings' },
  { prefix: '/paper', key: '/' },
] as const

export default function TopNav() {
  const location = useLocation()
  const navigate = useNavigate()
  const favCount = useFavoriteStore((s) => s.items.length)
  const { t, i18n } = useTranslation()

  const matched = ROUTE_MAP.find((r) => location.pathname.startsWith(r.prefix))
  const selectedKey = matched?.key ?? location.pathname

  const currentLang = (i18n.language?.startsWith('en') ? 'en' : 'zh') as SupportedLang

  const toggleLanguage = () => {
    changeLanguage(currentLang === 'zh' ? 'en' : 'zh')
  }

  return (
    <Header
      style={{
        display: 'flex',
        alignItems: 'center',
        borderBottom: '1px solid #e8ecf1',
        boxShadow: '0 1px 2px rgba(15,23,42,0.04)',
        position: 'sticky',
        top: 0,
        zIndex: 20,
      }}
    >
      <div
        style={{ cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 10, marginRight: 32 }}
        onClick={() => navigate('/')}
      >
        <div
          style={{
            width: 34,
            height: 34,
            borderRadius: 8,
            background: '#1e40af',
            color: '#fff',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontFamily: 'Georgia, serif',
            fontWeight: 700,
            fontSize: 20,
          }}
        >
          P
        </div>
        <div style={{ lineHeight: 1.15 }}>
          <div
            className="pf-serif"
            style={{
              fontSize: 18,
              fontWeight: 600,
              color: '#1a1a2e',
              letterSpacing: '0.2px',
            }}
          >
            {t('app.title')}
          </div>
          <div style={{ fontSize: 11, color: '#94a3b8' }}>{t('app.subtitle')}</div>
        </div>
      </div>

      <Menu
        mode="horizontal"
        selectedKeys={[selectedKey]}
        style={{ flex: 1, borderBottom: 'none', background: 'transparent' }}
        items={[
          { key: '/', icon: <HomeOutlined />, label: <Link to="/">{t('nav.home')}</Link> },
          { key: '/ask', icon: <QuestionCircleOutlined />, label: <Link to="/ask">{t('nav.ask')}</Link> },
          { key: '/generate', icon: <EditOutlined />, label: <Link to="/generate">{t('nav.generate')}</Link> },
          { key: '/write', icon: <EditOutlined />, label: <Link to="/write">{t('nav.write')}</Link> },
          { key: '/models', icon: <ThunderboltOutlined />, label: <Link to="/models">{t('nav.models')}</Link> },
          { key: '/settings', icon: <SettingOutlined />, label: <Link to="/settings">{t('nav.settings')}</Link> },
          {
            key: '/favorites',
            icon: (
              <Badge count={favCount} size="small" offset={[6, -2]}>
                <StarOutlined />
              </Badge>
            ),
            label: <Link to="/favorites">{t('nav.favorites')}</Link>,
          },
        ]}
      />

      <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
        <ModelSelector />
        <DevTools />
        {/* 语言切换按钮（子任务 5） */}
        <Tooltip title={t('nav.language')}>
          <Button
            type="text"
            size="small"
            icon={<GlobalOutlined />}
            onClick={toggleLanguage}
            style={{ color: '#64748b', fontWeight: 500 }}
          >
            {currentLang === 'zh' ? '中' : 'EN'}
          </Button>
        </Tooltip>
        <Tooltip title={t('nav.localRag')}>
          <BookOutlined style={{ fontSize: 16, color: '#64748b', cursor: 'pointer' }} />
        </Tooltip>
        <Tooltip title={t('nav.repo')}>
          <GithubOutlined style={{ fontSize: 16, color: '#64748b', cursor: 'pointer' }} />
        </Tooltip>
        <span
          style={{
            fontSize: 12,
            color: '#94a3b8',
            borderLeft: '1px solid #e5e7eb',
            paddingLeft: 16,
          }}
        >
          {t('app.version')}
        </span>
      </div>
    </Header>
  )
}
